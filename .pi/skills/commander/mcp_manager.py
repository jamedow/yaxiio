#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
MCP Server 动态注册管理器 — Commander 扩展系统 2/3
====================================================
让 Commander 能自主连接、断开、管理 MCP Server。
基于 pi-mcp-adapter + Redis pub/sub 通知机制。

Constitution R1: 使用 mcp:* 前缀，不碰 page:*/lightingmetal:*
Constitution R4: 所有通知消息使用标准 JSON 格式

集成点：
  - CommanderV2.handle_task() → ExtensionRouter → MCPManager.register_mcp_server()
  - 定期健康检查 → MCPManager.test_connection()
  - Pi MCP Adapter 监听 mcp:config_changed 频道 → 热重载配置

v1.0 | 2026-05-24 | 初始版本
"""

import asyncio
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import redis as redis_lib


class MCPManager:
    """MCP Server 生命周期管理器。

    管理 MCP Server 的注册/注销/启用/禁用/健康检查。
    配置持久化到 mcp.json + Redis 注册表，通过 Redis Pub/Sub
    通知 Pi MCP Adapter 热重载。
    """

    # Redis 前缀
    KEY_REGISTRY = "mcp:registry"           # Hash: server_name → metadata JSON
    KEY_STATUS = "mcp:status"               # Hash: server_name → enabled/disabled
    KEY_TOOLS = "mcp:tools:{server}"        # String: JSON list of tool names
    KEY_HEALTH = "mcp:health:{server}"      # String: last health check result JSON
    CHANNEL_CONFIG = "mcp:config_changed"   # Pub/Sub 频道：通知 Adapter 热重载

    def __init__(
        self,
        redis_client: redis_lib.Redis,
        mongo_client=None,
        mcp_config_path: str = ".pi/agent/mcp.json",
        **kwargs,
    ):
        self.redis = redis_client
        self.mongo = mongo_client
        self.mcp_config_path = Path(mcp_config_path)

    # ── 配置 CRUD ─────────────────────────────────────────────

    def _load_config(self) -> Dict:
        """加载当前 MCP 配置文件。"""
        try:
            if self.mcp_config_path.exists():
                with open(self.mcp_config_path, "r") as f:
                    return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return {"mcpServers": {}}

    def _save_config(self, config: Dict):
        """持久化 MCP 配置到文件。"""
        self.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.mcp_config_path, "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    # ── 注册 / 注销 ──────────────────────────────────────────

    async def register_mcp_server(
        self,
        server_name: str,
        command: str = "npx",
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        enabled: bool = True,
    ) -> Dict:
        """注册一个新的 MCP Server。

        Args:
            server_name: Server 名称（如 'firecrawl', 'github', 'mongodb'）
            command: 启动命令（如 'npx', 'node', 'python'）
            args: 命令参数列表
            env: 环境变量字典
            enabled: 是否立即启用

        Returns:
            {server_name, action, timestamp, status, [error]}
        """
        if args is None:
            args = []

        result = {
            "server_name": server_name,
            "action": "register",
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
        }

        try:
            # 1. 更新 mcp.json
            config = self._load_config()
            server_config = {
                "command": command,
                "args": args,
                "enabled": enabled,
            }
            if env:
                server_config["env"] = env

            config["mcpServers"][server_name] = server_config
            self._save_config(config)

            # 2. 更新 Redis 注册表
            register_meta = {
                "command": command,
                "args": args,
                "registered_at": datetime.now().isoformat(),
                "enabled": enabled,
            }
            if env:
                register_meta["env"] = env

            self.redis.hset(
                self.KEY_REGISTRY,
                server_name,
                json.dumps(register_meta, ensure_ascii=False),
            )
            self.redis.hset(
                self.KEY_STATUS,
                server_name,
                "enabled" if enabled else "disabled",
            )

            # 3. 如果启用且是 npx 命令，预拉取包（非阻塞）
            if enabled and command == "npx" and args:
                result["prefetch"] = await self._prefetch_package(command, args)

            # 4. 通知 Pi MCP Adapter 热重载
            self._notify_adapter("register", server_name)

            result["status"] = "success"

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        self._persist_log(result)
        return result

    async def unregister_mcp_server(self, server_name: str) -> Dict:
        """注销一个 MCP Server。

        从 mcp.json 移除配置 + 清理 Redis 注册表 + 通知 Adapter。
        """
        result = {
            "server_name": server_name,
            "action": "unregister",
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
        }

        try:
            # 1. 更新配置文件
            config = self._load_config()
            if server_name in config.get("mcpServers", {}):
                del config["mcpServers"][server_name]
                self._save_config(config)

            # 2. 清理 Redis
            self.redis.hdel(self.KEY_REGISTRY, server_name)
            self.redis.hdel(self.KEY_STATUS, server_name)
            self.redis.delete(self.KEY_TOOLS.format(server=server_name))
            self.redis.delete(self.KEY_HEALTH.format(server=server_name))

            # 3. 通知 Adapter
            self._notify_adapter("unregister", server_name)

            result["status"] = "success"

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        self._persist_log(result)
        return result

    # ── 启用 / 禁用 ──────────────────────────────────────────

    async def enable_mcp_server(self, server_name: str) -> Dict:
        """启用一个已注册的 MCP Server。"""
        config = self._load_config()
        if server_name in config.get("mcpServers", {}):
            config["mcpServers"][server_name]["enabled"] = True
            self._save_config(config)

        self.redis.hset(self.KEY_STATUS, server_name, "enabled")
        self._notify_adapter("enable", server_name)

        return {"server_name": server_name, "status": "enabled"}

    async def disable_mcp_server(self, server_name: str) -> Dict:
        """禁用一个 MCP Server（不注销，仅标记 disabled）。"""
        config = self._load_config()
        if server_name in config.get("mcpServers", {}):
            config["mcpServers"][server_name]["enabled"] = False
            self._save_config(config)

        self.redis.hset(self.KEY_STATUS, server_name, "disabled")
        self._notify_adapter("disable", server_name)

        return {"server_name": server_name, "status": "disabled"}

    # ── 查询 ──────────────────────────────────────────────────

    def get_registered_servers(self) -> List[Dict]:
        """获取所有已注册的 MCP Server 及元数据。"""
        all_servers = self.redis.hgetall(self.KEY_REGISTRY)
        result = []
        for k, v in all_servers.items():
            name = k.decode() if isinstance(k, bytes) else k
            meta = json.loads(v.decode() if isinstance(v, bytes) else v)
            # 附加当前状态
            status = self.redis.hget(self.KEY_STATUS, name)
            meta["current_status"] = (
                status.decode() if isinstance(status, bytes) else status
            ) or "unknown"
            result.append({"name": name, **meta})
        return result

    def get_server_tools(self, server_name: str) -> List[str]:
        """获取某个 MCP Server 提供的工具列表（从 Redis 缓存读取）。

        Pi MCP Adapter 负责定期更新此缓存。
        """
        tools_json = self.redis.get(self.KEY_TOOLS.format(server=server_name))
        if tools_json:
            return json.loads(
                tools_json.decode() if isinstance(tools_json, bytes) else tools_json
            )
        return []

    def set_server_tools(self, server_name: str, tools: List[str]):
        """写入工具列表缓存（由 Pi MCP Adapter 或 Commander 调用）。"""
        self.redis.set(
            self.KEY_TOOLS.format(server=server_name),
            json.dumps(tools, ensure_ascii=False),
            ex=3600,  # 1 小时 TTL，遵守 R1
        )

    # ── 健康检查 ──────────────────────────────────────────────

    async def test_connection(self, server_name: str) -> Dict:
        """测试 MCP Server 连接是否正常。

        通过尝试执行 Server 命令的健康检查子命令来验证。
        """
        result = {
            "server_name": server_name,
            "timestamp": datetime.now().isoformat(),
            "status": "unknown",
        }

        try:
            config = self._load_config()
            if server_name not in config.get("mcpServers", {}):
                result["status"] = "not_found"
                return result

            server_config = config["mcpServers"][server_name]
            if not server_config.get("enabled", False):
                result["status"] = "disabled"
                return result

            # 尝试健康探测：不同的 MCP Server 有不同的探测方式
            check_result = await self._health_probe(
                server_config["command"],
                server_config.get("args", []),
            )

            result["status"] = "connected" if check_result[0] else "error"
            result["output"] = check_result[1][:500]

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)[:500]

        # 缓存健康检查结果
        self.redis.set(
            self.KEY_HEALTH.format(server=server_name),
            json.dumps(result, ensure_ascii=False),
            ex=300,  # 5 分钟 TTL
        )

        return result

    async def run_health_checks(self) -> List[Dict]:
        """对所有已启用的 MCP Server 执行健康检查。"""
        servers = self.get_registered_servers()
        results = []
        for server in servers:
            if server.get("current_status") != "disabled":
                result = await self.test_connection(server["name"])
                results.append(result)
        return results

    # ── 搜索 ──────────────────────────────────────────────────

    async def search_mcp_server(self, capability: str) -> List[str]:
        """根据能力描述，搜索匹配的 MCP Server 名称。

        用于 Commander 自动发现需要注册的 MCP Server。
        支持已知 Server 的关键词映射 + Redis 注册表匹配。
        """
        # 已知能力 → Server 映射表（可扩展）
        KNOWN_SERVERS = {
            # Web/数据源
            "爬取": "firecrawl",
            "抓取": "firecrawl",
            "crawl": "firecrawl",
            "scrape": "firecrawl",
            "firecrawl": "firecrawl",
            "github": "github",
            "git": "github",
            "代码仓库": "github",
            "数据库": "mongodb",
            "mongodb": "mongodb",
            "mongo": "mongodb",
            "搜索": "google-search",
            "google": "google-search",
            "文件": "filesystem",
            "filesystem": "filesystem",
            "playwright": "playwright",
            "浏览器": "playwright",
            "browser": "playwright",
            "puppeteer": "playwright",
            # RAG 知识库检索
            "rag": "lightingmetal-rag",
            "知识库": "lightingmetal-rag",
            "知识检索": "lightingmetal-rag",
            "产品查询": "lightingmetal-rag",
            "产品搜索": "lightingmetal-rag",
            "产品知识": "lightingmetal-rag",
            "向量检索": "lightingmetal-rag",
            "语义搜索": "lightingmetal-rag",
            # 多模态 — 图片生成
            "生图": "image-gen",
            "画图": "image-gen",
            "生成图片": "image-gen",
            "图片生成": "image-gen",
            "图片": "image-gen",
            "图像": "image-gen",
            "海报": "image-gen",
            "banner": "image-gen",
            "产品图": "image-gen",
            "效果图": "image-gen",
            "dalle": "image-gen",
            "dall-e": "image-gen",
            "imagen": "image-gen",
            "image-gen": "image-gen",
            # deployment MCP Server
            "部署": "deployment",
            "deploy": "deployment",
            "上线": "deployment",
            "发布": "deployment",
            "推送": "deployment",
            "构建": "deployment",
            "打包": "deployment",
            "一键部署": "deployment",
            "生产": "deployment",
            "production": "deployment",
            "deployment": "deployment",
            "ci": "deployment",
            "cd": "deployment",
            "devops": "deployment",
            "运维": "deployment",
            "回滚": "deployment",
            "rollback": "deployment",
            "preview": "deployment",
            "预发": "deployment",
            "staging": "deployment",
        }

        capability_lower = capability.lower()

        # 1. 精确映射
        if capability_lower in KNOWN_SERVERS:
            return [KNOWN_SERVERS[capability_lower]]

        # 2. 关键词匹配
        matching = []
        for kw, server in KNOWN_SERVERS.items():
            if kw in capability_lower or capability_lower in kw:
                matching.append(server)

        if matching:
            return list(set(matching))

        # 3. 从 Redis 注册表中搜索
        all_servers = self.get_registered_servers()
        for server in all_servers:
            if capability_lower in server["name"].lower():
                matching.append(server["name"])

        return matching

    # ── 内部方法 ──────────────────────────────────────────────

    async def _prefetch_package(self, command: str, args: List[str]) -> str:
        """预拉取 npm 包（非关键路径）。"""
        try:
            package_name = args[0] if args else ""
            proc = await asyncio.create_subprocess_exec(
                command, "-y", package_name, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            return "success"
        except asyncio.TimeoutError:
            return "timeout"
        except Exception:
            return "skipped"

    async def _health_probe(self, command: str, args: List[str]) -> tuple:
        """执行健康探测。

        Returns:
            (success: bool, output: str)
        """
        # 策略：尝试 --ping / --version / --help 等常见探测参数
        probes = [
            ["--ping"],
            ["--version"],
            ["--help"],
            [],
        ]

        for probe_args in probes:
            try:
                proc = await asyncio.create_subprocess_exec(
                    command,
                    *(args + probe_args),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=10
                )
                output = (stdout + stderr).decode()
                # 只要进程正常退出（不崩溃），就认为可用
                if proc.returncode >= 0:
                    return True, output[:500]
            except asyncio.TimeoutError:
                continue
            except FileNotFoundError:
                return False, f"命令不存在: {command}"
            except Exception:
                continue

        return False, "所有探测均失败（超时或异常）"

    def _notify_adapter(self, action: str, server_name: str):
        """通过 Redis Pub/Sub 通知 Pi MCP Adapter 热重载配置。"""
        try:
            message = json.dumps({
                "action": action,
                "server_name": server_name,
                "timestamp": datetime.now().isoformat(),
            }, ensure_ascii=False)
            self.redis.publish(self.CHANNEL_CONFIG, message)
        except Exception as e:
            print(f"[MCPManager] 通知 Adapter 失败: {e}")

    def _persist_log(self, log: Dict):
        """持久化操作日志到 MongoDB。"""
        if self.mongo is None:
            return
        try:
            self.mongo["mcp_register_logs"].insert_one(log)
        except Exception as e:
            print(f"[MCPManager] MongoDB 写入失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 引导工具：从现有 mcp.json 初始化注册表
# ═══════════════════════════════════════════════════════════════

class MCPBootstrap:
    """将已有 mcp.json 中的 Server 批量注册到 Redis。

    用于系统启动时同步文件配置到 Redis 注册表。
    """

    def __init__(self, mcp_manager: MCPManager, config_path: str = ".pi/agent/mcp.json", **kwargs):
        self.manager = mcp_manager
        self.config_path = Path(config_path)

    def bootstrap(self) -> List[str]:
        """扫描 mcp.json，将所有 Server 注册到 Redis。

        Returns:
            注册成功的 Server 名称列表。
        """
        registered = []
        try:
            config = self.manager._load_config()
        except Exception:
            return registered

        for server_name, server_config in config.get("mcpServers", {}).items():
            meta = {
                "command": server_config.get("command", "npx"),
                "args": server_config.get("args", []),
                "registered_at": datetime.now().isoformat(),
                "enabled": server_config.get("enabled", True),
            }
            if "env" in server_config:
                meta["env"] = server_config["env"]

            self.manager.redis.hset(
                self.manager.KEY_REGISTRY,
                server_name,
                json.dumps(meta, ensure_ascii=False),
            )
            self.manager.redis.hset(
                self.manager.KEY_STATUS,
                server_name,
                "enabled" if meta["enabled"] else "disabled",
            )
            registered.append(server_name)

        return registered
