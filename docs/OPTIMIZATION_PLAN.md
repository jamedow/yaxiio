# Yaxiio 深度优化方案

> 版本: 1.0 | 日期: 2026-05-29
> 状态: 设计完成，待实施
> 范围: L2 规划层 + L3 调度层 + L5 进化层全面重构

---

## 一、问题诊断总览

### 1.1 核心矛盾

| 维度 | 设计文档承诺 | 代码实际实现 | 差距等级 |
|------|------------|-------------|---------|
| L2 意图路由 | 能力卡片语义匹配 | `INTENT_TOOL_MAP` 19条硬编码 | 🔴 严重 |
| L2 经验注入 | L0 经验检索 → 注入 LLM prompt | 检索到经验但只打日志未注入 | 🔴 严重 |
| L2 模型路由 | 按任务复杂度/成本/能力选模型 | 3条规则9个中文关键词 | 🟡 中等 |
| L3 任务调度 | 事件驱动 + 优先级队列 + 负载均衡 | 线程池 + 同步阻塞 HTTP + 轮询 | 🔴 严重 |
| L3 数据中转 | Redis Stream / 消息队列 | 文件系统 JSON 快照 | 🟡 中等 |
| L5 评分系统 | 统一多维度评分 | 三套评分系统互不调用 | 🔴 严重 |
| L5 差距分析 | 通用差距识别 → 行动推荐 | 硬编码外贸行业关键词 | 🔴 严重 |
| L5 经验飞轮 | 高分回写模板 → 下次克隆优化 | 经验只存不用，飞轮断裂 | 🔴 严重 |
| L5 DSPy 优化 | 自动 Prompt 编译优化 | ImportError 静默降级 | 🟡 中等 |

### 1.2 三阶段路线

```
Phase 1 (1-2月)  补齐基建: 让设计文档承诺的真的能工作
Phase 2 (3-6月)  飞轮加速: 让系统越用越聪明
Phase 3 (6-18月) 生态自生长: 让用户不依赖 Yaxiio 开发者
```

---

## 二、Phase 1 新建文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| `intent_router.py` | `modules/layer2/` | 语义意图路由器，替代 INTENT_TOOL_MAP |
| `model_router_v2.py` | `modules/layer2/` | 智能模型路由器，多目标优化 |
| `async_orchestrator.py` | `modules/layer3/` | 异步事件驱动调度器 |
| `redis_data_bus.py` | `modules/layer3/` | Redis Stream 数据中转总线 |
| `unified_scorer.py` | `modules/layer5/` | 统一评分总线，融合所有评分源 |
| `gap_analyzer_v2.py` | `modules/layer5/` | 通用差距分析器，零行业硬编码 |
| `experience_flywheel.py` | `modules/layer5/` | 经验飞轮，闭合存→取→改善循环 |

## 三、Phase 1 修改文件清单

| 文件 | 修改点 |
|------|--------|
| `workflow_engine.py` | `_decompose_via_l2()` 注入 L0 经验；`_orchestrate_subtasks()` 对接异步调度器；`_do_L5()` 对接统一评分总线 |
| `neuron.py` | 支持 `AGENT_CONFIG` 环境变量，从能力卡片启动 |
| `constitution.py` | `FORBIDDEN_DIRECT` 迁移到 Redis 配置文件 |
| `modules/layer2/__init__.py` | 导出新增模块 |

---

## 四、Phase 2 详细方案

| 任务 | 涉及文件 |
|------|---------|
| A/B 测试自动化 | `modules/layer5/ab_tester.py` 增强 |
| DSPy MIPROv2 集成 | `dspy_optimizer.py` → 真正安装 dspy 库 |
| 多 Provider 自动切换 | `modules/layer2/multi_provider.py` 增强 |
| Chroma 语义经验检索 | 从 Redis List 迁移到 ChromaVectorStore |
| 模板自动回写 | `experience_flywheel.py` → `_promote_template()` |

---

## 五、Phase 3 详细方案

| 任务 | 涉及文件 |
|------|---------|
| 能力卡片市场 | 新建 `card_marketplace.py` |
| 自动能力卡片生成 | 在 L5 中新增 `generate_agent_card` 方法 |
| 多 Yaxiio 联邦 | MCP 协议跨实例通信 |
| Yaxiio Desktop | Tauri 壳 + SQLite 单文件 |

---

## 六、关键设计决策记录

### 决策 9: 统一评分总线 vs 保留多套评分器

**选**: 统一评分总线（`UnifiedScorer`），将 `AutoScorer`、`LLMJudge`、`HybridScorer` 作为评分源接入。

**理由**: 三套评分器并行存在是设计文档与代码的最大鸿沟之一。统一评分总线不是废弃任一评分器，而是让它们各司其职：AutoScorer 提供零成本快速评分，LLMJudge 提供深度评分，HybridScorer 提供人类校准。由总线决定何时使用哪个。

### 决策 10: 异步调度 vs 线程池

**选**: asyncio 事件驱动（`AsyncOrchestrator`），替代 `ThreadPoolExecutor`。

**理由**: 当前线程池的方案受限于 Python GIL 和同步 HTTP 调用——5 个 worker 全部阻塞在 `call_layer(4, "dispatch_and_await", timeout=60)` 上时，第 6 个子任务即使依赖已满足也无法被调度。asyncio 可以同时发射 N 个 HTTP 请求而不阻塞调度循环。

### 决策 11: 经验向量化 vs Redis List

**选**: Chroma 向量存储替代 Redis List 作为主检索方式，Redis List 保留为快速缓存。

**理由**: Redis List 是 FIFO 队列，不支持语义搜索。"翻译 500 条电力行业描述"和"翻译 300 条矿业设备参数"在 Redis List 中是两条独立记录，无法通过"翻译工业产品"检索到。向量化后，语义相近的经验可以被自动召回。

### 决策 12: 通用化 GapAnalyzer vs 保留行业关键词

**选**: 完全通用化，基于能力卡片的 `output_schema` 和 L5 评分维度，不假设任何行业。

**理由**: Yaxiio 的定位是"通用 Agent 操作系统内核"。`mixed_lang`、`空字段`、`缺页` 是 LightingMetal 外贸网站的特定问题，硬编码在差距分析器中与通用定位根本矛盾。

---

> **核心主张**: Yaxiio 真正的护城河不是五层架构，不是宪法约束，而是"经验向量化 + 模板自动回写"的数据飞轮。当前代码中这条飞轮是断裂的——修复它应该是 Phase 1 的最高优先级。
