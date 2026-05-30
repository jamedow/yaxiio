"""
AsyncOrchestrator — 异步事件驱动的子任务编排器
================================================
替代 workflow_engine._orchestrate_subtasks 的线程池方案。

核心改进:
  1. asyncio 事件驱动 — 不再阻塞线程等待 HTTP 响应
  2. 优先级队列 (heapq) — CRITICAL/HIGH/MEDIUM/LOW 四级
  3. 依赖图回调 — 子任务完成自动解锁后继（O(log n) 堆操作）
  4. 并发控制 — max_concurrent 限制同时执行的子任务数
  5. 超时 + 重试 — per-subtask 可配置

环境变量:
  YAXIIO_MAX_CONCURRENT: 最大并发子任务数 (默认 10)
  YAXIIO_TASK_TIMEOUT: 整体超时秒数 (默认 600)
  YAXIIO_SUBTASK_TIMEOUT: 单个子任务超时秒数 (默认 120)
"""
import asyncio
import heapq
import json
import os
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Set, Optional


class SubtaskPriority(IntEnum):
    """子任务优先级（数值越小越优先）"""
    CRITICAL = 0   # 安全修复、宪法违规修复、系统崩溃恢复
    HIGH = 1       # 核心业务、用户直接请求
    MEDIUM = 2     # 常规任务
    LOW = 3        # 优化、美化、非紧急


@dataclass(order=True)
class _QueueItem:
    """优先队列条目"""
    priority: int
    sid: str = field(compare=False)
    agent: str = field(compare=False)
    action: str = field(compare=False)
    depends: List[str] = field(compare=False)
    prompt: str = field(compare=False)
    submitted_at: float = field(compare=False)
    retry_count: int = field(compare=False)
    max_retries: int = field(compare=False)


