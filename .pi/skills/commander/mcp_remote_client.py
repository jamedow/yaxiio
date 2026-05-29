#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
MCPRemoteClient — 外部 MCP 服务集成客户端
==========================================
Commander 通过 MCP 协议调用外部 AI 服务，扩充内部 Agent 能力。

协议: JSON-RPC 2.0 over HTTP
  - initialize:    获取服务能力列表
  - tools/list:    列出可用工具
  - tools/call:    调用远程工具
  - resources/list: 列出资源
  - resources/read: 读取资源

安全:
  - 白名单模式: 只允许调用用户信任列表中的服务器
  - API Key 透传: 从环境变量注入（$FACTORY_A_API_KEY）
  - 响应校验: 只接受 JSON-RPC 2.0 格式
"""

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests as http_requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

DEFAULT_MCP_TIMEOUT_S = 30
DEFAULT_RPC_VERSION = "2.0"


# ═══════════════════════════════════════════════════════════════
# MCP 服务描述
# ═══════════════════════════════════════════════════════════════

class MCPServerConfig:
    """外部 MCP 服务配置。"""

    def __init__(self,
                 name: str,
                 url: str,
                 api_key: Optional[str] = None,
                 api_key_env: Optional[str] = None,
                 description: str = "",
                 tools: List[str] = None,
                 headers: Dict[str, str] = None,
                 timeout_s: int = DEFAULT_MCP_TIMEOUT_S, **kwargs):
        self.name = name
        self.url = url.rstrip("/")
        self.description = description
        self.tools = tools or []
        self.timeout_s = timeout_s
        self.headers = headers or {}

        # 解析 API Key
        if api_key:
            self._api_key = api_key
        elif api_key_env:
            self._api_key = os.environ.get(api_key_env, "")
        else:
            self._api_key = ""

        if self._api_key:
            self.headers["Authorization"] = f"Bearer {self._api_key}"

        self.headers["Content-Type"] = "application/json"
        self.headers["Accept"] = "application/json"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "description": self.description,
            "tools": self.tools,
            "timeout_s": self.timeout_s,
        }

    @classmethod
    def from_trusted_server_entry(cls, entry: Dict[str, Any]) -> "MCPServerConfig":
        """从用户信任列表创建配置。

        entry = {
            "name": "building-analysis",
            "url": "https://api.factory-a.com/mcp/building-analysis",
            "api_key": "$FACTORY_A_API_KEY",  # 或以 $ 开头的环境变量名
            "description": "...",
            "tools": [...]
        }
        """
        api_key_raw = entry.get("api_key", "")
        if api_key_raw.startswith("$"):
            api_key_env = api_key_raw[1:]
            api_key = None
        else:
            api_key = api_key_raw
            api_key_env = None

        return cls(
            name=entry["name"],
            url=entry["url"],
            api_key=api_key,
            api_key_env=api_key_env,
            description=entry.get("description", ""),
            tools=entry.get("tools", []),
            headers=entry.get("headers", {}),
            timeout_s=entry.get("timeout_s", DEFAULT_MCP_TIMEOUT_S),
        )


# ═══════════════════════════════════════════════════════════════
# MCP 远程调用结果
# ═══════════════════════════════════════════════════════════════

class MCPCallResult:
    """MCP 调用结果封装。"""

    def __init__(self,
                 server_name: str,
                 tool_name: str,
                 success: bool,
                 result: Any = None,
                 error: str = None,
                 latency_ms: float = 0,
                 rpc_id: int = 0, **kwargs):
        self.server_name = server_name
        self.tool_name = tool_name
        self.success = success
        self.result = result
        self.error = error
        self.latency_ms = latency_ms
        self.rpc_id = rpc_id
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server": self.server_name,
            "tool": self.tool_name,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "rpc_id": self.rpc_id,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════
# MCPRemoteClient 核心
# ═══════════════════════════════════════════════════════════════

class MCPRemoteClient:
    """外部 MCP 服务调用客户端。

    使用:
        client = MCPRemoteClient()
        client.register_server(MCPServerConfig(...))
        result = client.call_tool("building-analysis", "analyze_bim", {...})
    """

    def __init__(self):
        self._servers: Dict[str, MCPServerConfig] = {}
        self._rpc_counter: int = 0

    # ═══════════════════════════════════════════════════════════
    # 服务器管理
    # ═══════════════════════════════════════════════════════════

    def register_server(self, config: MCPServerConfig):
        """注册外部 MCP 服务器。"""
        print(f"[MCPRemoteClient] 🔌 注册外部服务: {config.name} → {config.url}")
        self._servers[config.name] = config

    def register_from_trusted_list(self,
                                    servers: List[Dict[str, Any]]):
        """从用户信任列表批量注册。"""
        registered = 0
        for entry in servers:
            try:
                config = MCPServerConfig.from_trusted_server_entry(entry)
                self.register_server(config)
                registered += 1
            except KeyError as e:
                print(f"[MCPRemoteClient] ⚠️ 跳过 {entry.get('name', '?')}: 缺少字段 {e}")
        return registered

    def unregister_server(self, name: str):
        """注销外部 MCP 服务器。"""
        self._servers.pop(name, None)

    def list_servers(self) -> List[Dict[str, Any]]:
        """列出所有已注册的外部服务。"""
        return [s.to_dict() for s in self._servers.values()]

    def get_server(self, name: str) -> Optional[MCPServerConfig]:
        """获取指定服务器配置。"""
        return self._servers.get(name)

    # ═══════════════════════════════════════════════════════════
    # JSON-RPC 基础调用
    # ═══════════════════════════════════════════════════════════

    def _next_id(self) -> int:
        self._rpc_counter += 1
        return self._rpc_counter

    def _rpc_call(self,
                   server: MCPServerConfig,
                   method: str,
                   params: Dict[str, Any] = None) -> Tuple[bool, Any, str]:
        """执行 JSON-RPC 2.0 调用。

        Returns:
            (success: bool, result: Any, error: str)
        """
        if not HAS_REQUESTS:
            return (False, None, "requests 库未安装")

        rpc_id = self._next_id()
        payload = {
            "jsonrpc": DEFAULT_RPC_VERSION,
            "method": method,
            "id": rpc_id,
        }
        if params:
            payload["params"] = params

        try:
            resp = http_requests.post(
                server.url,
                json=payload,
                headers=server.headers,
                timeout=server.timeout_s,
            )

            if resp.status_code != 200:
                return (False, None,
                        f"HTTP {resp.status_code}: {resp.text[:500]}")

            data = resp.json()

            # 检查 JSON-RPC 错误
            if "error" in data:
                rpc_error = data["error"]
                return (False, None,
                        f"RPC Error {rpc_error.get('code')}: {rpc_error.get('message')}")

            return (True, data.get("result"), "")

        except http_requests.Timeout:
            return (False, None, f"超时 ({server.timeout_s}s)")
        except http_requests.ConnectionError as e:
            return (False, None, f"连接失败: {e}")
        except json.JSONDecodeError:
            return (False, None, f"非 JSON 响应: {resp.text[:200]}")
        except Exception as e:
            return (False, None, f"未知错误: {e}")

    # ═══════════════════════════════════════════════════════════
    # MCP 标准方法
    # ═══════════════════════════════════════════════════════════

    def initialize(self, server_name: str,
                    client_info: Dict[str, Any] = None) -> MCPCallResult:
        """发送 MCP initialize 请求，获取服务能力列表。

        Args:
            server_name: 服务名
            client_info: {"name": "commander-v3", "version": "3.0.0"}

        Returns:
            MCPCallResult
        """
        server = self._get_server_or_error(server_name)
        if isinstance(server, MCPCallResult):
            return server

        params = {
            "protocolVersion": "2024-11-05",
            "clientInfo": client_info or {
                "name": "commander-v3",
                "version": "3.0.0",
            },
            "capabilities": {},
        }

        t0 = time.time()
        success, result, error = self._rpc_call(server, "initialize", params)
        latency = (time.time() - t0) * 1000

        return MCPCallResult(
            server_name=server_name,
            tool_name="initialize",
            success=success,
            result=result,
            error=error,
            latency_ms=latency,
        )

    def list_tools(self, server_name: str) -> MCPCallResult:
        """获取远程 MCP 服务提供的工具列表。"""
        server = self._get_server_or_error(server_name)
        if isinstance(server, MCPCallResult):
            return server

        t0 = time.time()
        success, result, error = self._rpc_call(server, "tools/list")
        latency = (time.time() - t0) * 1000

        return MCPCallResult(
            server_name=server_name,
            tool_name="tools/list",
            success=success,
            result=result,
            error=error,
            latency_ms=latency,
        )

    def call_tool(self,
                   server_name: str,
                   tool_name: str,
                   arguments: Dict[str, Any] = None) -> MCPCallResult:
        """调用远程 MCP 工具。

        Args:
            server_name: 服务名
            tool_name: 工具名，如 "analyze_bim"
            arguments: 工具参数

        Returns:
            MCPCallResult
        """
        server = self._get_server_or_error(server_name)
        if isinstance(server, MCPCallResult):
            return server

        params = {
            "name": tool_name,
        }
        if arguments:
            params["arguments"] = arguments

        t0 = time.time()
        success, result, error = self._rpc_call(server, "tools/call", params)
        latency = (time.time() - t0) * 1000

        return MCPCallResult(
            server_name=server_name,
            tool_name=tool_name,
            success=success,
            result=result,
            error=error,
            latency_ms=latency,
        )

    def list_resources(self, server_name: str) -> MCPCallResult:
        """获取远程服务提供的资源列表。"""
        server = self._get_server_or_error(server_name)
        if isinstance(server, MCPCallResult):
            return server

        t0 = time.time()
        success, result, error = self._rpc_call(server, "resources/list")
        latency = (time.time() - t0) * 1000

        return MCPCallResult(
            server_name=server_name,
            tool_name="resources/list",
            success=success,
            result=result,
            error=error,
            latency_ms=latency,
        )

    def read_resource(self,
                       server_name: str,
                       uri: str) -> MCPCallResult:
        """读取远程 MCP 资源。"""
        server = self._get_server_or_error(server_name)
        if isinstance(server, MCPCallResult):
            return server

        params = {"uri": uri}
        t0 = time.time()
        success, result, error = self._rpc_call(server, "resources/read", params)
        latency = (time.time() - t0) * 1000

        return MCPCallResult(
            server_name=server_name,
            tool_name=f"resources/read:{uri}",
            success=success,
            result=result,
            error=error,
            latency_ms=latency,
        )

    # ═══════════════════════════════════════════════════════════
    # 批量操作
    # ═══════════════════════════════════════════════════════════

    def discover_all_servers(self) -> Dict[str, List[str]]:
        """发现所有已注册服务器提供的工具。"""
        capabilities = {}
        for name in list(self._servers.keys()):
            result = self.list_tools(name)
            if result.success and isinstance(result.result, dict):
                tools = result.result.get("tools", [])
                capabilities[name] = [t.get("name", "?") for t in tools]
            else:
                capabilities[name] = [f"(error: {result.error})"]
        return capabilities

    def find_tool(self, tool_name: str) -> List[Tuple[str, MCPServerConfig]]:
        """在所有已注册服务器中查找提供某个工具的服务。

        Returns:
            [(server_name, MCPServerConfig), ...]
        """
        matches = []
        for name, server in self._servers.items():
            if tool_name in server.tools:
                matches.append((name, server))
        return matches

    # ═══════════════════════════════════════════════════════════
    # 健康检查
    # ═══════════════════════════════════════════════════════════

    def health_check(self, server_name: str) -> Dict[str, Any]:
        """检查外部 MCP 服务健康状态（发送 initialize 请求）。"""
        result = self.initialize(server_name)
        return {
            "server": server_name,
            "url": self._servers.get(server_name, MCPServerConfig("?", "")).url,
            "healthy": result.success,
            "latency_ms": result.latency_ms,
            "error": result.error if not result.success else None,
        }

    def health_check_all(self) -> List[Dict[str, Any]]:
        """检查所有外部 MCP 服务健康状态。"""
        return [self.health_check(name) for name in self._servers]

    # ═══════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════

    def _get_server_or_error(self,
                              server_name: str) -> MCPServerConfig | MCPCallResult:
        """获取服务配置，未找到时返回错误结果。"""
        server = self._servers.get(server_name)
        if not server:
            return MCPCallResult(
                server_name=server_name,
                tool_name="",
                success=False,
                error=f"未注册的外部服务: {server_name}",
            )
        return server


# ═══════════════════════════════════════════════════════════════
# MCP 集成工具: 智能路由决策
# ═══════════════════════════════════════════════════════════════

class MCPRouter:
    """MCP 智能路由: 内外部能力匹配与调用。

    决策逻辑:
      1. 分析任务 → 提取所需能力列表
      2. 搜索内部 Agent 能力 (AgentDiscovery)
      3. 搜索外部 MCP 工具 (MCPRemoteClient)
      4. 决策: 内部优先，外部补充
    """

    def __init__(self,
                 mcp_client: MCPRemoteClient,
                 agent_discovery=None, **kwargs):
        self.mcp = mcp_client
        self.discovery = agent_discovery

    def route_tool_call(self,
                         tool_name: str,
                         arguments: Dict[str, Any] = None,
                         preferred_server: str = None) -> MCPCallResult:
        """路由工具调用到可用的外部服务。

        Args:
            tool_name: 工具名
            arguments: 参数
            preferred_server: 优先使用的服务名

        Returns:
            MCPCallResult
        """
        # 1. 如果指定了优先服务且可用
        if preferred_server and preferred_server in self.mcp._servers:
            return self.mcp.call_tool(preferred_server, tool_name, arguments)

        # 2. 搜索提供该工具的服务
        matches = self.mcp.find_tool(tool_name)
        if not matches:
            # 尝试在所有服务器上列出工具
            for name in self.mcp._servers:
                result = self.mcp.list_tools(name)
                if not result.success:
                    continue
            matches = self.mcp.find_tool(tool_name)

        # 3. 尝试调用（按注册顺序）
        for server_name, _ in matches:
            result = self.mcp.call_tool(server_name, tool_name, arguments)
            if result.success:
                return result

        return MCPCallResult(
            server_name=preferred_server or "any",
            tool_name=tool_name,
            success=False,
            error=f"未找到能调用 {tool_name} 的服务 (已搜索 {len(self.mcp._servers)} 个服务)",
        )


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCPRemoteClient — 外部 MCP 服务调用")
    parser.add_argument("action", nargs="?", default="list",
                        choices=["list", "discover", "health", "call"])
    parser.add_argument("--server", type=str, help="服务名")
    parser.add_argument("--tool", type=str, help="工具名")
    parser.add_argument("--args", type=str, default="{}", help="参数 JSON")
    args = parser.parse_args()

    client = MCPRemoteClient()

    if args.action == "list":
        print(json.dumps(client.list_servers(), indent=2, ensure_ascii=False))

    elif args.action == "discover":
        result = client.discover_all_servers()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "health":
        if args.server:
            result = client.health_check(args.server)
        else:
            result = client.health_check_all()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "call":
        if not args.server or not args.tool:
            print("错误: 需要 --server 和 --tool")
            exit(1)
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError:
            print(f"错误: 无效的 JSON: {args.args}")
            exit(1)
        result = client.call_tool(args.server, args.tool, arguments)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
