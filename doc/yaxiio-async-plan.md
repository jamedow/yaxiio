# Yaxiio asyncio 协程化 — 评估与方案

> 日期: 2026-05-29  
> 当前: Commander 同步主循环 + ThreadPoolExecutor + SharedAsyncLoop 桥接  
> 目标: Commander 原生 async，消除桥接层，提升并发能力

---

## 一、现状评估

### 1.1 当前架构

```
Commander.run() — 同步 while True
  ├── pubsub.get_message(timeout=2.0)    # 同步阻塞
  ├── BoundedThreadPool (max 5 threads)   # 线程池
  │    └── _run_delegated()              # 同步任务处理
  │         └── workflow.process()        # L1→L5
  │              └── async_loop.run_coro(llm.chat())  # ← 桥接异步
  └── cycle += 1

SharedAsyncLoop — 后台线程跑 event loop
  └── 唯一的用途: LLM API 调用
```

### 1.2 为什么需要改

| 问题 | 影响 |
|------|------|
| 线程池 max 5 个 worker | 第 6 个并发任务被拒绝 (queue_rejected++) |
| SharedAsyncLoop 是补丁 | 代码绕来绕去, 新人完全看不懂 |
| 同步 Redis | `pubsub.get_message(timeout=2)` 阻塞 2 秒, 浪费 |
| 同步 urllib HTTP | MCP 调用阻塞整个任务线程 |
| ThreadPool 有 GIL | Python 线程不能真正并行, 只是并发等待 |

### 1.3 改造范围

| 文件 | 行数 | 需改 | 难度 |
|------|:---:|------|:--:|
| yaxiio.py | 866 | `BoundedThreadPool` → asyncio tasks | ⭐⭐⭐⭐ |
| workflow_engine.py | 1134 | 所有 `process()` 方法 | ⭐⭐⭐⭐⭐ |
| mcp_bridge.py | 27 | `urllib` → `aiohttp` | ⭐⭐ |
| mcp/protocol.py | ~60 | MCPClient 异步化 | ⭐⭐ |
| modules/ (layers) | ~500 | 部分方法签名 | ⭐⭐ |
| neuron.py | 644 | 不改 (独立进程, 保持同步) | ⭐ |

---

## 二、业界参考

### 2.1 redis-py 原生支持 async

```python
import redis.asyncio as aioredis

r = aioredis.Redis(host="127.0.0.1", port=6379, password="...")

# 旧: pubsub.get_message(timeout=2.0)     同步阻塞
# 新: await pubsub.get_message(timeout=2.0) 非阻塞协程

# 旧: r.publish(ch, msg)                   同步
# 新: await r.publish(ch, msg)             异步
```

### 2.2 Python asyncio 并发模式

```python
# 旧: ThreadPoolExecutor (max 5)
pool.submit(tid, fn, args)

# 新: asyncio.Semaphore (max_concurrency=10)
async with semaphore:
    await asyncio.create_task(process_task(tid, args))
```

### 2.3 HTTP 客户端

```python
# 旧: urllib.request.urlopen(req, timeout=10)
# 新: httpx.AsyncClient / aiohttp
async with httpx.AsyncClient() as client:
    resp = await client.post(url, json=payload, timeout=10)
```

### 2.4 参考项目

| 项目 | 做法 |
|------|------|
| **FastAPI** | 全异步 HTTP 框架, `async def` 路由 |
| **LangChain** | `arun()` / `ainvoke()` 异步变体, 保留同步兼容 |
| **redis-py** | 同步 `Redis` + 异步 `redis.asyncio.Redis` 双 API |
| **httpx** | `Client` (同步) + `AsyncClient` (异步) 同接口 |

**最佳实践**: LangChain 的做法最实用——保留同步方法签名, 加 `async` 变体, 渐进迁移。不要一次全改。

---

## 三、方案设计：三阶段渐进迁移

### 阶段 1: Commander 主循环异步化（8小时）

**改动**: yaxiio.py 的 `Commander.run()` 方法

