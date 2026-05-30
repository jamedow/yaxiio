# L3 调度层重构方案

> 版本: 1.0 | 日期: 2026-05-29
> 涉及文件: `modules/layer3/async_orchestrator.py` (新), `modules/layer3/redis_data_bus.py` (新)
> 修改文件: `workflow_engine.py`（`_orchestrate_subtasks`, `_execute_subtask`）, `modules/layer3/__init__.py`

---

## 一、当前问题

### 1.1 线程池 + 同步阻塞 = 伪并行

```python
# workflow_engine._orchestrate_subtasks — 当前代码
with ThreadPoolExecutor(max_workers=5) as executor:
    while len(completed) < len(subtasks):
        for st in ready:
            executor.submit(self._execute_subtask, ...)  # 同步阻塞 HTTP

# _execute_subtask 内部:
result = call_layer(4, "dispatch_and_await", ..., timeout=60)
# ↑ HTTP 同步调用，阻塞线程 60 秒
```

当 5 个 worker 线程全部阻塞在 HTTP 上时，第 6 个就绪子任务无法被调度。

### 1.2 拓扑排序是 O(n²) 线性扫描

每次循环全量扫描所有子任务检查依赖是否满足。

### 1.3 WorkflowSnapshot 用文件系统做数据中转

并行子任务之间走文件 IO，无并发保护。

### 1.4 无优先级队列

所有子任务一视同仁。"修复安全漏洞"和"优化加载速度"同等优先级。

---

## 二、目标架构

```
                    ┌─────────────────────────┐
                    │   AsyncOrchestrator      │
                    │   (asyncio 事件驱动)      │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
        ┌──────────┐     ┌──────────┐       ┌──────────┐
        │PriorityQueue│   │ Running  │       │  Done    │
        │ (就绪队列)   │   │ (执行中)  │       │ (完成)   │
        │ heapq 优先  │   │ asyncio  │       │ 结果收集 │
        └──────────┘     │ Task 池  │       └──────────┘
                         └──────────┘
                                │
                         ┌──────▼──────┐
                         │ RedisDataBus │
                         │ (Stream)     │
                         │ 替代文件快照  │
                         └─────────────┘
```

---

## 三、实现: AsyncOrchestrator

**新建文件**: `modules/layer3/async_orchestrator.py`

