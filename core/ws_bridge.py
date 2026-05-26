#!/usr/bin/env python3
"""
WebSocket 桥接服务 — Commander 与外部客户端的长连接通道
=========================================================
外部客户端（网页、CLI、pi Agent 等）通过 WebSocket 连接到 Commander，
发送任务请求、接收结果，无需暴露内部 Redis。

协议: JSON over WebSocket
  客户端 → Commander:
    { "action": "dispatch",  "target": "翻译官", "task": "翻译这段文字...", "correlation_id": "uuid" }
    { "action": "ping" }
    { "action": "register",  "agent_id": "pi-client", "capabilities": ["translate", "search"] }

  Commander → 客户端:
    { "type": "pong" }
    { "type": "task_accepted",  "correlation_id": "uuid", "target": "翻译官" }
    { "type": "task_result",    "correlation_id": "uuid", "result": "...", "agent": "翻译官" }
    { "type": "task_error",     "correlation_id": "uuid", "error": "..." }
    { "type": "agent_status",   "agents": [...] }

内部流程:
  客户端 → WebSocket → Redis Pub/Sub (commander:dispatch) → Commander → Agent → 结果
  Agent → Redis Pub/Sub (agent:result) → Commander → WebSocket → 客户端

Constitution:
  R1 — 使用 `commander:*`, `agent:*` 前缀
  R2 — 最大连接数 50
"""

import asyncio
import json
import os
import time
import uuid
from datetime import datetime

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    aioredis = None
    HAS_REDIS = False

try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    HAS_WS = True
except ImportError:
    websockets = None
    HAS_WS = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

WS_PORT = int(os.environ.get("WS_PORT", "3398"))
WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
MAX_CONNECTIONS = int(os.environ.get("WS_MAX_CONNECTIONS", "50"))
RESPONSE_TIMEOUT_S = int(os.environ.get("WS_RESPONSE_TIMEOUT", "120"))

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "Yaxiio2026")

DISPATCH_CHANNEL = "commander:dispatch"
RESULT_CHANNEL = "agent:result"
HEARTBEAT_CHANNEL = "commander:heartbeat"


# ═══════════════════════════════════════════════════════════════
# WebSocket 桥接服务
# ═══════════════════════════════════════════════════════════════

