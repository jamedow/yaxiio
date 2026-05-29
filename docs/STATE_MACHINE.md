# 雅溪 Yaxiio 状态机详解

> Version: 1.0 | Date: 2026-05-29
> 贯穿 L1-L5 任务状态机 + Agent 生命周期状态机 + 故障恢复路径

---

## 一、两层状态机体系

Yaxiio 有两套互相独立但协作的状态机：

| 层级 | 状态机 | 管理对象 | 持久化 |
|------|--------|---------|--------|
| 任务层 | TaskStateMachine | 一个任务从创建到完成 | Redis `task:{task_id}` |
| Agent 层 | Neuron 状态机 | 一个 Agent 的生命周期 | Redis `agent:{name}:state` |

---

## 二、任务状态机（11 状态）

### 2.1 状态图

```
                    ┌──────────┐
                    │  QUEUED  │  ← 任务创建
                    └────┬─────┘
                         │
                    ┌────▼─────┐
                    │ PLANNING │  ← L2 拆解子任务
                    └────┬─────┘
                         │
                    ┌────▼──────┐
              ┌─────│DISPATCHING│  ← L3 分配 Agent
              │     └────┬──────┘
              │          │
              │     ┌────▼─────┐
              │     │  RUNNING │  ← L4 执行中
              │     └────┬─────┘
              │          │
              │     ┌────▼──────┐
              │     │  WAITING  │  ← 等待依赖完成
              │     └────┬──────┘
              │          │
              │     ┌────▼──────┐
              │     │EVALUATING │  ← L5 评分中
              │     └────┬──────┘
              │          │
              │     ┌────▼──────┐
              │     │ COMPLETED │  ← 成功 ✅
              │     └───────────┘
              │
              │     ┌──────────┐
              ├────►│ RETRYING │  ← 失败但可重试
              │     └────┬─────┘
              │          │
              │     ┌────▼─────┐
              │     │ FALLBACK │  ← 重试耗尽，走降级路径
              │     └────┬─────┘
              │          │
              │     ┌────▼─────┐
              └────►│  FAILED  │  ← 不可恢复 ❌
                    └──────────┘
```

### 2.2 合法转换表

```python
TRANSITIONS = {
    QUEUED:      [PLANNING],
    PLANNING:    [DISPATCHING, FAILED],
    DISPATCHING: [RUNNING, WAITING, FAILED],
    RUNNING:     [EVALUATING, FAILED, RETRYING],
    WAITING:     [RUNNING],
    EVALUATING:  [COMPLETED, RETRYING, FAILED],
    RETRYING:    [DISPATCHING, FALLBACK],
    FALLBACK:    [COMPLETED],
}
```

**关键规则**：
- 只有 `DISPATCHING` 可以转到 `WAITING`（依赖未满足）
- 只有 `RUNNING` 可以转到 `EVALUATING`
- `RETRYING` → `DISPATCHING`（重试会重新调度 Agent）
- `FALLBACK` → `COMPLETED`（降级路径也算完成）

### 2.3 实际代码中的状态流转

```
handle_task()
  → sm.create(task_id, ...)              # QUEUED
  → sm.start_layer("L1_perception")      # PLANNING
  → _do_L1() → sm.complete_layer(...)
  → sm.start_layer("L2_planning")        # DISPATCHING
  → _do_L2() → _clone_agents_for_task()
  → _orchestrate_subtasks()              # RUNNING / WAITING
  → _do_L5()                             # EVALUATING
  → sm.transition("DONE")                # COMPLETED
  → 或 sm.transition("FAILED")           # FAILED
```

---

## 三、Agent 生命周期状态机（Neuron）

### 3.1 状态图

```
     ┌──────┐   收到任务   ┌───────────┐
     │ IDLE │────────────►│ EXECUTING  │
     └──┬───┘             └─────┬──────┘
        │                      │
        │                 ┌────┴──────┐
        │                 │  TIMEOUT  │ ← 超时
        │                 └────┬──────┘
        │                      │
        │                 ┌────▼──────┐
        │                 │RECOVERING │ ← 重试中
        │                 └────┬──────┘
        │                      │
        │          ┌───────────┴──────────┐
        │          │                      │
        │    重试成功                重试耗尽
        │          │                      │
        │     ┌────▼───┐            ┌─────▼───┐
        └─────│  IDLE  │            │  FAULT  │ ← 需要人工介入
              └────────┘            └─────────┘
```