```python
"""
AsyncOrchestrator — 异步事件驱动的子任务编排器
================================================
替代 workflow_engine._orchestrate_subtasks 的线程池方案。

核心改进:
  1. asyncio 事件驱动 — 不再阻塞线程等待 HTTP 响应
  2. 优先级队列 — 关键任务优先执行
  3. 依赖图回调 — 子任务完成自动解锁后继
  4. 并发控制 — max_concurrent 限制同时执行的子任务数
"""
import asyncio
import json
import time
import heapq
import os
from dataclasses import dataclass, field
from typing import Dict, List, Set, Optional
from enum import IntEnum


class SubtaskPriority(IntEnum):
    """子任务优先级（数值越小越优先）"""
    CRITICAL = 0    # 安全修复、宪法违规修复、系统崩溃恢复
    HIGH = 1        # 核心业务、用户直接请求
    MEDIUM = 2      # 常规任务
    LOW = 3         # 优化、美化、非紧急任务


@dataclass(order=True)
class PrioritizedSubtask:
    """可入优先队列的子任务"""
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

    def __init__(self, commander=None, max_concurrent: int = 10,
                 total_timeout: float = 600.0, subtask_timeout: float = 120.0):
        self.commander = commander
        self.max_concurrent = max_concurrent
        self.total_timeout = total_timeout
        self.subtask_timeout = subtask_timeout

        # 运行时状态
        self._ready_queue: List[PrioritizedSubtask] = []
        self._running: Dict[str, asyncio.Task] = {}
        self._results: Dict[str, dict] = {}
        self._subtask_map: Dict[str, dict] = {}
        self._dep_graph: Dict[str, Set[str]] = {}
        self._pending_deps: Dict[str, int] = {}

    async def execute(self, task_id: str, subtasks: List[dict],
                      payload: dict = None) -> Dict[str, dict]:
        """异步执行所有子任务"""
        self._reset()
        for st in subtasks:
            self._subtask_map[st["id"]] = st

        self._build_dep_graph(subtasks)

        # 入队无依赖子任务
        for st in subtasks:
            if not st.get("depends"):
                priority = self._resolve_priority(st)
                heapq.heappush(self._ready_queue, PrioritizedSubtask(
                    priority=priority.value, sid=st["id"],
                    agent=st["agent"], action=st.get("action", ""),
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
                        "agent": self._subtask_map.get(sid, {}).get("agent", "?")
                    }

        # 标记未处理子任务
        for st in subtasks:
            if st["id"] not in self._results:
                self._results[st["id"]] = {
                    "ok": False, "error": "not executed (dependency failed or timeout)",
                    "agent": st.get("agent", "?")
                }

        return dict(self._results)

    async def _event_loop(self, task_id: str):
        """主事件循环"""
        total = len(self._subtask_map)
        while len(self._results) < total:
            # 启动就绪任务
            while self._ready_queue and len(self._running) < self.max_concurrent:
                ps = heapq.heappop(self._ready_queue)
                if ps.sid in self._results:
                    continue
                coro = self._run_subtask(task_id, ps)
                t = asyncio.create_task(coro)
                self._running[ps.sid] = t
                t.add_done_callback(
                    lambda fut, sid=ps.sid: asyncio.ensure_future(
                        self._on_subtask_done(task_id, sid, fut)
                    )
                )

            if not self._running:
                break

            done, pending = await asyncio.wait(
                list(self._running.values()),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=5.0
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
                            "ok": False, "error": str(e)[:200],
                            "agent": self._subtask_map.get(sid, {}).get("agent", "?")
                        }
                    await self._unlock_dependents(task_id, sid)
                    del self._running[sid]

    async def _run_subtask(self, task_id: str, ps: PrioritizedSubtask) -> dict:
        """异步执行单个子任务"""
        try:
            async with asyncio.timeout(self.subtask_timeout):
                result = await self._async_dispatch(
                    agent_name=ps.agent, task_id=task_id, sid=ps.sid,
                    action=ps.action, prompt=ps.prompt
                )
                return {
                    "ok": result.get("ok", False),
                    "output": str(result.get("output", result.get("error", "")))[:5000],
                    "agent": ps.agent,
                    "elapsed_ms": result.get("elapsed_ms", 0),
                    "_retried": ps.retry_count > 0,
                }
        except asyncio.TimeoutError:
            if ps.retry_count < ps.max_retries:
                ps.retry_count += 1
                ps.priority = max(ps.priority, SubtaskPriority.HIGH.value)
                heapq.heappush(self._ready_queue, ps)
                return {
                    "ok": False,
                    "error": f"timeout, retrying ({ps.retry_count}/{ps.max_retries})",
                    "agent": ps.agent, "_retrying": True,
                }
            return {"ok": False, "error": f"timeout after {ps.max_retries} retries", "agent": ps.agent}
        except Exception as e:
            return {"ok": False, "error": str(e)[:500], "agent": ps.agent}

    async def _async_dispatch(self, agent_name: str, task_id: str,
                               sid: str, action: str, prompt: str) -> dict:
        """异步分发到 L4 执行层"""
        import aiohttp

        # 优先 Redis Pub/Sub
        if self.commander and self.commander.redis:
            try:
                channel = f"lightingmetal:agent:{agent_name}"
                msg = {
                    "type": "task", "taskId": task_id, "sid": sid,
                    "from": "orchestrator", "to": agent_name,
                    "replyTo": "lightingmetal:agent:commander",
                    "payload": {"action": action, "prompt": prompt,
                                "task_id": task_id, "sid": sid}
                }
                self.commander.redis.publish(channel, json.dumps(msg, ensure_ascii=False, default=str))
                result = await self._wait_neuron_response(task_id, sid, agent_name, timeout=self.subtask_timeout)
                if result:
                    return result
            except Exception:
                pass

        # HTTP 降级
        url = f"http://127.0.0.1:3404/jsonrpc"
        payload = {
            "method": "dispatch_and_await",
            "params": {"agent_name": agent_name, "task_id": task_id,
                       "sid": sid, "action": action, "prompt": prompt,
                       "timeout": int(self.subtask_timeout - 5)}
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                         timeout=aiohttp.ClientTimeout(total=self.subtask_timeout)) as resp:
                    return await resp.json()
        except Exception as e:
            return {"ok": False, "error": f"HTTP dispatch failed: {e}"}

    async def _wait_neuron_response(self, task_id: str, sid: str,
                                     agent_name: str, timeout: float) -> Optional[dict]:
        """等待 Neuron 通过 Redis Pub/Sub 发布的结果"""
        if not self.commander or not self.commander.redis:
            return None
        start = time.time()
        try:
            import redis as _redis
            r = _redis.Redis(host="127.0.0.1", port=6379,
                            password=os.environ.get("REDIS_PASSWORD", ""),
                            decode_responses=True, socket_connect_timeout=3)
            pubsub = r.pubsub()
            pubsub.subscribe("lightingmetal:agent:commander")
            while time.time() - start < timeout:
                msg = pubsub.get_message(timeout=1.0)
                if not msg or msg.get("type") != "message":
                    await asyncio.sleep(0.1)
                    continue
                try:
                    data = json.loads(msg["data"])
                except json.JSONDecodeError:
                    continue
                if (data.get("taskId") == task_id and data.get("sid") == sid
                        and data.get("type") in ("response", "result")):
                    payload = data.get("payload", {})
                    elapsed = int((time.time() - start) * 1000)
                    return {
                        "ok": payload.get("status") in ("success", "completed"),
                        "output": str(payload.get("thought", payload.get("result", "")))[:5000],
                        "elapsed_ms": elapsed,
                    }
            pubsub.close()
        except Exception:
            pass
        return None

    async def _on_subtask_done(self, task_id: str, sid: str, future: asyncio.Task):
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
            ps = PrioritizedSubtask(
                priority=SubtaskPriority.HIGH.value, sid=sid,
                agent=st.get("agent", "?"), action=st.get("action", ""),
                depends=st.get("depends", []), prompt=st.get("prompt", ""),
                submitted_at=time.time(), retry_count=1,
                max_retries=st.get("max_retries", 2),
            )
            heapq.heappush(self._ready_queue, ps)
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
                        heapq.heappush(self._ready_queue, PrioritizedSubtask(
                            priority=priority.value, sid=dep_sid,
                            agent=st.get("agent", "?"), action=st.get("action", ""),
                            depends=st.get("depends", []), prompt=st.get("prompt", ""),
                            submitted_at=time.time(), retry_count=0,
                            max_retries=st.get("max_retries", 2),
                        ))

    def _build_dep_graph(self, subtasks: List[dict]):
        """构建反向依赖图"""
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

    def _resolve_priority(self, subtask: dict) -> SubtaskPriority:
        """根据子任务属性决定优先级"""
        explicit = subtask.get("priority", "").upper()
        if explicit in SubtaskPriority.__members__:
            return SubtaskPriority[explicit]
        action = str(subtask.get("action", "")).lower()
        prompt = str(subtask.get("prompt", "")).lower()
        combined = action + " " + prompt
        if any(kw in combined for kw in ["安全", "security", "修复漏洞", "crash", "紧急", "urgent", "宪法"]):
            return SubtaskPriority.CRITICAL
        if any(kw in combined for kw in ["审计", "audit", "翻译", "translate", "报价", "quote", "部署"]):
            return SubtaskPriority.HIGH
        if any(kw in combined for kw in ["优化", "optimize", "美化", "样式", "style"]):
            return SubtaskPriority.LOW
        return SubtaskPriority.MEDIUM

    def _reset(self):
        """重置所有运行时状态"""
        self._ready_queue.clear()
        self._running.clear()
        self._results.clear()
        self._subtask_map.clear()
        self._dep_graph.clear()
        self._pending_deps.clear()

    def stats(self) -> dict:
        return {
            "ready_queue_size": len(self._ready_queue),
            "running_count": len(self._running),
            "completed_count": len(self._results),
            "max_concurrent": self.max_concurrent,
        }
```