```python
# 旧
def run(self):
    while self.running:
        pubsub = self.redis.client.pubsub()
        pubsub.subscribe("yaxiio:agent:commander")
        msg = pubsub.get_message(timeout=2.0)
        ...
        self.pool.submit(tid, _run)

# 新
async def run(self):
    r = aioredis.Redis(...)
    sem = asyncio.Semaphore(10)  # 替代 BoundedThreadPool
    
    async with r.pubsub() as pubsub:
        await pubsub.subscribe("yaxiio:agent:commander")
        
        while self.running:
            msg = await pubsub.get_message(timeout=2.0)
            ...
            # 异步任务处理（不阻塞主循环）
            async with sem:
                asyncio.create_task(self._process_async(tid, data))
```

**效果**: 
- ❌ 删除 BoundedThreadPool
- ❌ 删除 SharedAsyncLoop
- ✅ 并发上限 5 → 10, 无 rejected
- ✅ Redis 操作全异步

### 阶段 2: 工作流引擎异步化（12小时）

**改动**: workflow_engine.py 和 mcp_bridge.py

```python
# 旧
def process(self, task_id, payload):
    l1 = self._do_L1(task_id, payload)
    l4 = self._do_L3_L4(task_id, payload, plan, state)
    l5 = self._do_L5(task_id, action, plan, l4, state)

# 新
async def process(self, task_id, payload):
    l1 = await self._do_L1(task_id, payload)
    l4 = await self._do_L3_L4(task_id, payload, plan, state)
    l5 = await self._do_L5(task_id, action, plan, l4, state)
```

```python
# mcp_bridge.py 旧
import urllib.request
def call_layer(layer, method, **kwargs):
    req = urllib.request.Request(...)
    resp = urllib.request.urlopen(req, timeout=10)

# mcp_bridge.py 新
import httpx
async def call_layer(layer, method, **kwargs):
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, timeout=10)
```

**效果**:
- ✅ LLM 调用原生 async (不用桥接)
- ✅ MCP 调用异步 (不阻塞)
- ✅ 简单任务 0 线程

### 阶段 3: 清理 + 测试（4小时）

- 删除 SharedAsyncLoop 类
- 删除 BoundedThreadPool 类  
- 删除 `import threading`
- 跑 test_dispatch_suite.py 验证
- 性能压测: 并发 20 个任务, 对比旧版

---

## 四、工作量评估

| 阶段 | 内容 | 工作量 | 风险 |
|------|------|:--:|------|
| 1 | Commander 主循环 async | ✅ 1.5h | 完成 — 主入口改动 |
| 2 | 工作流引擎 async | ✅ 1h | 完成 — 所有 pipeline 方法 |
| 3 | 清理 + 测试 | ✅ 0.5h | 完成 |
| **总计** | | **✅ 3h (实际)** | |

### 不改的部分

| 模块 | 原因 |
|------|------|
| neuron.py | 独立子进程, 同步没毛病 |
| Guardian | 独立进程, 同步没毛病 |
| L1-L5 MCP Server | 独立进程, 同步 HTTP Server 够用 |
| modules/ | 大多是工具函数, 改签名即可 |

---

## 五、风险与回退方案

**最大风险**: workflow_engine 1134 行中每条路径都要加 `await`, 容易漏。

**回退**: Git 保留当前分支, 新开 `feat/async-commander` 分支开发。任何阶段出问题, 切回 master 即可。

**验证策略**: 每改完一个方法, 立即用 `test_dispatch_suite.py` 验证。不要攒到最后一把测。

---

## 六、预期收益

| 指标 | 旧 | 新 | 提升 |
|------|:--:|:--:|:--:|
| 最大并发任务 | 5 | 10+ | 2x |
| 任务拒绝率 | 有 (queue_rejected) | 0 | ∞ |
| 代码行数 | ~2700 | ~2400 | -11% |
| 线程数 | 8 (主+5线程池+async+日志) | 1 (主协程) | -87% |
| LLM 调用延迟 | 桥接开销 ~5ms | 0 | 即时 |
| 新人理解代码 | 20分钟 | 5分钟 | 4x |

---

> 建议: 先做阶段 1, 验证 Commander 主循环 async 稳定性, 再推进 2/3。
> 实施记录将追加到本文档。


## 实施记录

- 2026-05-29: Stage 1-3 全部完成, 3小时, 866→765行(-12%)
- 分支: feat/async-commander → master
