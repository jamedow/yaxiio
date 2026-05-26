# Yaxiio 任务状态机设计

## 现状

```python
class TaskStatus(Enum):
    PENDING = "pending"      # 定义了但从未使用
    RUNNING = "running"      # Scheduler.execute() 直接返回，不跟踪
    COMPLETED = "completed"
    FAILED = "failed"
```

Scheduler 调用 `execute()` 后立即返回 `{"status":"success"}`，不等待子任务完成，不跟踪进度，不处理失败。任务发出去就丢了。

---

## 设计目标

1. **可观测**：任务在任意时刻有明确状态，Dashboard 可查看
2. **可恢复**：Commander 重启后能从 Redis 恢复未完成任务
3. **可重试**：失败自动重试 N 次，支持退避策略
4. **可降级**：超时或持续失败自动走降级路径
5. **可审计**：完整的状态变更日志

---

## 状态定义

```
                     ┌──────────┐
                     │  QUEUED  │  刚收到，等待调度
                     └────┬─────┘
                          │ Scheduler 取走
                     ┌────▼─────┐
                     │ PLANNING │  LLM拆解任务→生成子任务DAG
                     └────┬─────┘
                          │
               ┌──────────▼──────────┐
               │    DISPATCHING      │  分配Agent，发送PubSub
               └──────────┬──────────┘
                          │
               ┌──────────▼──────────┐
               │      RUNNING        │  Agent执行中
               └────┬──────┬────┬────┘
                    │      │    │
          ┌─────────▼─┐ ┌──▼──┐└─────────┐
          │ EVALUATING│ │WAIT │          │
          │ (L4评分)  │ │依赖 │          │
          └─────┬─────┘ └──┬──┘          │
                │          │             │
         ┌──────▼──────┐   │    ┌────────▼────────┐
         │  COMPLETED  │   │    │     FAILED       │
         │  (得分≥6.0)  │   │    │ (不可恢复错误)    │
         └─────────────┘   │    └────────┬─────────┘
                           │             │ retry_count < max
                           │    ┌────────▼────────┐
                           │    │    RETRYING      │
                           │    │ (指数退避5s/10s)  │
                           │    └────────┬────────┘
                           │             │ 重试次数超限
                           │    ┌────────▼────────┐
                           │    │   FALLBACK      │
                           │    │ (降级路径/人工)   │
                           │    └─────────────────┘
                           │
                           │ 依赖的子任务全部完成
                     ┌──────▼──────┐
                     │   RESUMED   │  继续执行
                     └──────┬──────┘
                            │
                     ┌──────▼──────┐
                     │  COMPLETED  │
                     └─────────────┘
```

## 状态说明

| 状态 | 含义 | 触发条件 | 下一状态 |
|------|------|---------|---------|
| **QUEUED** | 刚收到，等待调度 | `handle_task()` 收到消息 | PLANNING |
| **PLANNING** | LLM拆解中 | Scheduler 取走任务 | DISPATCHING |
| **DISPATCHING** | 分配Agent | 拆解完成，有子任务列表 | RUNNING / WAIT |
| **RUNNING** | Agent执行中 | Agent确认收到 | EVALUATING / FAILED |
| **WAIT** | 等待依赖完成 | 依赖的子任务未完成 | RESUMED |
| **EVALUATING** | L4评分中 | Agent返回结果 | COMPLETED / FAILED / RETRYING |
| **RETRYING** | 重试中 | 失败且 retry_count < max | RUNNING |
| **FALLBACK** | 降级处理 | 重试次数超限 | COMPLETED (降级) |
| **COMPLETED** | 终态-成功 | 评分≥6.0 或降级完成 | - |
| **FAILED** | 终态-失败 | 不可恢复错误 | - |

---

## 数据结构 (Redis)

```python
# 任务状态
task:{task_id} → {
    "status": "running",
    "action": "site_audit",
    "created_at": "2026-05-26T12:00:00",
    "updated_at": "2026-05-26T12:00:05",
    "retry_count": 0,
    "max_retries": 3,
    "current_agent": "CodeAuditor",
    "subtasks": [
        {"id": "sub-1", "status": "completed", "agent": "CodeAuditor"},
        {"id": "sub-2", "status": "running", "agent": "CodeAuditor"},
        {"id": "sub-3", "status": "waiting", "agent": null}
    ],
    "score": null,
    "error": null,
    "result": null
}

# 状态变更日志
task:log:{task_id} → [
    {"from": "queued", "to": "planning", "timestamp": "..."},
    {"from": "planning", "to": "dispatching", "timestamp": "..."},
]

# 全局任务索引
task:index:status:running → SET {task_id1, task_id2}
task:index:status:failed  → SET {task_id3}
```

---

## 状态转换规则

| 转换 | 条件 | 副作用 |
|------|------|--------|
| QUEUED→PLANNING | Scheduler 空闲 | 创建 task:{id} |
| PLANNING→DISPATCHING | LLM拆解完成 | 写入 subtasks |
| DISPATCHING→RUNNING | Agent确认收到 | heartbeat 计时开始 |
| DISPATCHING→WAIT | 依赖未满足 | 订阅依赖任务完成事件 |
| RUNNING→EVALUATING | Agent返回结果 | L4 AutoScorer 评分 |
| RUNNING→FAILED | Agent超时(>5min) | retry_count++ |
| EVALUATING→COMPLETED | score ≥ threshold | 写审计日志, 触发 drill 检查 |
| EVALUATING→RETRYING | score < threshold 且可重试 | retry_count++, 退避 |
| RETRYING→RUNNING | 退避结束 | 重新 DISPATCHING |
| RETRYING→FALLBACK | retry_count ≥ max | 走降级路径 |
| WAIT→RESUMED | 依赖任务全部 COMPLETED | 继续执行 |
| 任意→FAILED | Redis写失败 / 不可恢复 | 通知 Operator |

---

## 与现有系统的集成

```
handle_task()          → create_task(QUEUED)
scheduler.execute()    → transition(PLANNING→DISPATCHING)
_send_to_agent()       → transition(DISPATCHING→RUNNING)
agent response handler → transition(RUNNING→EVALUATING)
auto_scorer.score()    → transition(EVALUATING→COMPLETED/RETRYING)
_run_drill() 触发      → score < threshold 时自动检查
```

## 与 Agent 四象限的关系

- **Agent 四象限**：管理 Agent 的创建/销毁/空闲超时
- **任务状态机**：管理任务的流转/重试/降级

两者互补：Agent 是"谁来做"，任务是"做什么、做完了没"。