---

## 四、实现: RedisDataBus

**新建文件**: `modules/layer3/redis_data_bus.py`

```python
"""
RedisDataBus — L3 数据中转总线
================================
替代 WorkflowSnapshot 的文件系统存储。
使用 Redis Stream 在并行子任务之间传递数据。
"""
import json
import time
from typing import Dict, Optional


class RedisDataBus:
    """Redis Stream 数据中转总线"""

    STREAM_PREFIX = "yaxiio:data_bus"
    DEFAULT_TTL = 3600

    def __init__(self, redis_client):
        self.redis = redis_client

    def put(self, task_id: str, sid: str, data: dict) -> str:
        """发布子任务结果到 Stream"""
        stream_key = f"{self.STREAM_PREFIX}:{task_id}"
        payload = {
            "sid": sid,
            "data": json.dumps(data, ensure_ascii=False, default=str),
            "timestamp": str(time.time()),
        }
        try:
            msg_id = self.redis.client.xadd(stream_key, payload, maxlen=200, approximate=True)
            self.redis.client.expire(stream_key, self.DEFAULT_TTL)
            return msg_id
        except Exception:
            self.redis.set(f"{self.STREAM_PREFIX}:{task_id}:{sid}",
                          json.dumps(data, ensure_ascii=False, default=str),
                          ex=self.DEFAULT_TTL)
            return "fallback_key"

    def get(self, task_id: str, sid: str) -> Optional[dict]:
        """获取特定子任务的结果"""
        try:
            stream_key = f"{self.STREAM_PREFIX}:{task_id}"
            results = self.redis.client.xread({stream_key: "0"}, count=200)
            if results:
                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        msg_sid = fields.get(b"sid", b"").decode()
                        if msg_sid == sid:
                            return json.loads(fields.get(b"data", b"{}").decode())
        except Exception:
            pass
        try:
            raw = self.redis.get(f"{self.STREAM_PREFIX}:{task_id}:{sid}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass
        return None

    def get_all(self, task_id: str) -> Dict[str, dict]:
        """获取任务的所有子任务结果"""
        results = {}
        try:
            stream_key = f"{self.STREAM_PREFIX}:{task_id}"
            raw = self.redis.client.xread({stream_key: "0"}, count=200)
            if raw:
                for stream_name, messages in raw:
                    for msg_id, fields in messages:
                        msg_sid = fields.get(b"sid", b"").decode()
                        data_raw = fields.get(b"data", b"{}").decode()
                        if msg_sid:
                            results[msg_sid] = json.loads(data_raw)
        except Exception:
            pass
        return results

    def cleanup(self, task_id: str):
        """清理任务的所有数据"""
        try:
            self.redis.client.delete(f"{self.STREAM_PREFIX}:{task_id}")
            pattern = f"{self.STREAM_PREFIX}:{task_id}:*"
            keys = self.redis.keys(pattern)
            if keys:
                self.redis.client.delete(*keys)
        except Exception:
            pass
```

