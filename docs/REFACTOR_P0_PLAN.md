# Yaxiio P0 重构方案

> 版本: 1.0 | 日期: 2026-05-30
> 原则: 不破坏设计思想，不引入回归，渐进式重构

---

## 一、现状分析

### 1.1 问题

| 文件 | 行数 | CC(max) | 核心问题 |
|------|------|---------|---------|
| `workflow_engine.py` | 1247 | 12 | 五层逻辑全部内联在一个类中，圈复杂度高，无法独立测试 |
| `yaxiio.py` | 932 | 8 | Commander 承担了 LLM管理/Neuron生命周期/任务路由/沙箱管理 四种职责 |
| `tests/` | 0 | — | 零测试覆盖，任何重构都有回归风险 |

### 1.2 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 重构引入回归 bug | 高 | 严重 | 重构前先写测试，重构后跑测试验证 |
| 破坏五层 MCP 接口 | 中 | 严重 | 保持 `call_layer()` 接口不变 |
| 破坏宪法审查链 | 低 | 严重 | 不修改 `constitution.review()` 调用路径 |
| 性能退化 | 低 | 中 | 重构后做 benchmark 对比 |
| 与 Gateway 兼容性破坏 | 中 | 中 | 保持 `WorkflowEngine.process()` 签名不变 |

### 1.3 业界参考

| 参考 | 要点 |
|------|------|
| **Martin Fowler - Refactoring** | 小步重构，每次改动可验证；先写测试再重构 |
| **Google Python Style Guide** | 单个类不超过 500 行；模块内聚 |
| **Clean Architecture** | 依赖倒置：高层模块不依赖低层模块 |
| **Django 重构经验** | 从 `views.py` 拆出 `services.py`/`managers.py` |
| **LangChain 模块化** | 每个 Chain/Agent 独立文件，通过接口组合 |

---

## 二、重构原则

1. **渐进式** — 不一次性大改，每次只动一个维度
2. **可回滚** — 每个 commit 都是可工作的状态
3. **先测后改** — 重构前先给要改的模块写测试
4. **接口不变** — `WorkflowEngine.process()` 和 Commander 的对外接口保持不变
5. **符合 Yaxiio 设计思想**:
   - 五层独立可测试
   - 宪法审查不可绕过
   - 纯编排不执行

---

## 三、执行计划

### Step 1: 建立测试基线 (先测后改)

给三个核心模块写测试：

| 模块 | 测试重点 | 预估行数 |
|------|---------|---------|
| `constitution.py` | 四种裁决、白名单、危险模式匹配 | ~80 |
| `task_state_machine.py` | 11 状态转换合法性、恢复 | ~60 |
| `unified_scorer.py` | 评分策略、维度融合、进化信号 | ~60 |

### Step 2: 拆分 workflow_engine.py

```
重构前: workflow_engine.py (1247行, 一个类)
重构后:
  workflow_engine.py (~300行) — WorkflowEngine 编排器
  workflow_layer1.py (~80行)  — L1 感知逻辑
  workflow_layer4.py (~150行) — L4 执行+等待逻辑
  workflow_orchestrate.py (~200行) — 子任务编排+依赖管理
  (已拆出的: L2 intent_router, L3 async_orchestrator, L5 unified_scorer)
```

**不动的部分**: `process()`, `_process_complex()`, `_process_simple()` — 入口和编排逻辑留在 WorkflowEngine

**提取的部分**:
- `_do_L1()` → `workflow_layer1.py`
- `_execute_subtask()`, `_wait_for_neuron_response()` → `workflow_layer4.py`
- `_orchestrate_subtasks()`, `_build_dep_graph()` → `workflow_orchestrate.py`

### Step 3: 拆分 yaxiio.py

```
重构前: yaxiio.py (932行)
重构后:
  yaxiio.py (~400行) — Commander 主控
  commander_llm.py (~100行) — LLM 管理
  commander_neurons.py (~150行) — Neuron 生命周期
  commander_tasks.py (~120行) — 任务路由
```

**不动的部分**: `handle_task()`, `run()`, `_run_delegated()` — 核心编排

**提取的部分**:
- `_get_llm()`, `_call_llm()` → `commander_llm.py`
- `spawn_neuron()`, `_find_neuron()` → `commander_neurons.py`
- `_run_allowed()`, `_publish_result()` → `commander_tasks.py`

---

## 四、预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| workflow_engine.py 行数 | 1247 | <300 (编排器) |
| yaxiio.py 行数 | 932 | <400 (主控) |
| 测试覆盖 | 0% | >20% (核心模块) |
| 单文件最大圈复杂度 | 12 | <8 |
| 架构评分 | 3/10 | 7/10 |

---

## 五、回滚预案

每个 Step 独立 commit。如果某个 Step 引入问题，只回滚该 Step，其他 Step 不受影响。Feature flag `YAXIIO_REFACTOR_V2` 控制是否使用新模块路径。

---

> **核心理念**: 不是"大改一次到位"，而是"每次只拆一个职责，拆完就跑测试，通过就提交"。