class WSBridge:
    """WebSocket ↔ Redis Pub/Sub 双向桥接。

    每个 WebSocket 客户端对应一个唯一的 client_id，
    通过 correlation_id 关联请求和响应。
    """

    def __init__(self):
        self.clients: dict[str, websockets.WebSocketServerProtocol] = {}
        self.pending: dict[str, asyncio.Future] = {}  # correlation_id → Future
        self.redis = None
        self.connected_clients = set()

    async def init_redis(self):
        """初始化 Redis 连接（异步 Pub/Sub）。"""
        if not HAS_REDIS:
            print("[WSBridge] ⚠️ redis 不可用")
            return None
        try:
            self.redis = await aioredis.Redis(
                host=REDIS_HOST, port=REDIS_PORT,
                password=REDIS_PASSWORD,
                decode_responses=True)
            await self.redis.ping()
            print(f"[WSBridge] ✅ Redis 已连接 {REDIS_HOST}:{REDIS_PORT}")
            return self.redis
        except Exception as e:
            print(f"[WSBridge] ⚠️ Redis 连接失败: {e}")
            return None

    async def _listen_results(self):
        """后台监听 agent:result 频道，匹配 pending 中的请求。"""
        if not self.redis:
            return
        try:
            pubsub = self.redis.pubsub()
            await pubsub.subscribe(RESULT_CHANNEL)
            print(f"[WSBridge] 👂 监听 {RESULT_CHANNEL}")

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    corr_id = data.get("correlation_id")
                    if corr_id and corr_id in self.pending:
                        future = self.pending.pop(corr_id)
                        if not future.done():
                            future.set_result(data)
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[WSBridge] 监听异常: {e}")

    async def handle_client(self, websocket):
        """处理单个 WebSocket 客户端连接。"""
        client_id = uuid.uuid4().hex[:12]
        self.clients[client_id] = websocket

        try:
            print(f"[WSBridge] 🔗 客户端 {client_id} 已连接 (当前 {len(self.clients)} 个)")
            await self._send(websocket, {"type": "connected", "client_id": client_id})

            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self._dispatch(msg, websocket, client_id)
                except json.JSONDecodeError:
                    await self._send(websocket, {"type": "error", "error": "无效的 JSON"})
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.pop(client_id, None)
            # 清理该客户端残留的 pending
            to_remove = [k for k, f in self.pending.items()
                         if getattr(f, "_client_id", None) == client_id]
            for k in to_remove:
                f = self.pending.pop(k, None)
                if f and not f.done():
                    f.cancel()
            print(f"[WSBridge] 🔌 客户端 {client_id} 已断开 (剩余 {len(self.clients)} 个)")

    async def _dispatch(self, msg: dict, ws, client_id: str):
        """分发消息到对应处理器。"""
        action = msg.get("action", "")

        if action == "ping":
            await self._send(ws, {"type": "pong", "time": time.time()})

        elif action == "status":
            await self._handle_status(ws)

        elif action == "dispatch":
            await self._handle_dispatch(msg, ws, client_id)

        elif action == "register":
            await self._handle_register(msg, ws, client_id)

        elif action == "browser":
            await self._handle_browser(msg, ws, client_id)

        else:
            await self._send(ws, {"type": "error", "error": f"未知 action: {action}"})

    async def _handle_dispatch(self, msg: dict, ws, client_id: str):
        """将任务派发到内部 Agent，等待响应。"""
        target = msg.get("target", "")
        task = msg.get("task", "")
        correlation_id = msg.get("correlation_id", uuid.uuid4().hex[:16])

        if not target or not task:
            await self._send(ws, {
                "type": "task_error",
                "correlation_id": correlation_id,
                "error": "缺少 target 或 task"
            })
            return

        # 创建 Future 等待响应
        future = asyncio.get_event_loop().create_future()
        future._client_id = client_id
        self.pending[correlation_id] = future

        # 发布到 Commander 派发频道
        dispatch_msg = {
            "action": "dispatch",
            "target": target,
            "task": task,
            "correlation_id": correlation_id,
            "from_client": client_id,
            "timestamp": time.time(),
        }

        if self.redis:
            try:
                await self.redis.publish(DISPATCH_CHANNEL, json.dumps(dispatch_msg, ensure_ascii=False))
                await self._send(ws, {
                    "type": "task_accepted",
                    "correlation_id": correlation_id,
                    "target": target,
                })
            except Exception as e:
                self.pending.pop(correlation_id, None)
                await self._send(ws, {
                    "type": "task_error",
                    "correlation_id": correlation_id,
                    "error": f"Redis 发布失败: {e}"
                })
                return
        else:
            self.pending.pop(correlation_id, None)
            await self._send(ws, {
                "type": "task_error",
                "correlation_id": correlation_id,
                "error": "Redis 不可用"
            })
            return

        # 等待 Agent 响应
        try:
            result = await asyncio.wait_for(future, timeout=RESPONSE_TIMEOUT_S)
            await self._send(ws, {
                "type": "task_result",
                "correlation_id": correlation_id,
                "result": result.get("result", ""),
                "agent": result.get("from", target),
                "elapsed_ms": int((time.time() - dispatch_msg["timestamp"]) * 1000),
            })
        except asyncio.TimeoutError:
            self.pending.pop(correlation_id, None)
            await self._send(ws, {
                "type": "task_error",
                "correlation_id": correlation_id,
                "error": f"任务超时 ({RESPONSE_TIMEOUT_S}s)",
            })

    async def _handle_status(self, ws):
        """查询 Agent 状态。"""
        agents = []
        if self.redis:
            try:
                keys = await self.redis.keys("agent:pool:*")
                for key in keys:
                    val = await self.redis.hgetall(key)
                    if val:
                        agents.append(val)
            except Exception:
                pass
        await self._send(ws, {
            "type": "agent_status",
            "agents": agents,
            "connected_clients": len(self.clients),
        })

    async def _handle_register(self, msg: dict, ws, client_id: str):
        """注册外部客户端元信息。"""
        agent_id = msg.get("agent_id", "")
        capabilities = msg.get("capabilities", [])
        if self.redis and agent_id:
            try:
                await self.redis.hset(
                    f"commander:ws:client:{agent_id}",
                    mapping={
                        "client_id": client_id,
                        "capabilities": json.dumps(capabilities),
                        "connected_at": str(time.time()),
                    })
                await self.redis.expire(f"commander:ws:client:{agent_id}", 300)
            except Exception:
                pass
        await self._send(ws, {
            "type": "registered",
            "client_id": client_id,
            "agent_id": agent_id,
        })

    async def _handle_browser(self, msg: dict, ws, client_id: str):
        """代理浏览器 MCP 调用 — 通过持久 Browser Harness 进程执行。"""
        tool_name = msg.get("tool", "")
        tool_args = msg.get("arguments", {})
        correlation_id = msg.get("correlation_id", uuid.uuid4().hex[:16])

        if not tool_name:
            await self._send(ws, {
                "type": "browser_error",
                "correlation_id": correlation_id,
                "error": "缺少 tool 参数"
            })
            return

        try:
            result = await asyncio.to_thread(
                self._call_browser_persistent, tool_name, tool_args
            )
            await self._send(ws, {
                "type": "browser_result",
                "correlation_id": correlation_id,
                "tool": tool_name,
                "result": result,
            })
        except Exception as e:
            await self._send(ws, {
                "type": "browser_error",
                "correlation_id": correlation_id,
                "error": str(e),
            })

    _browser_proc = None
    _browser_lock = None

    @classmethod
    def _get_browser_proc(cls):
        """获取或创建持久浏览器进程。"""
        import threading
        import subprocess
        if cls._browser_lock is None:
            cls._browser_lock = threading.Lock()
        with cls._browser_lock:
            if cls._browser_proc is None or cls._browser_proc.poll() is not None:
                cls._browser_proc = subprocess.Popen(
                    ["python3", "-u", "/app/.pi/skills/commander/mcp_servers/browser_harness.py"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True
                )
                # Initialize
                cls._browser_proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0", "id": 0,
                    "method": "initialize", "params": {}
                }) + "\n")
                cls._browser_proc.stdin.flush()
                init_resp = cls._browser_proc.stdout.readline()
            return cls._browser_proc

    @classmethod
    def _call_browser_persistent(cls, tool_name: str, tool_args: dict) -> dict:
        """通过持久进程调用浏览器工具。"""
        proc = cls._get_browser_proc()
        proc.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": tool_args}
        }) + "\n")
        proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        content = resp.get("result", {}).get("content", [])
        if content:
            return json.loads(content[0].get("text", "{}"))
        return {"error": str(resp.get("error", "unknown"))}

    @staticmethod
    async def _send(ws, data: dict):
        """安全发送 JSON 消息。"""
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

async def main():
    if not HAS_WS:
        print("[WSBridge] ❌ websockets 库未安装，pip3 install websockets")
        return

    bridge = WSBridge()
    await bridge.init_redis()

    # 启动结果监听
    listen_task = asyncio.create_task(bridge._listen_results())

    # 启动 WebSocket 服务器
    print(f"[WSBridge] 🌉 WebSocket 桥接启动 ws://{WS_HOST}:{WS_PORT}")
    async with ws_serve(bridge.handle_client, WS_HOST, WS_PORT):
        await asyncio.Future()  # 永久运行


if __name__ == "__main__":
    asyncio.run(main())