### 3.2 状态转换代码

```python
# neuron.py
def _set_state(self, new_state: str):
    old = self.state
    self.state = new_state
    self.state_since = time.time()
    # 写入 Redis 便于外部监控
    self.redis.setex(f"agent:{self.name}:state", 300,
        json.dumps({"state": new_state, "since": self.state_since}))

# 超时检测
elapsed = time.time() - self.task_start_time
if elapsed > self.task_timeout:
    self._set_state("TIMEOUT")
    if self.retry_count < self.max_retries:
        self.retry_count += 1
        self._set_state("RECOVERING")
        time.sleep(2 ** self.retry_count)  # 指数退避
    else:
        self._set_state("FAULT")
```

### 3.3 四象限对状态机的影响

| 象限 | IDLE 超时行为 | FAULT 处理 | DESTROY 条件 |
|------|-------------|-----------|-------------|
| Core | 不超时，永不休眠 | 立即重建 | 永不销毁 |
| Strategic | 600s → HIBERNATING | 重试 3 次，仍失败休眠 | 低分 + 闲置 |
| Utility | 不超时 | 重建 | 异常率 > 20% |
| Ephemeral | 不适用 | 不重试 | 任务完成立即销毁 |

---

## 四、故障恢复路径

### 4.1 Agent 崩溃

```
Agent crash → Commander._find_neuron() 检测到 PID 不存在
           → 记录到故障检测器
           → handle_agent_failure("crash")
           → spawn_neuron() 重启
           → 任务从检查点继续
```

### 4.2 Agent 低质量

```
L5 评分 < 5 连续 3 次 → FailureDetector 标记 "low_quality"
                      → Commander.handle_agent_failure("low_quality")
                      → 派系统医生 Agent
                      → 医生分析 prompt → 生成修复建议 → A/B 测试
```

### 4.3 Commander 崩溃

```
Guardian 心跳检测 → Commander 进程不存在
                  → FaultDiagnoser 分析日志
                  → AutoRepair 针对性修复（Redis / models.json / API Key）
                  → RateLimiter 检查（2 分钟内最多 3 次）
                  → CommanderManager.restart()
```

### 4.4 Guardian 崩溃

```
PM2 检测到 Guardian 进程退出
  → 自动重启 Guardian（最多 5 次）
  → Guardian 重启后执行 Leader 选举
  → 发现已有 Leader → Secondary 模式
  → 发现无 Leader → Primary 模式 + 启动 Commander
```

---

## 五、工作流子任务状态

在复杂任务的并行编排中，每个子任务有独立状态：

```
subtask_start(task_id, sid, agent, action)
  → 状态: DISPATCHED

subtask_done(task_id, sid, output, elapsed)
  → 状态: DONE ✅

subtask_timeout(task_id, sid)
  → 状态: TIMEOUT ⏰ → 触发重试

subtask_failed(task_id, sid, error)
  → 状态: FAILED ❌ → 检查是否连续失败 > 2 → 派医生
```

---

## 六、断点恢复机制

### 6.1 Commander 重启后

```python
# yaxiio.py _recover_inflight()
for key in redis.keys("yaxiio:task:*"):
    task = json.loads(redis.get(key))
    if task.status in ("EXECUTING", "SCORING", "RUNNING"):
        # 重新调度这个任务
        self._run_delegated(tid, action, payload, data)
```

### 6.2 TaskStateMachine.recover()

```python
# task_state_machine.py
def recover(self):
    for status in [RUNNING, DISPATCHING, EVALUATING, RETRYING]:
        for tid in self.list_by_status(status):
            task = self._load(tid)
            age = time.time() - task["updated_at"]
            if age > 300:  # 超过 5 分钟
                self.fail(tid, f"timeout after {int(age)}s")
```

---

*下一步：阅读 `MEMORY-SYSTEM.md` 了解三层记忆架构。*
