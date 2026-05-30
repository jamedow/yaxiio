# Yaxiio P0 重构：方案对比分析

> 日期: 2026-05-30 | 目标: workflow_engine.py (1247行) + yaxiio.py (932行)

---

## 零、现状诊断

### workflow_engine.py 当前结构

```
class WorkflowEngine:                    # 1247行, 无继承
  ├── __init__()                         # 初始化 15 个成员变量
  ├── process()                          # 入口: 简单/复杂任务分流
  ├── _process_simple()                  # 单任务 L1→L5
  ├── _process_complex()                 # 多子任务编排 (300+行, CC=12)
  ├── _do_L1()                           # L1 感知 (25行)
  ├── _do_L2()                           # L2 规划 (8行)
  ├── _do_L3_L4()                        # L3 调度 + L4 执行 (90行)
  ├── _do_L5()                           # L5 评分 → UnifiedScorer
  ├── _decompose_via_l2()                # 任务拆解 (80行)
  ├── _llm_decompose()                   # LLM 拆解 (60行)
  ├── _orchestrate_subtasks()            # 子任务编排 (120行)
  ├── _execute_subtask()                 # 单子任务执行 (50行)
  ├── _schedule_via_l3()                 # L3 调度
  ├── _clone_agents_for_task()           # Agent 克隆
  ├── _check_and_heal()                  # 故障检测
  ├── _cleanup_task()                    # 任务清理
  ├── _analyze_gap()                     # 差距分析
  ├── _summarize_results()               # 结果汇总
  ├── _get_llm() / _call_llm()          # LLM 调用
  └── _build_plan()                      # 构建计划
```

**核心问题**：
1. 五层逻辑全部耦合在同一个类中，修改 L1 可能影响 L5
2. `_process_complex()` 方法 300+ 行，圈复杂度 12，是 bug 温床
3. 15 个成员变量在 20 个方法间共享，依赖关系不可见
4. 无法单独测试任何一个层

---

## 一、五种方案对比

### 方案 A：策略模式 + 委托 (Delegate Pattern)

**核心思想**：每个层抽象为独立处理器，WorkflowEngine 持有处理器实例并委托。

```python
# 重构后
class WorkflowEngine:
    def __init__(self, commander):
        self.l1_handler = L1Handler(commander)       # 25行
        self.l2_handler = L2Handler(commander)        # 80行
        self.l4_handler = L4Handler(commander)        # 150行
        self.orchestrator = Orchestrator(commander)   # 120行 (已有 AsyncOrchestrator)
    
    def _process_complex(self, task_id, payload):
        state = self.l1_handler.analyze(task_id, payload)      # 委托
        subtasks = self.l2_handler.decompose(task_id, payload)  # 委托
        results = self.orchestrator.run(task_id, subtasks)      # 委托
        score = self.l5_handler.evaluate(task_id, results)      # 委托
```

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ⭐⭐⭐ | 中。每个方法提取为独立类，需要显式传递 commander/sm/l0 等依赖 |
| 风险 | ⭐⭐⭐⭐ | 低。委托关系清晰，每个处理器可独立测试 |
| 测试友好 | ⭐⭐⭐⭐⭐ | 极高。每个处理器可 mock 依赖独立测试 |
| 符合 Yaxiio 设计 | ⭐⭐⭐⭐⭐ | 完美契合"五层独立 MCP"的设计思想 |
| 业界参考 | Django View → Service, Spring @Service | 成熟的委托模式 |
| 回滚难度 | ⭐⭐⭐⭐ | 低。保留原类，新旧并行 |

**改了哪里**：新建 3 个 handler 文件，WorkflowEngine 减少 ~400 行。

**为什么不选**：
- 需要显式管理各 handler 之间的状态传递（当前通过 `state` dict 隐式共享）
- `_process_complex()` 的 300 行中有大量内联逻辑，需要先整理才能提取

---

### 方案 B：Mixin 多重继承

**核心思想**：各层方法分散到 Mixin 类中，WorkflowEngine 通过多重继承组合。