class AsyncOrchestrator:
    """异步事件驱动的子任务编排器"""

    def __init__(self, commander=None, max_concurrent: int = None,
                 total_timeout: float = None, subtask_timeout: float = None):
        self.commander = commander
        self.max_concurrent = max_concurrent or int(
            os.environ.get("YAXIIO_MAX_CONCURRENT", "10"))
        self.total_timeout = total_timeout or float(
            os.environ.get("YAXIIO_TASK_TIMEOUT", "600"))
        self.subtask_timeout = subtask_timeout or float(
            os.environ.get("YAXIIO_SUBTASK_TIMEOUT", "120"))

        # 运行时状态
        self._ready_queue: List[_QueueItem] = []
        self._running: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, dict] = {}
        self._subtask_map: Dict[str, dict] = {}
        self._dep_graph: Dict[str, Set[str]] = {}
        self._pending_deps: Dict[str, int] = {}

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    async def execute(self, task_id: str, subtasks: List[dict],
                      payload: dict = None) -> Dict[str, dict]:
        """
        异步执行所有子任务

        Args:
            task_id: 父任务 ID
            subtasks: [{"id","agent","action","depends","prompt","priority"}]
            payload: 原始任务 payload

        Returns:
            {sid: {"ok": True/False, "output": "...", "agent": "...", "elapsed_ms": 123}}
        """
        self._reset()
        for st in subtasks:
            self._subtask_map[st["id"]] = st
        self._build_dep_graph(subtasks)

        # 入队无依赖子任务
        for st in subtasks:
            if not st.get("depends"):
                priority = self._resolve_priority(st)
                heapq.heappush(self._ready_queue, _QueueItem(
                    priority=priority.value,
                    sid=st["id"], agent=st["agent"],
                    action=st.get("action", ""),
                    depends=[], prompt=st.get("prompt", ""),
                    submitted_at=time.time(), retry_count=0,
                    max_retries=st.get("max_retries", 2),
                ))

        # 事件驱动主循环
        try:
            async with asyncio.timeout(self.total_timeout):
                await self._event_loop(task_id)
        except asyncio.TimeoutError:
            for sid in list(self._running.keys()):
                if sid not in self._results:
                    self._results[sid] = {
                        "ok": False,
                        "error": f"orchestrator timeout ({self.total_timeout}s)",
                        "agent": self._subtask_map.get(sid, {}).get("agent", "?"),
                    }

        # 标记未处理子任务
        for st in subtasks:
            if st["id"] not in self._results:
                self._results[st["id"]] = {
                    "ok": False,
                    "error": "not executed (dependency failed or timeout)",
                    "agent": st.get("agent", "?"),
                }

        return dict(self._results)

    # ═══════════════════════════════════════════════
    # 事件循环
    # ═══════════════════════════════════════════════

    async def _event_loop(self, task_id: str):
        """主事件循环"""
        total = len(self._subtask_map)

        while len(self._results) < total:
            # 启动就绪任务（直到达到并发上限）
            while self._ready_queue and len(self._running) < self.max_concurrent:
                qi = heapq.heappop(self._ready_queue)
                if qi.sid in self._results:
                    continue
                coro = self._run_subtask(task_id, qi)
                t = asyncio.create_task(coro)
                self._running[qi.sid] = t
                t.add_done_callback(
                    lambda fut, sid=qi.sid: asyncio.ensure_future(
                        self._on_done(task_id, sid, fut)
                    )
                )

            if not self._running:
                break

            # 等待任意一个完成（最多 5 秒，然后重新检查就绪队列）
            done, _pending = await asyncio.wait(
                list(self._running.values()),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=5.0,
            )

            for t in done:
                sid = None
                for s, task in list(self._running.items()):
                    if task is t:
                        sid = s
                        break
                if sid and sid in self._running and sid not in self._results:
                    try:
                        result = t.result()
                        self._results[sid] = result
                    except Exception as e:
                        self._results[sid] = {
                            "ok": False,
                            "error": str(e)[:200],
                            "agent": self._subtask_map.get(sid, {}).get("agent", "?"),
                        }
                    await self._unlock_dependents(task_id, sid)
                    if sid in self._running:
                        del self._running[sid]

    # ═══════════════════════════════════════════════
    # 子任务执行
    # ═══════════════════════════════════════════════

    async def _run_subtask(self, task_id: str, qi: _QueueItem) -> dict:
        """异步执行单个子任务（带超时和重试）"""
        try:
            async with asyncio.timeout(self.subtask_timeout):
                result = await self._dispatch(
                    agent_name=qi.agent, task_id=task_id,
                    sid=qi.sid, action=qi.action, prompt=qi.prompt,
                )
                return {
                    "ok": result.get("ok", False),
                    "output": str(result.get("output", result.get("error", "")))[:5000],
                    "agent": qi.agent,
                    "elapsed_ms": result.get("elapsed_ms", 0),
                    "_retried": qi.retry_count > 0,
                }
        except asyncio.TimeoutError:
            if qi.retry_count < qi.max_retries:
                qi.retry_count += 1
                qi.priority = max(qi.priority, SubtaskPriority.HIGH.value)
                heapq.heappush(self._ready_queue, qi)
                return {
                    "ok": False,
                    "error": f"timeout, retrying ({qi.retry_count}/{qi.max_retries})",
                    "agent": qi.agent,
                    "_retrying": True,
                }
            return {
                "ok": False,
                "error": f"timeout after {qi.max_retries} retries",
                "agent": qi.agent,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)[:500], "agent": qi.agent}

    async def _dispatch(self, agent_name: str, task_id: str,
                        sid: str, action: str, prompt: str) -> dict:
        """异步分发到 L4 执行层 — Stream 优先 + Pub/Sub 回退"""
        msg = {
            "type": "task", "taskId": task_id, "sid": sid,
            "from": "orchestrator", "to": agent_name,
            "replyTo": "lightingmetal:agent:commander",
            "payload": {
                "action": action, "prompt": prompt,
                "task_id": task_id, "sid": sid,
            },
        }

        # 优先 Stream (消息持久化，不丢失)
        try:
            from stream_bridge import StreamBridge
            _bridge = StreamBridge(
                redis_host="127.0.0.1", redis_port=6379,
                redis_password=os.environ.get("REDIS_PASSWORD", ""))
            _bridge.publish_task("L4", msg, task_id)
            print(f"[AsyncOrch] {task_id}/{sid} Stream 发布 → L4", flush=True)
        except Exception as e:
            print(f"[AsyncOrch] Stream 发布失败: {e}", flush=True)

        # Pub/Sub 回退
        if self.commander and self.commander.redis:
            try:
                channel = f"lightingmetal:agent:{agent_name}"
                self.commander.redis.publish(
                    channel, json.dumps(msg, ensure_ascii=False, default=str)
                )
            except Exception:
                pass

        # 等待响应 (Stream 双通道)
        result = await self._wait_neuron(task_id, sid, agent_name)
        if result:
            return result

        # HTTP 降级: aiohttp
        try:
            import aiohttp
            url = f"http://127.0.0.1:3404/jsonrpc"
            payload = {
                "method": "dispatch_and_await",
                "params": {
                    "agent_name": agent_name, "task_id": task_id,
                    "sid": sid, "action": action, "prompt": prompt,
                    "timeout": int(self.subtask_timeout - 5),
                },
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.subtask_timeout),
                ) as resp:
                    return await resp.json()
        except Exception as e:
            return {"ok": False, "error": f"dispatch failed: {e}"}

    async def _wait_neuron(self, task_id: str, sid: str,
                           agent_name: str) -> Optional[dict]:
        """Stream + Pub/Sub 双重等待 Neuron 响应"""
        start = time.time()

        # Stream 响应通道
        try:
            from stream_bridge import StreamBridge
            _bridge = StreamBridge(
                redis_host="127.0.0.1", redis_port=6379,
                redis_password=os.environ.get("REDIS_PASSWORD", ""))
            _response_stream = "yaxiio:stream:L4_response"
            _response_group = "commander-response"
            _bridge.ensure_group(_response_stream, _response_group)
        except Exception:
            _bridge = None

        # Pub/Sub 回退
        pubsub = None
        try:
            import redis as _redis
            r = _redis.Redis(
                host="127.0.0.1", port=6379,
                password=os.environ.get("REDIS_PASSWORD", ""),
                decode_responses=True, socket_connect_timeout=3,
            )
            pubsub = r.pubsub()
            pubsub.subscribe("lightingmetal:agent:commander")
        except Exception:
            pass

        try:
            while time.time() - start < self.subtask_timeout:
                # 1. Stream 优先
                if _bridge:
                    try:
                        results = _bridge.r.xreadgroup(
                            groupname=_response_group,
                            consumername=f"orchestrator-{task_id}",
                            streams={_response_stream: ">"},
                            block=500, count=10)
                        if results:
                            for stream_name, messages in results:
                                for msg_id, fields in messages:
                                    data = json.loads(fields.get("payload", "{}"))
                                    _bridge.r.xack(_response_stream, _response_group, msg_id)
                                    if (data.get("taskId") == task_id
                                            and data.get("sid", data.get("payload", {}).get("sid")) == sid):
                                        payload = data.get("payload", data)
                                        elapsed = int((time.time() - start) * 1000)
                                        return {
                                            "ok": payload.get("status") in ("success", "completed"),
                                            "output": str(payload.get("thought", payload.get("result", "")))[:5000],
                                            "elapsed_ms": elapsed,
                                        }
                    except Exception:
                        pass

                # 2. Pub/Sub 回退
                if pubsub:
                    msg = pubsub.get_message(timeout=0.5)
                    if msg and msg.get("type") == "message":
                        try:
                            data = json.loads(msg["data"])
                        except json.JSONDecodeError:
                            await asyncio.sleep(0.1)
                            continue
                        if (data.get("taskId") == task_id
                                and data.get("sid") == sid
                                and data.get("type") in ("response", "result")):
                            payload = data.get("payload", {})
                            elapsed = int((time.time() - start) * 1000)
                            return {
                                "ok": payload.get("status") in ("success", "completed"),
                                "output": str(payload.get("thought", payload.get("result", "")))[:5000],
                                "elapsed_ms": elapsed,
                            }

                await asyncio.sleep(0.1)

        except Exception:
            pass
        finally:
            if pubsub:
                try:
                    pubsub.close()
                except Exception:
                    pass
        return None

    # ═══════════════════════════════════════════════
    # 依赖管理
    # ═══════════════════════════════════════════════

    async def _on_done(self, task_id: str, sid: str, future: asyncio.Task):
        """子任务完成回调"""
        try:
            result = future.result()
        except Exception as e:
            result = {"ok": False, "error": str(e)[:200],
                      "agent": self._subtask_map.get(sid, {}).get("agent", "?")}

        if result.get("_retrying"):
            if sid in self._running:
                del self._running[sid]
            return

        self._results[sid] = result

        # 失败重试
        if (not result.get("ok") and not result.get("_retried")
                and self._subtask_map.get(sid, {}).get("max_retries", 2) > 0):
            st = self._subtask_map.get(sid, {})
            qi = _QueueItem(
                priority=SubtaskPriority.HIGH.value,
                sid=sid, agent=st.get("agent", "?"),
                action=st.get("action", ""),
                depends=st.get("depends", []),
                prompt=st.get("prompt", ""),
                submitted_at=time.time(), retry_count=1,
                max_retries=st.get("max_retries", 2),
            )
            heapq.heappush(self._ready_queue, qi)
            if sid in self._running:
                del self._running[sid]
            return

        if sid in self._running:
            del self._running[sid]

        await self._unlock_dependents(task_id, sid)

    async def _unlock_dependents(self, task_id: str, sid: str):
        """解锁所有依赖此子任务的后继任务"""
        dependents = self._dep_graph.get(sid, set())
        for dep_sid in dependents:
            if dep_sid in self._pending_deps:
                self._pending_deps[dep_sid] -= 1
                if self._pending_deps[dep_sid] == 0:
                    st = self._subtask_map.get(dep_sid)
                    if st:
                        priority = self._resolve_priority(st)
                        heapq.heappush(self._ready_queue, _QueueItem(
                            priority=priority.value,
                            sid=dep_sid, agent=st.get("agent", "?"),
                            action=st.get("action", ""),
                            depends=st.get("depends", []),
                            prompt=st.get("prompt", ""),
                            submitted_at=time.time(), retry_count=0,
                            max_retries=st.get("max_retries", 2),
                        ))

    def _build_dep_graph(self, subtasks: List[dict]):
        """构建反向依赖图: sid → {依赖它的 sid...}"""
        self._dep_graph.clear()
        self._pending_deps.clear()
        for st in subtasks:
            sid = st["id"]
            deps = st.get("depends", [])
            self._pending_deps[sid] = len(deps)
            for dep_sid in deps:
                if dep_sid not in self._dep_graph:
                    self._dep_graph[dep_sid] = set()
                self._dep_graph[dep_sid].add(sid)

    # ═══════════════════════════════════════════════
    # 优先级解析
    # ═══════════════════════════════════════════════

    def _resolve_priority(self, subtask: dict) -> SubtaskPriority:
        """根据子任务属性决定优先级"""
        explicit = subtask.get("priority", "").upper()
        if explicit in SubtaskPriority.__members__:
            return SubtaskPriority[explicit]

        combined = (
            str(subtask.get("action", "")) + " "
            + str(subtask.get("prompt", ""))
        ).lower()

        critical_kw = ["安全", "security", "修复漏洞", "crash", "紧急", "urgent", "宪法"]
        if any(kw in combined for kw in critical_kw):
            return SubtaskPriority.CRITICAL

        high_kw = ["审计", "audit", "翻译", "translate", "报价", "quote", "部署"]
        if any(kw in combined for kw in high_kw):
            return SubtaskPriority.HIGH

        low_kw = ["优化", "optimize", "美化", "样式", "style"]
        if any(kw in combined for kw in low_kw):
            return SubtaskPriority.LOW

        return SubtaskPriority.MEDIUM

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _reset(self):
        """重置所有运行时状态"""
        self._ready_queue.clear()
        self._running.clear()
        self._results.clear()
        self._subtask_map.clear()
        self._dep_graph.clear()
        self._pending_deps.clear()

    def stats(self) -> dict:
        """返回调度器统计"""
        return {
            "ready_queue_size": len(self._ready_queue),
            "running_count": len(self._running),
            "completed_count": len(self._results),
            "max_concurrent": self.max_concurrent,
            "total_timeout": self.total_timeout,
        }
