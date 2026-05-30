# Yaxiio 系统详细方案设计

> 版本: 3.1 | 日期: 2026-05-30 | AGPLv3

---

## 一、项目概述

### 1.1 定位

Yaxiio 是一个面向 Agent 时代的**元系统**与**精炼平台**，定位为 Agent 操作系统内核。

不同于市面上绝大多数 Agent 工具——它们解决"如何创建和编排 Agent"的问题——Yaxiio 解决的是一个更根本的问题：**如何让一群 Agent 像一支训练有素的团队一样，自我管理、自我进化，而不需要人类在中间持续协调。**

核心理念是**"从用 AI 到养 AI"**。普通 Agent 框架让用户"用 AI 完成任务"，Yaxiio 让用户"养 AI 使其越用越强"。

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **零配置智能** | 普通用户只需描述任务目标，Yaxiio 自动完成拆解、调度、执行、评估和进化 |
| **专业可控** | 高级用户可通过能力卡片精确控制每个环节的参数、策略和阈值 |
| **通用性** | 系统内核与行业完全解耦，通过可插拔的能力卡片适配任何领域 |
| **自我进化** | 内置评估-进化闭环，系统越用越聪明，持续沉淀经验与技能 |
| **安全与宪法约束** | 业界唯一的运行时硬约束机制，确保所有 Agent 行为安全可控 |

### 1.3 vs 市面方案

| 维度 | LangChain/CrewAI | AutoGPT/Ruflo | **Yaxiio** |
|------|-----------------|---------------|------------|
| Agent 怎么管 | 靠 prompt 约束 | 靠开发者自觉 | **硬编码宪法** |
| 记忆怎么处理 | Memory 抽象 | 累积对话历史 | **模板克隆 + 仓颉三层记忆** |
| 质量怎么保证 | 重试机制 | 人工检查 | **L5 自动评分 + 差距分析 + 自进化** |
| Agent 间通信 | 函数调用 | 自然语言 (高 token) | **结构化 Schema (低 token)** |
| 出问题怎么排查 | 看日志 | 黑盒 | **全链路 TraceLogger, /trace/:id 可追溯** |
| 能力复用 | 写代码 | 锁死生态 | **能力卡片 JSON, 可插拔可跨组织** |

---

## 二、总体架构：五层模块化进化架构

### 2.1 全景图

```
用户请求 → Gateway (WebSocket :3398 / HTTP :3399)
                │
                ▼
          Commander (宪法审查 + 工作流总指挥)
                │
   ┌────────────┼────────────┐
   ▼            ▼            ▼
 L1 感知     L2 规划      L3 调度
(什么?)     (怎么拆?)    (谁来做?)
 :3401       :3402        :3403
   │            │            │
   └────────────┼────────────┘
                ▼
           L4 执行 → Agent 池 (独立进程, DinD 沙箱)
           :3404
                │
                ▼
           L5 进化 → 评分 → 差距分析 → 经验回写 → DSPy 优化
           :3405
                │
     ┌─────────┴─────────┐
     ▼                   ▼
 仓颉记忆            能力卡片模板
(三层记忆架构)      (自动回写提升基线)
```

### 2.2 各层职责

| 层 | 核心职责 | 关键组件 | 端口 |
|----|---------|---------|------|
| **L1 感知** | 意图识别、输入解析、工具分发 | Redis/MongoDB 客户端、MCP Registry、Skill Loader、Chroma 向量存储 | 3401 |
| **L2 规划** | 任务拆解、模型路由、能力卡片匹配 | SemanticIntentRouter、IntelligentModelRouter、AgentFactory、RAGManager | 3402 |
| **L3 调度** | 依赖分析、并行编排、优先级调度、状态管理 | AsyncOrchestrator、TaskDecomposer、11-StateMachine、RedisDataBus | 3403 |
| **L4 执行** | Agent 分配、沙箱执行、结果收集、追踪审计 | Neuron 运行时、AutoScorer、FailureDetector、TraceCollector | 3404 |
| **L5 进化** | 多维度评估、差距分析、Prompt 优化、经验沉淀 | UnifiedScorer、UniversalGapAnalyzer、ExperienceFlywheel、ABTester | 3405 |

