# Yaxiio 设计决策

## 决策 1: 五层 MCP vs 微服务

选了五层 MCP 流水线 (L1-L5 HTTP/JSON-RPC)，没选微服务+消息队列。
- 五层是严格顺序依赖，不是独立服务
- MCP 调试方便，消息队列运维复杂度高
- 每层可独立重启/升级

## 决策 2: Commander 不能亲自执行 (宪法)

选了硬编码宪法约束，没选 Commander if/else 分发。
- v1.04 时 Commander 有 14 个 `_run_xxx()` 直接执行方法
- 宪法只给 6 个系统管理操作开白名单
- 违宪行为记录到 `yaxiio:constitution:violations`

## 决策 3: Agent 是独立进程 (神经元)

选了每个 Agent = 独立 Python 子进程，没选线程/协程。
- 进程隔离: 一个 Agent OOM 不影响其他
- Python GIL 下多进程是唯一真正并行方式
- 每个进程 ~25MB, 10 个才 250MB

## 决策 4: Redis Pub/Sub 通信

选了 Redis Pub/Sub + JSON，没选 gRPC/WebSocket。
- Redis 已在架构里，零额外依赖
- 天然支持多对多 (Commander↔Agent, Agent↔Agent P2P)
- 调试直接用 `redis-cli`

## 决策 5: 事件溯源状态机

选了任务状态机 + 不可变时间线 (Redis JSON)，没选 MySQL 状态表。
- 任务生命周期短 (分钟级)，不需持久化到关系库
- 天然审计轨迹 (timeline 不可变追加)
- Redis 亚毫秒读写

## 决策 6: 1 状态机 vs 5 独立 FSM

选了 1 个任务 = 1 个状态机 + 5 个 milestone。
- 五层是顺序依赖，不是独立状态转换
- L2 不能从 IDLE 跳到 DONE 而不经过 L1

## 决策 7: 双层互保 (Guardian)

选了 PM2 → Guard-primary + Guard-secondary, Redis leader 选举。
- Guardian 本身也是进程，也会挂
- Secondary 平时只监控心跳，30s 内接管

## 决策 8: 分层故障处理

| 谁坏 | 谁修 | 修什么 |
|------|------|------|
| Agent 崩溃 | Commander | spawn_neuron 重启 |
| Agent 低质量 | 系统医生 | Prompt 分析→修复→A/B |
| Commander 崩溃 | Guardian | 进程重启 |
| Commander 调度差 | Guardian | 元评分→渐进式修复 |
| Guardian 崩溃 | PM2/另一个Guardian | 进程重启/接管 |

## 设计模式

### 宪法模式
`handle_task()` 不直接判断 action，先过宪法:
```python
verdict, reason = constitution.review(action, payload)
# ALLOWED→直通, DELEGATED→五层, REJECTED→拒绝, DEGRADED→强制sandbox
```

### Arsenal 模式
Commander 能力注册为具名工具，流水线按需调用:
```python
arsenal.call("content_audit", task_id, payload)
```

### 神经元模式
```
Agent = 感知器(Redis Sub) + 大脑(LLM) + 双手(Tool) + 嘴巴(Pub) + 记忆(Redis)
```

### 依赖 DAG + 并行发射
```
s1 ─┐
s2 ─┼─→ s4 ─┐
s3 ─┘       ├─→ s6 → s7
     s5 ────┘
发射: {s1,s2,s3}并行 → {s4,s5}并行 → s6 → s7
```

## 技术债务

| 债务 | 说明 |
|------|------|
| LLMAdapter 双重调用 | neuron.py 同步, workflow_engine.py 异步, 两套代码 |
| POLL_TIMEOUT 硬编码 | 子任务等待 60s, 复杂任务不够 |
| 工具 stdout 不可见 | spawn_neuron PIPE 无人读 |
