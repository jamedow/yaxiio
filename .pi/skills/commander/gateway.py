#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
"""
Gateway — WebSocket/HTTP 接入网关
=====================================
在 Commander 六大优化引擎基础上，引入 SessionManager 实现:
  会话持久化 — Session 独立于 Connection 生命周期
  离线任务托管 — 客户端断开，任务继续运行，结果暂存队列
  心跳重连 — 客户端恢复后，积压消息一次性吐出（seq 去重）
  多端互通 — 同一 token 在浏览器/手机/PC 多端接入
  历史同步 — 新端接入自动获得完整对话历史
  分层存储 — Redis 热数据 + MongoDB 冷归档

这是 Yaxiio 对外的统一入口。内部调用 commander.py 做任务编排。

启动:
  python3 gateway.py
  python3 gateway.py --ws-port 3398 --http-port 3399
"""

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from typing import Any, Dict, Optional, Set

# 允许从同级目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commander import CommanderV2
from session_manager import SessionManager, SessionBridge

# 可选 WebSocket 库
try:
    import websockets
    from websockets.asyncio.server import serve as ws_serve
    from websockets.exceptions import ConnectionClosed
    HAS_WS = True
except ImportError:
    websockets = None
    HAS_WS = False
    print("[CommanderV3] ⚠️ websockets 未安装，WebSocket 不可用")


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

DEFAULT_WS_PORT = int(os.environ.get("WS_PORT", "3398"))
DEFAULT_WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
DEFAULT_HTTP_PORT = int(os.environ.get("HTTP_PORT", "3399"))
DEFAULT_REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
DEFAULT_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
DEFAULT_REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
DEFAULT_STORE_PATH = os.environ.get("YAXIO_DB", "/opt/commander/data/yaxiio.db")

WS_PING_INTERVAL = 15   # WebSocket 原生 ping 间隔 (秒)
WS_PING_TIMEOUT = 10    # WebSocket 原生 pong 超时 (秒)
WS_MAX_CONNECTIONS = int(os.environ.get("WS_MAX_CONNECTIONS", "100"))
CLIENT_HEARTBEAT_INTERVAL = 30  # 应用层心跳间隔


# ═══════════════════════════════════════════════════════════════
# CommanderV3
# ═══════════════════════════════════════════════════════════════