### 2.3 设计原则

1. **MCP-First** — 所有任务默认走 L1→L5 流水线
2. **纯编排** — Commander 只做路由和调度，不亲自执行
3. **白名单准入** — 只有宪法明确授权的操作可绕过流水线
4. **LLM 决策** — 任务理解和分派由 LLM 驱动，禁止硬编码分支
5. **沙箱隔离** — 代码执行必须在 L4 sandbox 内（DinD 容器级）
6. **审计不可绕过** — 所有操作记录在案
7. **模板克隆** — 每任务新 Agent 实例，上下文零泄露
8. **渐进修复** — 故障先诊断再修复，不盲目重启

---

## 三、核心创新点详解

### 3.1 宪法约束系统 (Constitution)

Yaxiio 的宪法约束是**业界独一无二的运行时硬约束机制**。Anthropic 的 Constitutional AI 在模型训练层面，Yaxiio 在 Agent 运行时编排层面。

**审查链**：
1. **白名单检查** — `SYSTEM_OPS` 中的 6 个系统管理操作直接放行
2. **禁止直接执行** — `FORBIDDEN_DIRECT` 中的业务操作强制走 L1→L5 流水线
3. **危险模式匹配** — 检测 `docker exec`、`rm -rf`、`eval()` 等，触发沙箱降级
4. **默认委托** — 所有未明确允许的操作走标准流水线

**四种裁决**：
```
constitution.review(action, payload)
  → ALLOWED    — 系统白名单，Commander 直通执行
  → DELEGATED  — 必须走 L1→L5 MCP 流水线
  → REJECTED   — 违宪，拒绝执行
  → DEGRADED   — 高危操作，强制 sandbox 降级
```

所有违宪行为写入 Redis `yaxiio:constitution:violations`（最近 100 条），不可绕过审计。`FORBIDDEN_DIRECT` 支持从 Redis 动态加载，无需修改代码即可调整。

### 3.2 能力卡片 (Capability Card)

能力卡片是 Agent 的**标准定义语言**，实现"即插即用"。卡片包含六要素：

```yaml
name: "翻译官"
role: "多语言产品描述翻译专家"
quadrant: "core"              # core | strategic | utility | ephemeral
version: "2.1.0"

# 大脑配置
model: "deepseek-chat"
thinking: "medium"
temperature: 0.3
system_prompt: |
  你是严谨的翻译专家。职责：将产品描述准确翻译为目标语言。
  原则：不添加、不遗漏、不曲解原文信息。

# 工具箱
skills: ["translate-engine"]
tools: ["mongo_query", "redis_query"]

# 接口契约
input_schema:
  type: object
  required: ["text", "target_language"]
  properties:
    text: { type: string }
    target_language: { type: string, enum: ["ar", "en", "ru", "es"] }

output_schema:
  type: object
  required: ["translated_text", "language"]
  properties:
    translated_text: { type: string }
    language: { type: string }

# 生命周期
lifecycle:
  task_timeout: 300s
  max_retries: 3
  idle_timeout: 600s
  heartbeat_interval: 30s
```

**防呆设计**：普通用户使用默认卡片，专业用户可打开"高级模式"精确控制所有参数。同一张卡片，同一套系统——区别只在于参数由系统预填还是用户手填。

### 3.3 仓颉记忆系统 (Cangjie)

仓颉是 L5 进化层的核心引擎，实现从"记住"到"学会"的质变。

**三层记忆架构**：
```
工作记忆 (Redis Session, TTL 5min)
  → 短期经验 (L0 经验库, TTL 7天, 50条/意图)
    → 长期知识 (Chroma 向量DB, 按领域 TTL 7d-365d)
```

**四大分析模块**：
- **模式提炼器**：分析低分任务的共性失败模式，生成规则补丁
- **Schema 优化器**：自动优化 Agent 的输入输出 Schema
- **工作流拓扑变异器**：探索更优的 Agent 协作流程
- **Skill 片段生成器**：从高分任务中提取可复用的技能片段