```python
class L1Mixin:
    def _do_L1(self, ...): ...

class L4Mixin:
    def _execute_subtask(self, ...): ...

class WorkflowEngine(L1Mixin, L4Mixin, OrchMixin):
    def process(self, ...): ...  # 编排逻辑留在主类
```

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ⭐⭐⭐⭐ | 最小。只移动方法定义，`self.xxx` 引用完全不变 |
| 风险 | ⭐⭐⭐ | 中。Python MRO 可能导致方法覆盖冲突 |
| 测试友好 | ⭐⭐ | 差。Mixin 无法独立实例化，仍需通过 WorkflowEngine 测试 |
| 符合 Yaxiio 设计 | ⭐⭐ | 弱。Mixin 只是物理拆分，逻辑上五层仍然耦合 |
| 业界参考 | Django CBV Mixins | Django 的类视图混入 |
| 回滚难度 | ⭐⭐⭐ | 中。需要调整多重继承顺序 |

**改了哪里**：3 个 Mixin 文件，WorkflowEngine 加继承声明，`self` 引用零改动。

**为什么不选**：
- Mixin 是"假拆分"——文件变小了但耦合度没变
- 无法解决"修改 L1 可能影响 L5"的问题
- 测试时仍需构造完整的 WorkflowEngine 实例

---

### 方案 C：纯函数提取

**核心思想**：将方法改为纯函数，所有依赖通过参数显式传递。

```python
# 重构前
def _do_L1(self, task_id, payload):
    l1_text = {k: v for k, v in payload.items() if not k.startswith("_")}
    l1 = call_layer(1, "analyze_intent", text=json.dumps(l1_text))
    ...

# 重构后
def analyze_intent_layer1(payload: dict) -> dict:
    """L1 感知 — 纯函数，无 self 依赖"""
    l1_text = {k: v for k, v in payload.items() if not k.startswith("_")}
    return call_layer(1, "analyze_intent", text=json.dumps(l1_text))
```

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ⭐⭐ | 大。每个方法需要提取 `self.xxx` 引用为参数，改动面广 |
| 风险 | ⭐⭐ | 高。函数签名变更可能遗漏调用处 |
| 测试友好 | ⭐⭐⭐⭐⭐ | 极高。纯函数天然可测试 |
| 符合 Yaxiio 设计 | ⭐⭐⭐⭐ | 好。符合"纯编排"原则 |
| 业界参考 | Functional Core / Imperative Shell | Gary Bernhardt 的经典架构 |
| 回滚难度 | ⭐⭐ | 高。函数签名变化影响所有调用方 |

**改了哪里**：新建 `workflow_functions.py`（~300 行），WorkflowEngine 改为调用这些函数。

**为什么不选**：
- 改动量最大，`self.commander`、`self.sm`、`self.l0` 等 15 个引用都需要传参
- 风险高，当前测试覆盖率不足以支撑这种规模的改动

---

### 方案 D：渐进式 Feature Flag 并行

**核心思想**：新代码和旧代码并行运行，通过 feature flag 切换。

```python
def _process_complex(self, task_id, payload):
    if os.environ.get("YAXIIO_REFACTOR_V2") == "true":
        return self._process_complex_v2(task_id, payload)  # 新实现
    return self._process_complex_v1(task_id, payload)      # 旧实现
```

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ⭐ | 最大。需要完整重写核心逻辑 |
| 风险 | ⭐⭐⭐⭐⭐ | 最低。新旧并行，随时可切回 |
| 测试友好 | ⭐⭐⭐ | 中。新旧代码都需要测试 |
| 符合 Yaxiio 设计 | ⭐⭐⭐⭐ | 好。新代码可以完全按设计重构 |
| 业界参考 | LaunchDarkly Feature Flags, K8s API 版本演进 | 成熟的渐进式迁移 |
| 回滚难度 | ⭐⭐⭐⭐⭐ | 极低。改环境变量即回滚 |

**改了哪里**：新建 `workflow_engine_v2.py`（~500 行），原文件不动。

**为什么不选**：
- 重复工作量最大
- 需要同时维护两套代码直到新代码稳定

---

### 方案 E：暂不拆分，先加测试 (Do Nothing Now)

**核心思想**：承认当前技术债务，先通过增加测试覆盖来降低未来重构风险。