---

## 五、修改 workflow_engine._orchestrate_subtasks

```python
def _orchestrate_subtasks(self, task_id: str, subtasks: list, payload: dict) -> dict:
    """异步事件驱动编排（替代线程池方案）"""
    if MCP_LAYERS_ENABLED.get("L3"):
        return {"mcp_routed": True, "layer": "L3", "phase": "not_implemented"}

    use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "true").lower() == "true"

    if use_async and hasattr(self, "orchestrator") and self.orchestrator:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(
                self.orchestrator.execute(task_id, subtasks, payload)
            )
            loop.close()
            return results
        except Exception as e:
            print(f"[WF] 异步调度器失败，降级线程池: {e}", flush=True)

    # === 旧方案：线程池（保留作为 fallback）===
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    pending = {}
    completed = set()
    with ThreadPoolExecutor(max_workers=5) as executor:
        deadline = time.time() + POLL_TIMEOUT
        while len(completed) < len(subtasks) and time.time() < deadline:
            ready = [st for st in subtasks
                     if st["id"] not in completed and st["id"] not in pending
                     and all(d in completed for d in st.get("depends", []))]
            for st in ready:
                sid = st["id"]
                future = executor.submit(self._execute_subtask, task_id, sid, st, payload)
                pending[sid] = future
            if not ready and not pending:
                break
            if pending:
                for future in as_completed(list(pending.values()), timeout=3):
                    pass
                for sid, future in list(pending.items()):
                    if future.done():
                        try:
                            results[sid] = future.result(timeout=5)
                            completed.add(sid)
                            if hasattr(self, "data_bus") and self.data_bus:
                                self.data_bus.put(task_id, sid, results[sid])
                            del pending[sid]
                        except Exception as e:
                            results[sid] = {"ok": False, "error": str(e)[:200]}
                            completed.add(sid)
                            del pending[sid]
            time.sleep(0.5)
        for sid, future in pending.items():
            results[sid] = {"ok": False, "error": "timeout",
                           "agent": subtasks[0].get("agent", "?")}
            completed.add(sid)
    return results
```