**经验飞轮闭环**：
```
任务执行 → L5 评分
  ├─ 高分(≥8) → 向量化索引 → 模板回写 → 下次克隆从优化后模板起步
  ├─ 中分(5-7) → 创建 A/B 变体 → 自动测试 → 选优回写
  └─ 低分(<5) → 记录失败模式 → 避免重复
```

### 3.4 智能意图路由 (SemanticIntentRouter)

替代传统的硬编码 `INTENT_TOOL_MAP`（19 条 if-else），基于能力卡片进行语义匹配：

```
输入："把 500 条电力行业产品描述翻译成阿拉伯语"
  → 向量搜索 Agent 能力卡片
  → 多信号融合置信度（向量相似度 40% + 角色匹配 20% + Schema 匹配 25% + Skill 匹配 15%）
  → 输出：{"primary_agent": "翻译官", "confidence": 0.92}
  → 无匹配时触发 L5 建议新建 Agent 类型
```

### 3.5 统一评分总线 (UnifiedScorer)

融合四套评分源为统一出口：

| 评分源 | 成本 | 作用 |
|--------|------|------|
| RuleScorer (AutoScorer) | 零成本 | 快速规则评分 |
| CardScorer | 零成本 | 能力卡片 Schema 校验 |
| LLMJudge | 高成本 | 深度语义评估 |
| HybridScorer | 异步 | 人类反馈校准 |

三种策略：`fast`（仅规则+卡片）、`standard`（+LLM 降级）、`deep`（全源）。同时提取进化信号：prompt 优化需求、知识缺口、Agent 不匹配、模板提升建议。

### 3.6 双层守护 (Guardian)

```
PM2 → Guard-Primary + Guard-Secondary (Redis Leader 选举)
        ├─ 管理 Commander (健康检查/诊断/修复/重启)
        └─ Secondary 监控 Primary 心跳，30s 内接管
```

三层健康检查：进程存活 (pgrep) → Redis PING → HTTP API。故障诊断分类：`FAULT_REDIS` / `FAULT_MODELS` / `FAULT_APIKEY` / `FAULT_UNKNOWN`，按类型自动修复。速率限制：2 分钟内最多 3 次重启，超限暂停等人工。

---

## 四、对标分析与生态位

### 4.1 综合对比

| 特性 | LangChain/LangGraph | CrewAI | AutoGPT | Ruflo | **Yaxiio** |
|------|---------------------|--------|---------|-------|------------|
| 架构理念 | 链式/图式组合 | 角色协作 | 自主循环 | Swarm 协作 | **5层 MCP + 宪法** |
| Agent 隔离 | 同进程 | 同进程 | 同进程 | 同进程 | **独立进程 (最强)** |
| 上下文管理 | Memory 抽象 | 共享上下文 | 累积式 | 虚拟上下文 | **Template Clone** |
| 安全约束 | 依赖开发者 | 无 | 无 | 无 | **宪法硬约束 (独有)** |
| 自进化 | 无 | 无 | 无 | 无 | **评估-进化闭环** |
| 可观测性 | LangSmith (商业化) | 无 | 无 | 基础 | **全链路 TraceLogger** |
| 成熟度 | 生产级 | 快速迭代 | 实验性 | 早期 | **MVP→1.0** |

### 4.2 Yaxiio 的独特生态位

Yaxiio 并非现有工具的竞争者，而是其**增强层**：

- **与编排工具 (Ruflo, CrewAI)**：不替代它们，而是为它们产出的 Agent 提供评估、优化和进化服务
- **与 Agent 市场 (阿里)**：成为市场的"品质保证"，提供标准化评估和自动优化
- **终极定位**：Agent 时代的 CI/CD 与质量保证平台

---

## 五、实施路线图

### Phase 1：补齐基建，闭合飞轮 (已完成 ✅)