class CommanderV3:
    """多Agent总指挥 V3 — 会话与连接分离。

    架构:
      ┌────────────────────────────────────────┐
      │           CommanderV3                   │
      │  ┌──────────┐  ┌────────────────────┐  │
      │  │CommanderV2│  │  SessionManager     │  │
      │  │(6引擎+A2A)│  │  (会话/离线/历史)    │  │
      │  └─────┬────┘  └─────────┬──────────┘  │
      │        │                 │              │
      │  ┌─────▼─────────────────▼──────────┐  │
      │  │        SessionBridge             │  │
      │  │   (SessionManager ↔ WebSocket)   │  │
      │  └──────────────┬───────────────────┘  │
      │                 │                      │
      │  ┌──────────────▼───────────────────┐  │
      │  │    WebSocket Server (:3398)      │  │
      │  │    + HTTP Heartbeat (:3399)      │  │
      │  └──────────────────────────────────┘  │
      └────────────────────────────────────────┘
    """

    def __init__(self,
                 ws_port: int = DEFAULT_WS_PORT,
                 ws_host: str = DEFAULT_WS_HOST,
                 http_port: int = DEFAULT_HTTP_PORT,
                 redis_host: str = DEFAULT_REDIS_HOST,
                 redis_port: int = DEFAULT_REDIS_PORT,
                 redis_password: str = None,
                 store_path: str = DEFAULT_STORE_PATH,
                 llm_api_key: str = None,
                 llm_base_url: str = None,
                 llm_model: str = "deepseek-chat"):
        self.ws_port = ws_port
        self.ws_host = ws_host
        self.http_port = http_port
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_password = redis_password or DEFAULT_REDIS_PASSWORD
        self.store_path = store_path

        # ── SessionManager ──
        self.session_mgr = SessionManager(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_password=redis_password,
            store_path=store_path,
        )

        # ── SessionBridge ──
        self.session_bridge: Optional[SessionBridge] = None

        # ── CommanderV2 ──
        self.commander = CommanderV2(
            redis_host=redis_host,
            redis_port=redis_port,
            redis_password=redis_password,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
        )

        # ── 运行时状态 ──
        self._running = False
        self._ws_server = None
        self._http_server = None
        self._tasks: Set[asyncio.Task] = set()
        self._start_time = time.time()

    # ═══════════════════════════════════════════════════════════
    # 启动
    # ═══════════════════════════════════════════════════════════

    async def start(self):
        """启动 Commander V3 全部服务。"""
        print("[CommanderV3] ⚡ 启动 V3 架构...")

        # 1. 初始化 SessionManager
        await self.session_mgr.initialize()
        self.session_bridge = SessionBridge(self.session_mgr)

        # 2. 启动 WebSocket 服务器
        if HAS_WS:
            self._ws_server = await ws_serve(
                self._handle_ws_client,
                self.ws_host,
                self.ws_port,
                ping_interval=WS_PING_INTERVAL,
                ping_timeout=WS_PING_TIMEOUT,
                max_size=10 * 1024 * 1024,  # 10MB max message
            )
            print(f"[CommanderV3] 🌉 WebSocket 服务: ws://{self.ws_host}:{self.ws_port}")
        else:
            print("[CommanderV3] ⚠️ WebSocket 不可用，仅 HTTP 模式")

        # 3. 启动 HTTP 心跳/管理服务
        self._http_task = asyncio.create_task(self._run_http_server())
        self._tasks.add(self._http_task)

        # 4. 启动定时任务
        self._tasks.add(asyncio.create_task(self._run_periodic_tasks()))

        # 5. 启动 CommanderV2 Pub/Sub 循环（后台线程）
        self._tasks.add(asyncio.create_task(self._run_commander_v2_loop()))

        self._running = True
        print(f"[CommanderV3] ✅ 全部服务就绪 (WebSocket:{self.ws_port}, HTTP:{self.http_port})")

    async def stop(self):
        """优雅关闭。"""
        print("[CommanderV3] 🛑 正在关闭...")
        self._running = False

        # 关闭 WebSocket
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()

        # 取消后台任务
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # 关闭 CommanderV2
        self.commander.shutdown()

        # 关闭 SessionManager
        await self.session_mgr.close()

        print("[CommanderV3] 👋 已关闭")

    # ═══════════════════════════════════════════════════════════
    # WebSocket 客户端处理
    # ═══════════════════════════════════════════════════════════

    async def _handle_ws_client(self, websocket):
        """处理单个 WebSocket 客户端（会话感知）。"""
        client_id = SessionManager.generate_client_id()
        session_token = None

        try:
            print(f"[CommanderV3] 🔗 新连接 {client_id}")

            # 发送连接确认
            await self._ws_send(websocket, {
                "type": "hello",
                "client_id": client_id,
                "version": "3.0.0",
                "server_time": time.time(),
            })

            # 消息循环
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    action = msg.get("type", msg.get("action", ""))

                    if action == "register":
                        result = await self._handle_ws_register(msg, websocket, client_id)
                        if result.get("type") == "registered":
                            session_token = result.get("session_token")
                            await self.session_bridge.register_connection(
                                session_token, client_id, websocket)

                    elif action == "connect":
                        result = await self._handle_ws_connect(msg, websocket, client_id)
                        if result.get("type") == "connected":
                            session_token = result.get("session_token")
                            await self.session_bridge.register_connection(
                                session_token, client_id, websocket)

                    elif action == "heartbeat":
                        if session_token:
                            await self.session_mgr.client_heartbeat(
                                session_token, client_id)
                        await self._ws_send(websocket, {"type": "heartbeat_ack"})

                    elif action == "dispatch":
                        await self._handle_ws_dispatch(msg, websocket, client_id, session_token)

                    elif action == "destroy":
                        if session_token:
                            await self.session_mgr.destroy_session(session_token)
                            await self.session_bridge.disconnect_all_clients(session_token)
                            await self._ws_send(websocket, {
                                "type": "destroyed",
                                "session_token": session_token,
                            })
                        session_token = None

                    elif action == "ping":
                        await self._ws_send(websocket, {"type": "pong", "time": time.time()})

                    elif action == "history":
                        await self._handle_ws_history(msg, websocket, session_token)

                    elif action == "status":
                        await self._handle_ws_status(websocket, session_token)

                    else:
                        await self._ws_send(websocket, {
                            "type": "error",
                            "code": "unknown_action",
                            "message": f"未知操作: {action}",
                        })

                except json.JSONDecodeError:
                    await self._ws_send(websocket, {
                        "type": "error", "code": "invalid_json",
                        "message": "无效的 JSON 格式"
                    })

        except ConnectionClosed:
            pass
        except Exception as e:
            print(f"[CommanderV3] ⚠️ WebSocket 异常 ({client_id}): {e}")
        finally:
            # 清理连接
            if session_token:
                await self.session_bridge.unregister_connection(session_token, client_id)
            print(f"[CommanderV3] 🔌 断开 {client_id} (session={session_token})")

    # ═══════════════════════════════════════════════════════════
    # WebSocket 协议处理
    # ═══════════════════════════════════════════════════════════

    async def _handle_ws_register(self, msg: dict, ws, client_id: str) -> dict:
        """处理注册请求。"""
        client_type = msg.get("client_type", "browser")
        fingerprint = msg.get("fingerprint", "")
        user_tier = msg.get("user_tier", "free")
        metadata = msg.get("metadata", {})

        result = await self.session_mgr.register_session(
            client_fingerprint=fingerprint,
            client_type=client_type,
            user_tier=user_tier,
            metadata=metadata,
        )

        # 覆盖 client_id 为当前连接
        result["client_id"] = client_id

        await self._ws_send(ws, result)
        return result

    async def _handle_ws_connect(self, msg: dict, ws, client_id: str) -> dict:
        """处理重连/新端接入请求。"""
        session_token = msg.get("session_token", "")
        fingerprint = msg.get("fingerprint", "")
        last_seq = msg.get("last_seq", 0)

        result = await self.session_mgr.connect_client(
            session_token=session_token,
            client_id=client_id,
            client_fingerprint=fingerprint,
            last_seq=last_seq,
        )

        if result.get("type") == "connected":
            # 一次性吐出离线消息
            offline_msgs = result.get("offline_messages", [])
            if offline_msgs:
                # 分批发送，每批最多 50 条
                batch_size = 50
                for i in range(0, len(offline_msgs), batch_size):
                    batch = offline_msgs[i:i + batch_size]
                    await self._ws_send(ws, {
                        "type": "offline_batch",
                        "messages": batch,
                        "batch": i // batch_size + 1,
                        "total_batches": (len(offline_msgs) + batch_size - 1) // batch_size,
                    })

            # 发送历史
            history = result.get("history", [])
            if history:
                await self._ws_send(ws, {
                    "type": "history_sync",
                    "messages": history,
                    "count": len(history),
                })

            # 发送连接确认
            await self._ws_send(ws, {
                "type": "connected",
                "session_token": session_token,
                "client_id": client_id,
                "current_seq": result.get("current_seq", 0),
                "session_status": result.get("session_status", "active"),
                "user_tier": result.get("user_tier", "free"),
            })

        else:
            # 错误
            await self._ws_send(ws, result)

        return result

    async def _handle_ws_dispatch(self, msg: dict, ws, client_id: str,
                                   session_token: str):
        """处理任务派发请求。"""
        target = msg.get("target", "commander")
        task = msg.get("task", "")
        correlation_id = msg.get("correlation_id", uuid.uuid4().hex[:16])

        if not task:
            await self._ws_send(ws, {
                "type": "task_error",
                "correlation_id": correlation_id,
                "error": "缺少 task 参数",
            })
            return

        # 通过 SessionManager 记录用户请求
        if session_token:
            await self.session_mgr.send_to_session(
                session_token,
                {"type": "user_request", "data": {"target": target, "task": task}},
                store_history=True,
            )

        # 构造 CommanderV2 任务
        task_data = {
            "from": client_id,
            "to": target,
            "type": "task",
            "taskId": correlation_id,
            "session_token": session_token,
            "payload": task if isinstance(task, dict) else {"message": str(task)},
        }

        # 通过 CommanderV2 处理任务
        try:
            import redis as redis_lib
            r = redis_lib.Redis(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                decode_responses=True,
            )
            # 发布到 Commander 处理频道
            r.publish("lightingmetal:agent:commander", json.dumps(task_data, ensure_ascii=False))

            await self._ws_send(ws, {
                "type": "task_accepted",
                "correlation_id": correlation_id,
                "target": target,
            })
        except Exception as e:
            await self._ws_send(ws, {
                "type": "task_error",
                "correlation_id": correlation_id,
                "error": f"任务派发失败: {e}",
            })

    async def _handle_ws_history(self, msg: dict, ws, session_token: str):
        """处理历史查询请求。"""
        if not session_token:
            await self._ws_send(ws, {
                "type": "error",
                "code": "no_session",
                "message": "请先注册或连接会话",
            })
            return

        offset = msg.get("offset", 0)
        limit = msg.get("limit", 50)
        before_seq = msg.get("before_seq")

        result = await self.session_mgr.get_history(
            session_token, offset=offset, limit=limit, before_seq=before_seq
        )

        await self._ws_send(ws, {
            "type": "history",
            **result,
        })

    async def _handle_ws_status(self, ws, session_token: str):
        """处理状态查询请求。"""
        result = {
            "type": "status",
            "server_uptime": int(time.time() - self._start_time),
            "commander": self.commander.get_status() if hasattr(self.commander, 'get_status') else {},
        }

        if session_token:
            result["session"] = await self.session_mgr.get_session_online_status(session_token)

        await self._ws_send(ws, result)

    # ═══════════════════════════════════════════════════════════
    # HTTP 服务器（心跳 + 管理 API）
    # ═══════════════════════════════════════════════════════════

    async def _run_http_server(self):
        """启动 HTTP 服务器。"""
        from aiohttp import web

        app = web.Application()

        # ── 会话管理 API ──
        async def api_register(request):
            data = await request.json()
            result = await self.session_mgr.register_session(
                client_fingerprint=data.get("fingerprint", ""),
                client_type=data.get("client_type", "browser"),
                user_tier=data.get("user_tier", "free"),
                metadata=data.get("metadata", {}),
            )
            return web.json_response(result)

        async def api_connect(request):
            data = await request.json()
            result = await self.session_mgr.connect_client(
                session_token=data.get("session_token", ""),
                client_id=data.get("client_id"),
                client_fingerprint=data.get("fingerprint", ""),
                last_seq=data.get("last_seq", 0),
            )
            return web.json_response(result)

        async def api_destroy(request):
            data = await request.json()
            result = await self.session_mgr.destroy_session(
                data.get("session_token", "")
            )
            if self.session_bridge:
                await self.session_bridge.disconnect_all_clients(
                    data.get("session_token", ""))
            return web.json_response(result)

        async def api_heartbeat(request):
            data = await request.json()
            result = await self.session_mgr.client_heartbeat(
                data.get("session_token", ""),
                data.get("client_id", ""),
            )
            return web.json_response(result)

        async def api_status(request):
            session_token = request.query.get("session_token", "")
            if session_token:
                result = await self.session_mgr.get_session_online_status(session_token)
            else:
                sessions = await self.session_mgr.list_active_sessions()
                result = {
                    "active_sessions": len(sessions),
                    "sessions": sessions,
                    "server_uptime": int(time.time() - self._start_time),
                }
            return web.json_response(result)

        async def api_queue_stats(request):
            session_token = request.query.get("session_token", "")
            if not session_token:
                return web.json_response({"error": "缺少 session_token"}, status=400)
            result = await self.session_mgr.get_queue_stats(session_token)
            return web.json_response(result)

        async def api_history(request):
            session_token = request.query.get("session_token", "")
            if not session_token:
                return web.json_response({"error": "缺少 session_token"}, status=400)
            offset = int(request.query.get("offset", 0))
            limit = int(request.query.get("limit", 50))
            result = await self.session_mgr.get_history(
                session_token, offset=offset, limit=limit)
            return web.json_response(result)

        # ── 健康检查 ──
        async def health(request):
            return web.json_response({
                "status": "ok",
                "version": "3.0.0",
                "uptime": int(time.time() - self._start_time),
            })

        # 路由
        # ── 可观测性端点 ──
        async def metrics(request):
            try:
                import redis
                r = redis.Redis(host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD",""), protocol=2, decode_responses=True, socket_connect_timeout=2)
                return web.json_response({
                    "commander": bool(r.get("yaxiio:commander:lock")),
                    "guardian": bool(r.get("yaxiio:guardian:leader")),
                    "active_tasks": r.scard("yaxiio:task:active") or 0,
                    "constitution_checks": int(r.get("yaxiio:constitution:total") or 0),
                    "uptime_seconds": int(time.time() - self._start_time),
                })
            except:
                return web.json_response({"error": "Redis unavailable"}, status=503)

        async def trace_logs(request):
            trace_id = request.match_info.get("trace_id", "")
            if not trace_id:
                return web.json_response({"error": "trace_id required"}, status=400)
            try:
                from trace_logger import query_trace_logs
                logs = query_trace_logs(trace_id)
                return web.json_response({"trace_id": trace_id, "count": len(logs), "logs": logs})
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        async def health_detailed(request):
            try:
                import redis
                r = redis.Redis(host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD",""), protocol=2, decode_responses=True, socket_connect_timeout=2)
                redis_ok = r.ping()
            except:
                redis_ok = False
            return web.json_response({
                "status": "ok" if redis_ok else "degraded",
                "redis": redis_ok,
                "ws_port": self.ws_port,
                "http_port": self.http_port,
                "uptime_seconds": int(time.time() - self._start_time),
            })

        app.router.add_post("/api/v3/register", api_register)
        app.router.add_post("/api/v3/connect", api_connect)
        app.router.add_post("/api/v3/destroy", api_destroy)
        app.router.add_post("/api/v3/heartbeat", api_heartbeat)
        app.router.add_get("/api/v3/status", api_status)
        app.router.add_get("/api/v3/queue", api_queue_stats)
        app.router.add_get("/api/v3/history", api_history)
        app.router.add_get("/health", health)
        app.router.add_get("/metrics", metrics)
        app.router.add_get("/trace/{trace_id}", trace_logs)
        app.router.add_get("/health", health_detailed)
        app.router.add_get("/health-old", health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.http_port)
        await site.start()

        print(f"[CommanderV3] 🌐 HTTP API: http://0.0.0.0:{self.http_port}")

        # 保持运行
        while self._running:
            await asyncio.sleep(1)

    # ═══════════════════════════════════════════════════════════
    # 定时任务
    # ═══════════════════════════════════════════════════════════

    async def _run_periodic_tasks(self):
        """定时任务：过期检查 + 心跳监控 + 状态上报。"""
        while self._running:
            try:
                # 每小时检查过期会话
                result = await self.session_mgr.check_expired_sessions()
                if result.get("archived", 0) > 0:
                    print(f"[CommanderV3] 📦 归档了 {result['archived']} 个过期会话")

                # 每 5 分钟报告状态
                queue_sizes = []
                sessions = await self.session_mgr.list_active_sessions()
                for s in sessions:
                    stats = await self.session_mgr.get_queue_stats(s["session_token"])
                    queue_sizes.append(stats.get("offline_queue_size", 0))

                total_queued = sum(queue_sizes)
                if total_queued > 0:
                    print(f"[CommanderV3] 📊 {len(sessions)} 活跃会话, "
                          f"离线队列总计 {total_queued} 条消息")

            except Exception as e:
                print(f"[CommanderV3] ⚠️ 定时任务异常: {e}")

            await asyncio.sleep(300)  # 5 分钟

    # ═══════════════════════════════════════════════════════════
    # CommanderV2 Pub/Sub 循环
    # ═══════════════════════════════════════════════════════════

    async def _run_commander_v2_loop(self):
        """在异步上下文中运行 CommanderV2 的 Redis Pub/Sub 主循环。

        拦截任务结果，通过 SessionManager 推送给客户端。
        """
        import redis as redis_lib

        r = redis_lib.Redis(
            host=self.redis_host,
            port=self.redis_port,
            password=self.redis_password,
            decode_responses=True,
        )

        pubsub = r.pubsub()
        pubsub.subscribe("lightingmetal:agent:commander", "agent:result")

        print("[CommanderV3] 👂 监听 Redis Pub/Sub (commander + agent:result)")

        last_eval = time.time()

        for message in pubsub.listen():
            if not self._running:
                break

            if message["type"] != "message":
                continue

            try:
                data = json.loads(message["data"])
            except json.JSONDecodeError:
                continue

            channel = message.get("channel", "")

            # ── Commander 指令频道 ──
            if channel == "lightingmetal:agent:commander":
                # 交给 CommanderV2 处理
                self.commander.handle_pubsub_message(data)

                # 如果有 session_token，将处理结果通过 SessionManager 发送
                session_token = data.get("session_token")
                if session_token:
                    await self.session_mgr.send_to_session(
                        session_token,
                        {
                            "type": "task_processed",
                            "taskId": data.get("taskId", ""),
                            "from": data.get("from", "unknown"),
                        },
                        store_history=True,
                    )

            # ── Agent 结果频道 ──
            elif channel == "agent:result":
                # 转发给相关会话
                session_token = data.get("session_token")
                if session_token:
                    msg_type = "task_result" if "result" in data else "agent_message"
                    message_data = {
                        "type": msg_type,
                        "from": data.get("from", "unknown"),
                        "taskId": data.get("taskId", ""),
                        "data": data.get("result", data),
                    }
                    await self.session_mgr.send_to_session(
                        session_token,
                        message_data,
                        store_history=True,
                    )

                    # 在线客户端 → 通过 SessionBridge 推送
                    if self.session_bridge:
                        await self.session_bridge.push_to_session(
                            session_token, message_data)

            # 定时评估（每小时）
            if time.time() - last_eval > 3600:
                self.commander.run_daily_evaluation()
                last_eval = time.time()

        pubsub.close()

    # ═══════════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    async def _ws_send(ws, data: dict):
        """安全发送 JSON 到 WebSocket。"""
        try:
            await ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

async def main():
    """Commander V3 主入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="Commander V3 — 会话与连接分离")
    parser.add_argument("--ws-port", type=int, default=DEFAULT_WS_PORT,
                        help=f"WebSocket 端口 (默认: {DEFAULT_WS_PORT})")
    parser.add_argument("--ws-host", default=DEFAULT_WS_HOST,
                        help=f"WebSocket 主机 (默认: {DEFAULT_WS_HOST})")
    parser.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT,
                        help=f"HTTP 端口 (默认: {DEFAULT_HTTP_PORT})")
    parser.add_argument("--redis-host", default=DEFAULT_REDIS_HOST)
    parser.add_argument("--redis-port", type=int, default=DEFAULT_REDIS_PORT)
    parser.add_argument("--redis-password", default=os.environ.get("REDIS_PASSWORD", ""))
    parser.add_argument("--mongo-uri", default=DEFAULT_STORE_PATH)
    parser.add_argument("--store-path", default=DEFAULT_STORE_PATH)
    parser.add_argument("--llm-api-key", default=os.environ.get("DEEPSEEK_API_KEY", ""))
    parser.add_argument("--llm-base-url", default=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    parser.add_argument("--llm-model", default=os.environ.get("LLM_MODEL", "deepseek-chat"))
    args = parser.parse_args()

    # 检查依赖
    try:
        import aiohttp
    except ImportError:
        print("[CommanderV3] ❌ aiohttp 未安装，请执行: pip3 install aiohttp")
        sys.exit(1)

    if not HAS_WS:
        print("[CommanderV3] ❌ websockets 未安装，请执行: pip3 install websockets")
        sys.exit(1)

    commander = CommanderV3(
        ws_port=args.ws_port,
        ws_host=args.ws_host,
        http_port=args.http_port,
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
        store_path=args.store_path,
        llm_api_key=args.llm_api_key,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
    )

    # 优雅关闭
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(commander.stop()))
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    try:
        await commander.start()
        # 永久运行
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await commander.stop()


if __name__ == "__main__":
    asyncio.run(main())