---

## 六、初始化代码

在 `workflow_engine.__init__` 中添加：

```python
from modules.layer3.async_orchestrator import AsyncOrchestrator
from modules.layer3.redis_data_bus import RedisDataBus

self.orchestrator = AsyncOrchestrator(
    commander=self.commander,
    max_concurrent=int(os.environ.get("YAXIIO_MAX_CONCURRENT", "10")),
    total_timeout=int(os.environ.get("YAXIIO_TASK_TIMEOUT", "600")),
    subtask_timeout=int(os.environ.get("YAXIIO_SUBTASK_TIMEOUT", "120")),
)
self.data_bus = RedisDataBus(
    redis_client=self.commander.redis if self.commander else None
)
```

---

## 七、`modules/layer3/__init__.py` 新增导出

```python
from modules.layer3.async_orchestrator import AsyncOrchestrator        # 新增
from modules.layer3.redis_data_bus import RedisDataBus                # 新增
```

---

## 八、迁移步骤

```bash
# 启用异步调度器
export YAXIIO_ASYNC_ORCHESTRATOR=true
export YAXIIO_MAX_CONCURRENT=10

# 回滚方案: 设回 false 即可切回旧的线程池方案
export YAXIIO_ASYNC_ORCHESTRATOR=false
```

---

## 九、预期效果

| 指标 | 当前（线程池） | 目标（异步） | 提升 |
|------|-------------|-----------|------|
| 最大并发子任务 | 5（受限于线程池大小） | 10-50（受限于 asyncio） | 2-10x |
| 调度延迟 | O(n²) 线性扫描 | O(log n) 堆操作 | 显著 |
| 依赖解锁延迟 | 轮询等 3 秒 | 事件回调 即时 | ~3s per round |
| 数据中转延迟 | 文件 IO | Redis Stream | 10-100x |
| 优先级支持 | 无 | 4 级优先级 | ∞ |
| 超时控制 | 硬编码 120s | 可配置 per-task | 灵活性提升 |