| 维度 | 评分 | 说明 |
|------|------|------|
| 改动量 | ⭐⭐⭐⭐⭐ | 无代码改动，只加测试 |
| 风险 | ⭐⭐⭐⭐⭐ | 零风险 |
| 架构改善 | ⭐ | 无改善 |
| 远期收益 | ⭐⭐⭐⭐ | 高。测试覆盖是重构的前提 |

**业界参考**：Michael Feathers "Working Effectively with Legacy Code" —— 重构遗留代码的第一步永远是加测试。

---

## 二、推荐方案：A + D 组合

```
Phase 2a (本周): 方案 E — 增加测试覆盖
  目标: workflow_engine 关键路径测试覆盖率达到 40%
  产出: test_workflow.py (~150行)

Phase 2b (下周): 方案 A — 委托模式提取 L1/L4
  目标: workflow_engine.py 减至 ~800 行
  产出: workflow_l1_handler.py, workflow_l4_handler.py

Phase 2c (下下周): 方案 D — Feature Flag 重写 _process_complex
  目标: 圈复杂度从 12 降至 <6
  产出: workflow_complex_v2.py, YAXIIO_REFACTOR_V2=true 切换
```

### 为什么选这个组合

1. **先测后改**（方案 E 前置）—— 没有测试的拆分是盲飞
2. **委托模式**（方案 A）—— 契合五层 MCP 设计，改动量适中，可独立测试
3. **Feature Flag**（方案 D）—— 最复杂的方法（`_process_complex` 300 行）用全新实现，零风险切换

### 不选 Mixin (B) 的原因

Mixin 只是"把代码挪到另一个文件"，耦合度不变。Yaxiio 的设计哲学是**五层独立**，不是"五层的代码独立存储"。委托模式才能做到"修改 L1 不影响 L4"。

### 不选纯函数 (C) 的原因

当前测试覆盖率不足以支撑 15 个 `self.xxx` 引用的显式参数化。需要先达到 40% 覆盖率。

---

## 三、具体执行：Phase 2a — 测试先行

### 要测什么

| 测试对象 | 测试内容 | 预估行数 |
|---------|---------|---------|
| `process()` 分流 | 简单任务走 simple，复杂任务走 complex | 20 |
| `_decompose_via_l2()` | 数据驱动批处理、LLM 拆解、经验注入 | 30 |
| `_do_L5()` | UnifiedScorer 主路径 + legacy 降级 | 25 |
| `_orchestrate_subtasks()` | 依赖图、并行发射、超时处理 | 30 |
| `_cleanup_task()` | ExperienceFlywheel + 降级 | 20 |
| `_check_and_heal()` | 故障检测 + 医生派遣 | 15 |

### 怎么测

```python
class TestWorkflowEngine:
    def setup(self):
        # Mock Commander, Redis, LLM
        self.engine = WorkflowEngine(commander=MockCommander())
    
    def test_process_routes_simple_action_to_simple_path(self):
        result = self.engine.process("test-001", {"action": "status"})
        assert result["status"] == "DONE"
    
    def test_decompose_via_l2_with_data_driven_batch(self):
        subtasks = self.engine._decompose_via_l2("test-002", 
            {"task": "fix 300 mixed entries", "action": "fix"})
        assert len(subtasks) > 1  # Should batch
```

### 做到了什么

- 重构前的安全网：任何改动如果破坏现有行为，测试立刻报错
- 架构理解的文档：测试本身就是"这个方法应该做什么"的说明
- CI 集成：`python3 tests/test_workflow.py` 加入 GitHub Actions

---

## 四、预期效果汇总

| 指标 | 当前 | Phase 2a | Phase 2b | Phase 2c |
|------|------|---------|---------|---------|
| workflow_engine.py | 1247行 | 1247行 | ~800行 | ~500行 |
| 测试覆盖 | 0% | 20% | 30% | 40%+ |
| 圈复杂度(max) | 12 | 12 | 8 | <6 |
| 可独立测试的层 | 0 | 0 | 2 (L1,L4) | 4 |
| 架构评分 | 3/10 | 4/10 | 6/10 | 7/10 |

---

> **原则**: 不赌一次改对，赌每次改动都可验证、可回滚。