| 优先级 | 任务 | 状态 |
|--------|------|------|
| P0 | 修复 L2 经验注入 — L0 检索的经验注入 LLM prompt | ✅ |
| P0 | 实现统一评分总线 — UnifiedScorer 融合 4 源 | ✅ |
| P0 | 通用化 GapAnalyzer — 零行业硬编码 | ✅ |
| P1 | 能力卡片驱动的 Agent 创建 — spawn_neuron 传入 AGENT_CONFIG | ✅ |
| P1 | 语义意图路由 — SemanticIntentRouter 替代 INTENT_TOOL_MAP | ✅ |
| P1 | 异步调度器 — AsyncOrchestrator 替代线程池 | ✅ |
| P2 | Redis Stream 数据总线 — RedisDataBus 替代文件快照 | ✅ |
| P2 | 智能模型路由 — IntelligentModelRouter 多目标优化 | ✅ |

### Phase 2：飞轮加速，越用越聪明 (3-6 个月)

| 任务 | 说明 |
|------|------|
| 经验飞轮闭环 | 高分模板自动回写，低分创建 A/B 变体测试 |
| DSPy 真正集成 | 安装 dspy 库，MIPROv2 优化，few-shot 自动编译 |
| 多 Provider 自动切换 | 一个 Provider 挂了自动切到备选 |
| Chroma 语义经验检索 | 从 Redis List 迁移到 Chroma 向量检索 |
| 任务类型自发现 | L5 分析多次出现的 Ephemeral 模式，建议沉淀为新类型 |

### Phase 3：生态自生长 (6-18 个月)

| 任务 | 说明 |
|------|------|
| 能力卡片市场 | 用户可导出/导入/交易能力卡片 |
| 自动能力卡片生成 | "我需要一个合同审查官" → L5 分析 → 自动生成 → 人工确认 |
| 多 Yaxiio 联邦 | 多个实例通过 MCP 协议互联协作 |
| Yaxiio Desktop | Tauri 壳 + SQLite → 200MB 安装包，系统托盘静默运行 |
| Yaxiio Lite | 无 Redis/MongoDB 依赖，树莓派可跑 |

---

## 六、防呆设计与用户体验

### 6.1 核心原则

> Yaxiio 不需要两套系统。它只需要**一个内核 + 两层界面**。

**原则一：所有参数都有默认值，但所有默认值都可以被覆盖。**
这不是"要么傻瓜，要么专业"的二选一，而是"傻瓜是默认状态，专业是按需激活"。

**原则二：系统只暴露用户需要关心的决策点。**
Windows 不会问普通用户"你要分配多少内存给浏览器"，它自己决定。但任务管理器里，专业用户可以看到这些。

**原则三：危险操作必须有确认，但不过度打断。**
Windows 删除文件会弹窗确认，但不会每次打开文件都弹窗。

### 6.2 普通视图 vs 专业视图

| 决策点 | 普通用户看到什么 | 专业用户可以做什么 |
|--------|---------------|------------------|
| 模型选择 | 无感知，系统自动选 | 手动指定 Max / High / Flash |
| 重试策略 | 自动重试，用户无感 | 配置重试次数、退避策略、降级方案 |
| 评分标准 | 简单的"通过 / 不通过" | 多维度评分雷达图 + 每个维度的详细扣分项 |
| 进化触发 | 后台自动优化 | 手动触发、查看 A/B 测试数据、回滚版本 |
| Agent 创建 | 系统自动创建和销毁 | 手动管理 Agent 生命周期、查看心跳、调整四象限 |

### 6.3 危险操作保护

| 操作 | 防呆措施 |
|------|---------|
| 删除正在使用的 Skill | 弹窗：该 Skill 正被 N 个 Agent 使用，确认删除？ |
| 修改核心配置文件 | 弹窗：这会影响系统稳定性，建议先备份 |
| 手动销毁 Core Agent | 弹窗：该 Agent 是核心服务，销毁后系统会自动重建 |
| L5 进化替换旧版本 | 不弹窗，保留旧版本 7 天，支持一键回滚 |
| 大规模批量操作 | 显示预估影响范围 + 确认 |

