
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
WebSocket Bridge v3.1 — 双心跳 + Session 协议
=============================================
升级 ws_bridge.py：原生 ping/pong + 应用层心跳 + Session 集成

双心跳机制:
  - 原生 WebSocket ping/pong (15s间隔, 10s超时) — 检测死连接
  - 应用层 {"action":"ping"} (60s间隔) — 更新 Redis 状态

Session 协议:
  - 连接时携带 token: ws://host:3398?token=sess-xxx
  - 自动验证令牌签名 + 指纹
  - 重连时声明 last_seq，一次性吐出积压消息
"""

import asyncio
import json
import os
import time
import uuid

WS_PORT = int(os.environ.get("WS_PORT", "3398"))
WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
WS_PING_INTERVAL = int(os.environ.get("WS_PING_INTERVAL", "15"))
WS_PING_TIMEOUT = int(os.environ.get("WS_PING_TIMEOUT", "10"))
APP_HEARTBEAT_INTERVAL = int(os.environ.get("APP_HEARTBEAT_INTERVAL", "60"))
MAX_CONNECTIONS = int(os.environ.get("WS_MAX_CONNECTIONS", "50"))

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    aioredis = None
    HAS_REDIS = False

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    from websockets.exceptions import ConnectionClosed
    HAS_WS = True
except ImportError:
    websockets = None
    HAS_WS = False


class WSBridgeV3:
    """WebSocket V3: 双心跳 + Session 协议。"""

    def __init__(self, session_manager=None):
        self.session_mgr = session_manager
        self.clients: dict[str, dict] = {}  # client_id → {ws, token, fingerprint, last_seq}
        self.redis = None
        self._running = False

    async def init_redis(self):
        if not HAS_REDIS:
            return
        try:
            self.redis = await aioredis.Redis(
                host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                password=os.environ.get("REDIS_PASSWORD", ""),
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self.redis.ping()
            print(f"[WSBridgeV3] ✅ Redis 已连接")
        except Exception as e:
            print(f"[WSBridgeV3] ⚠️ Redis 连接失败: {e}")

    async def handle_client(self, websocket):
        """处理单个 WebSocket 连接 — Session 协议。"""
        client_id = uuid.uuid4().hex[:12]
        client_info = {"ws": websocket, "token": None, "fingerprint": None, "last_seq": 0}
        self.clients[client_id] = client_info

        try:
            await self._send(websocket, {
                "type": "connected",
                "client_id": client_id,
                "server_time": time.time(),
            })

            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self._dispatch(client_id, websocket, msg)
                except json.JSONDecodeError:
                    await self._send(websocket, {"type": "error", "error": "无效的 JSON"})

        except ConnectionClosed:
            pass
        finally:
            info = self.clients.pop(client_id, {})
            if info.get("token") and self.session_mgr:
                self.session_mgr.disconnect(info["token"], info.get("fingerprint", ""))
            print(f"[WSBridgeV3] 🔌 {client_id} 断开 (剩余 {len(self.clients)} 个)")

    async def _dispatch(self, client_id: str, ws, msg: dict):
        action = msg.get("action", "")

        if action == "ping":
            await self._send(ws, {"type": "pong", "time": time.time()})

        elif action == "connect":
            # Session 连接：携带 token + last_seq
            token = msg.get("token", "")
            fingerprint = msg.get("fingerprint", str(id(ws)))[:64]
            last_seq = msg.get("last_seq", 0)

            if self.session_mgr:
                result = self.session_mgr.connect(token, fingerprint)
                if result.get("error"):
                    await self._send(ws, {"type": "error", "error": result["error"]})
                    return
                last_seq = result.get("last_seq", last_seq)

                # 注册客户端信息
                self.clients[client_id].update({
                    "token": token, "fingerprint": fingerprint, "last_seq": last_seq,
                })

                # 吐出离线积压消息
                offline_msgs = self.session_mgr.get_offline_messages(token, last_seq)
                for m in offline_msgs:
                    await self._send(ws, {
                        "type": "offline_message",
                        "seq": m["seq"],
                        "message": m["payload"],
                    })

            await self._send(ws, {
                "type": "connected",
                "client_id": client_id,
                "last_seq": last_seq,
                "offline_count": len(offline_msgs) if self.session_mgr else 0,
            })

        elif action == "register":
            await self._send(ws, {
                "type": "registered",
                "client_id": client_id,
                "agent_id": msg.get("agent_id", ""),
            })

        elif action == "dispatch":
            # 通过 Redis 转发给 Commander
            if self.redis:
                dispatch_msg = {
                    "action": "dispatch",
                    "target": msg.get("target", ""),
                    "task": msg.get("task", ""),
                    "correlation_id": msg.get("correlation_id", uuid.uuid4().hex[:16]),
                    "from_client": client_id,
                    "timestamp": time.time(),
                }
                await self.redis.publish("commander:dispatch", json.dumps(dispatch_msg, ensure_ascii=False))
                await self._send(ws, {
                    "type": "task_accepted",
                    "correlation_id": dispatch_msg["correlation_id"],
                    "target": dispatch_msg["target"],
                })

        elif action == "status":
            await self._send(ws, {
                "type": "agent_status",
                "connected_clients": len(self.clients),
            })

        else:
            await self._send(ws, {"type": "error", "error": f"未知 action: {action}"})

    @staticmethod
    async def _send(ws, data: dict):
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


async def main():
    """独立启动入口。"""
    if not HAS_WS:
        print("[WSBridgeV3] ❌ websockets 未安装")
        return

    bridge = WSBridgeV3()
    await bridge.init_redis()

    print(f"[WSBridgeV3] 🌉 WebSocket V3 启动 ws://{WS_HOST}:{WS_PORT}")
    print(f"[WSBridgeV3]    原生ping间隔={WS_PING_INTERVAL}s, 超时={WS_PING_TIMEOUT}s")
    print(f"[WSBridgeV3]    应用心跳间隔={APP_HEARTBEAT_INTERVAL}s")

    async with ws_serve(
        bridge.handle_client, WS_HOST, WS_PORT,
        ping_interval=WS_PING_INTERVAL,
        ping_timeout=WS_PING_TIMEOUT,
        max_size=10 * 1024 * 1024,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
