#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Commander Client SDK v1.0 — 外部 Agent 标准化接入框架
=====================================================
任何语言的外部 Agent 通过此协议接入 Commander 生态。

Python 版本 — 开箱即用，零依赖（仅标准库 + websockets 可选）

协议:
  通道1: HTTP REST (3399) — Agent 注册/心跳/能力发现/健康检查
  通道2: WebSocket (3398) — 任务派发/结果接收/实时通信

使用:
  from commander_client import CommanderClient

  client = CommanderClient(
      commander_host="$COMMANDER_HOST",
      agent_id="my-agent-v1",
      capabilities=["search", "translate"],
      metadata={"version": "1.0.0"}
  )

  # 一行启动（阻塞主线程直到 stop）
  client.start()

  # 或者手动控制
  await client.connect_ws()
  corr_id = await client.dispatch("翻译官", "翻译: Hello → 俄语")
  result = await client.wait_result(corr_id, timeout=30)
"""

import json
import os
import sys
import time
import threading
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import URLError


# ═══════════════════════════════════════════════════════════════
# CommanderClient — 外部 Agent 标准客户端
# ═══════════════════════════════════════════════════════════════

class CommanderClient:
    """Commander 外部接入客户端。

    封装了完整的生命周期:
      注册 → 心跳保活 → WebSocket连接 → 任务派发 → 结果接收 → 注销

    示例:
        client = CommanderClient("$COMMANDER_HOST", "my-agent")
        client.start()  # 阻塞直到 Ctrl+C
    """

    def __init__(self,
                 commander_host: str = "127.0.0.1",
                 commander_http_port: int = 3399,
                 commander_ws_port: int = 3398,
                 agent_id: str = None,
                 agent_ip: str = None,
                 agent_port: int = 9900,
                 capabilities: List[str] = None,
                 metadata: Dict[str, Any] = None):
        """
        Args:
            commander_host: Commander 所在主机
            commander_http_port: HTTP 心跳端口
            commander_ws_port: WebSocket 端口
            agent_id: Agent 唯一标识（默认自动生成）
            agent_ip: Agent IP（Commander反向连接用）
            agent_port: Agent 监听端口
            capabilities: 能力标签列表
            metadata: 自定义元数据
        """
        self.commander_host = commander_host
        self.http_base = f"http://{commander_host}:{commander_http_port}"
        self.ws_url = f"ws://{commander_host}:{commander_ws_port}"
        self.agent_id = agent_id or f"agent-{uuid.uuid4().hex[:8]}"
        self.agent_ip = agent_ip or self._get_local_ip()
        self.agent_port = agent_port
        self.capabilities = capabilities or ["generic"]
        self.metadata = metadata or {}

        self._running = False
        self._ws = None
        self._pending: Dict[str, asyncio_future_or_queue] = {}
        self._handlers: Dict[str, List[Callable]] = {}
        self._heartbeat_thread = None
        self._client_id = None

    # ═══════════════════════════════════════════════════════════
    # HTTP 通道 — 注册/心跳/发现
    # ═══════════════════════════════════════════════════════════

    def _http(self, method: str, path: str, data: dict = None) -> dict:
        """HTTP 请求封装。"""
        url = f"{self.http_base}{path}"
        if data:
            req = urllib_request.Request(
                url, data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"})
        else:
            req = urllib_request.Request(url)
        with urllib_request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def register(self) -> dict:
        """注册到 Commander（首次调用）。"""
        result = self._http("POST", "/heartbeat", self._heartbeat_payload())
        if result.get("status") == "accepted":
            print(f"[{self.agent_id}] ✅ 已注册, 下次心跳: {result.get('next_heartbeat_in_s', '?')}s")
        else:
            print(f"[{self.agent_id}] ⚠️ 注册失败: {result}")
        return result

    def heartbeat(self) -> dict:
        """发送心跳续期。"""
        try:
            return self._http("POST", "/heartbeat", self._heartbeat_payload())
        except Exception:
            return {"status": "error"}

    def deregister(self):
        """主动注销。"""
        try:
            self._http("POST", "/heartbeat/deregister", {"agent_id": self.agent_id})
            print(f"[{self.agent_id}] 👋 已注销")
        except Exception:
            pass

    def list_online_agents(self) -> List[dict]:
        """列出所有在线 Agent。"""
        return self._http("GET", "/heartbeat/online")

    def find_agent_by_capability(self, capability: str) -> dict:
        """按能力查找 Agent。"""
        try:
            return self._http("GET", f"/heartbeat/capability?q={capability}")
        except URLError as e:
            if hasattr(e, 'code') and e.code == 404:
                return {"status": "not_found"}
            raise

    def get_health(self) -> dict:
        """获取 Agent 集群健康状态。"""
        return self._http("GET", "/heartbeat/status")

    def _heartbeat_payload(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "ip": self.agent_ip,
            "port": self.agent_port,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }

    def _start_heartbeat_loop(self):
        """后台心跳线程。"""
        while self._running:
            try:
                self.heartbeat()
            except Exception:
                pass
            time.sleep(30)

    # ═══════════════════════════════════════════════════════════
    # WebSocket 通道 — 任务派发 (需要 websockets 库)
    # ═══════════════════════════════════════════════════════════

    async def connect_ws(self):
        """建立 WebSocket 长连接。"""
        try:
            import websockets
        except ImportError:
            raise RuntimeError("需要安装 websockets: pip install websockets")

        self._ws = await websockets.connect(self.ws_url)
        data = json.loads(await self._ws.recv())
        self._client_id = data["client_id"]
        print(f"[{self.agent_id}] 🌉 WS 已连接 (client={self._client_id})")

        # 注册
        await self._ws.send(json.dumps({
            "action": "register",
            "agent_id": self.agent_id,
            "capabilities": self.capabilities,
        }))
        await self._ws.recv()  # registered 确认
        return self._client_id

    async def disconnect_ws(self):
        """断开 WebSocket。"""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def dispatch(self, target: str, task, correlation_id: str = None) -> str:
        """通过 WebSocket 派发任务到 Commander 内部 Agent。

        Args:
            target: 目标Agent名 (如 "翻译官", "售前经理", "Commander")
            task: 任务内容 (字符串或字典)
            correlation_id: 关联ID (默认自动生成)

        Returns:
            correlation_id — 用于后续 wait_result()
        """
        if not self._ws:
            raise RuntimeError("未连接 WebSocket，先调用 connect_ws()")

        corr_id = correlation_id or f"{self.agent_id}-{uuid.uuid4().hex[:8]}"
        msg = {
            "action": "dispatch",
            "target": target,
            "task": task,
            "correlation_id": corr_id,
        }
        await self._ws.send(json.dumps(msg, ensure_ascii=False))
        print(f"[{self.agent_id}] 📤 派发 → {target} (id={corr_id})")
        return corr_id

    async def wait_result(self, correlation_id: str, timeout: float = 120) -> dict:
        """等待指定 correlation_id 的任务结果。

        Returns:
            {"type": "task_result", "result": ..., "agent": ..., "elapsed_ms": ...}
            或 {"type": "task_error", "error": ...}
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(await self._ws.recv())
            msg_type = msg.get("type", "")

            if msg.get("correlation_id") == correlation_id:
                if msg_type == "task_result":
                    print(f"[{self.agent_id}] ✅ 结果: {msg.get('agent')} ({msg.get('elapsed_ms')}ms)")
                elif msg_type == "task_error":
                    print(f"[{self.agent_id}] ❌ 错误: {msg.get('error')}")
                else:
                    print(f"[{self.agent_id}] 📨 {msg_type}")
                return msg

            # 其他消息 → 交给消息处理器
            self._dispatch_message(msg)

        return {"type": "task_error", "error": f"超时 ({timeout}s)"}

    async def dispatch_and_wait(self, target: str, task, timeout: float = 120) -> dict:
        """派发任务并等待结果（一步完成）。"""
        corr_id = await self.dispatch(target, task)
        return await self.wait_result(corr_id, timeout)

    async def ping(self):
        """发送 WebSocket ping。"""
        if self._ws:
            await self._ws.send(json.dumps({"action": "ping"}))

    async def query_status(self) -> dict:
        """查询 Agent 集群状态。"""
        if not self._ws:
            return {}
        await self._ws.send(json.dumps({"action": "status"}))
        msg = json.loads(await self._ws.recv())
        return msg

    def on_message(self, msg_type: str, handler: Callable):
        """注册消息处理器。

        handler(msg_dict) — 当收到对应 type 的消息时调用
        """
        if msg_type not in self._handlers:
            self._handlers[msg_type] = []
        self._handlers[msg_type].append(handler)

    def _dispatch_message(self, msg: dict):
        """将消息分发给注册的处理器。"""
        msg_type = msg.get("type", "")
        for handler in self._handlers.get(msg_type, []):
            try:
                handler(msg)
            except Exception as e:
                print(f"[{self.agent_id}] 消息处理异常: {e}")

    # ═══════════════════════════════════════════════════════════
    # 高级: 浏览器 MCP 代理
    # ═══════════════════════════════════════════════════════════

    async def browser_navigate(self, url: str) -> dict:
        """通过 Commander 的浏览器 MCP 代理访问网页。"""
        corr_id = f"browser-{uuid.uuid4().hex[:8]}"
        await self._ws.send(json.dumps({
            "action": "browser",
            "tool": "browser_navigate",
            "arguments": {"url": url},
            "correlation_id": corr_id,
        }))
        return await self.wait_result(corr_id, timeout=30)

    async def browser_screenshot(self) -> dict:
        """截取当前页面。"""
        corr_id = f"screenshot-{uuid.uuid4().hex[:8]}"
        await self._ws.send(json.dumps({
            "action": "browser",
            "tool": "browser_screenshot",
            "arguments": {},
            "correlation_id": corr_id,
        }))
        return await self.wait_result(corr_id, timeout=15)

    # ═══════════════════════════════════════════════════════════
    # 生命周期管理
    # ═══════════════════════════════════════════════════════════

    def start(self):
        """一键启动（HTTP 心跳 + WebSocket + 事件循环）。

        注意: 此方法会阻塞当前线程。
        """
        import asyncio

        self._running = True

        # 1. 注册
        self.register()

        # 2. HTTP 心跳后台线程
        self._heartbeat_thread = threading.Thread(
            target=self._start_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

        # 3. WebSocket 主循环
        async def _ws_main():
            while self._running:
                try:
                    await self.connect_ws()
                    # 启动后台 ping
                    async def _ping_loop():
                        while self._ws:
                            await asyncio.sleep(30)
                            try:
                                await self.ping()
                            except Exception:
                                break
                    asyncio.ensure_future(_ping_loop())

                    # 消息接收循环
                    while self._ws and self._running:
                        try:
                            msg = json.loads(
                                await asyncio.wait_for(self._ws.recv(), timeout=10))
                            self._dispatch_message(msg)
                        except asyncio.TimeoutError:
                            continue
                        except Exception:
                            break
                except Exception as e:
                    print(f"[{self.agent_id}] ⚠️ WS 断开: {e}, 5s后重连...")
                    await asyncio.sleep(5)

        try:
            asyncio.get_event_loop().run_until_complete(_ws_main())
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止客户端，注销并清理资源。"""
        self._running = False
        self.deregister()
        print(f"[{self.agent_id}] 🛑 已停止")

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _get_local_ip() -> str:
        """获取本机 IP。"""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"


# ═══════════════════════════════════════════════════════════════
# 命令行入口
# ═══════════════════════════════════════════════════════════════

def cli_main():
    """命令行: python commander_client.py <command> [args...]"""
    import argparse

    parser = argparse.ArgumentParser(description="Commander Client CLI")
    parser.add_argument("--host", default="127.0.0.1", help="Commander 主机")
    parser.add_argument("--agent-id", default=None, help="Agent ID")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["status", "register", "agents", "health", "dispatch"])
    parser.add_argument("--target", help="派发目标 Agent 名")
    parser.add_argument("--task", help="任务内容")

    args = parser.parse_args()

    client = CommanderClient(commander_host=args.host, agent_id=args.agent_id,
                              capabilities=["cli-tool"])

    if args.command == "status":
        client.register()
        import asyncio
        async def _s():
            await client.connect_ws()
            status = await client.query_status()
            print(json.dumps(status, indent=2, ensure_ascii=False))
        asyncio.get_event_loop().run_until_complete(_s())

    elif args.command == "register":
        result = client.register()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "agents":
        agents = client.list_online_agents()
        print(json.dumps(agents, indent=2, ensure_ascii=False))

    elif args.command == "health":
        health = client.get_health()
        print(json.dumps(health, indent=2, ensure_ascii=False))

    elif args.command == "dispatch":
        if not args.target or not args.task:
            print("需要 --target 和 --task")
            sys.exit(1)
        import asyncio
        async def _d():
            await client.connect_ws()
            result = await client.dispatch_and_wait(args.target, args.task)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        asyncio.get_event_loop().run_until_complete(_d())

    client.deregister()


if __name__ == "__main__":
    cli_main()