### 6.4 能力卡片的高级模式

这是最典型的防呆实现——同一张卡片，两种视图：

```
普通视图（默认）：                 专业视图（点击"高级"展开）：
┌─────────────────────┐           ┌─────────────────────────┐
│ name: "翻译官"       │           │ name: "翻译官"           │
│ quality: [标准 ▾]   │  ──→     │ model: "deepseek-chat"   │
│ description: "..."   │           │ thinking: "medium"       │
│                      │           │ temperature: 0.3         │
│         [保存]       │           │ max_tokens: 4096         │
└─────────────────────┘           │ few_shot_examples: [...]  │
                                  │              [保存]      │
                                  └─────────────────────────┘
```

当用户选择 `quality: "快速"` → 系统自动设置 `model: flash, thinking: off`
当用户选择 `quality: "高质量"` → 系统自动设置 `model: max, thinking: high`

普通用户不需要知道 `temperature` 是什么，但专业用户可以手动覆盖。

---

## 七、技术栈与部署

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.12 |
| 通信 | Redis Pub/Sub → Redis Streams (Phase 2) |
| 存储 | Redis + MongoDB (开发环境可降级 SQLite) |
| 向量 | Chroma (可选) / MemVectorStore (兜底) |
| 进程管理 | PM2 双守护 |
| 容器化 | Docker + DinD 沙箱 |
| 桌面 | Tauri (未来) |

### 最小部署规格

| 场景 | 内存 | 磁盘 |
|------|------|------|
| 开发环境 | 512MB (Redis + SQLite) | 200MB |
| 生产环境 | 2GB (Redis + MongoDB + Chroma) | 1GB |
| 树莓派 | 256MB (SQLite 单文件) | 100MB |

---

## 八、当前实现状态

### 已落地 (v3.1)

| 模块 | 文件 | 状态 |
|------|------|------|
| 宪法约束 | `constitution.py` | ✅ 四裁决 + Redis 动态配置 |
| 能力卡片 | `neuron.py` (AGENT_CONFIG) | ✅ 文件+Redis 双源加载 |
| 语义路由 | `modules/layer2/intent_router.py` | ✅ 零硬编码 |
| 模型路由 | `modules/layer2/model_router_v2.py` | ✅ 多目标优化 |
| 异步调度 | `modules/layer3/async_orchestrator.py` | ✅ 默认启用 |
| 数据总线 | `modules/layer3/redis_data_bus.py` | ✅ Stream 中转 |
| 统一评分 | `modules/layer5/unified_scorer.py` | ✅ 4 源融合 |
| 差距分析 | `modules/layer5/gap_analyzer_v2.py` | ✅ 零行业硬编码 |
| 经验飞轮 | `modules/layer5/experience_flywheel.py` | ✅ 闭合循环 |
| 经验注入 | `workflow_engine._decompose_via_l2` | ✅ L0→LLM |
| 双层守护 | `pi_guardian_v3.py` | ✅ 诊断+修复+限速 |

### 待建设 (Phase 2+)

| 模块 | 优先级 |
|------|--------|
| DSPy MIPROv2 集成 | P1 |
| 多 Provider 自动切换 | P1 |
| Chroma 语义检索全覆盖 | P2 |
| A/B 测试统计增强 | P2 |
| 能力卡片市场 | P3 |
| Yaxiio Desktop | P3 |

---

## 九、总结

Yaxiio 是一套有远见的设计，其**宪法约束、能力卡片、仓颉记忆系统、经验飞轮**构成了多层护城河。与市面上"功能堆叠型"的 Agent 框架不同，Yaxiio 走的是"自我进化型"路线——用户量不是护城河，**用户量 × 时间 = 数据飞轮加速度**才是。

当前 v3.1 已完成 Phase 1 全部目标：核心循环可用，12 项基建全部闭合。通过防呆设计，Yaxiio 将同时服务普通用户与专业人士，成为 Agent 时代的"质量操作系统"。
