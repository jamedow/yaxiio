# Yaxiio 系统详细方案设计

> 版本: 3.2 | 日期: 2026-05-30 | AGPLv3

---

## 一、项目概述

### 1.1 定位

Yaxiio 是一个面向 Agent 时代的**元系统**与**精炼平台**，定位为 Agent 操作系统内核。

不同于市面上绝大多数 Agent 工具解决"如何创建和编排 Agent"，Yaxiio 解决一个更根本的问题：**如何让一群 Agent 像一支训练有素的团队一样，自我管理、自我进化，而不需要人类在中间持续协调。**

核心理念是**"从用 AI 到养 AI"**——普通 Agent 框架让用户"用 AI 完成任务"，Yaxiio 让用户"养 AI 使其越用越强"。

### 1.2 设计目标

| 目标 | 说明 |
|------|------|
| **零配置智能** | 普通用户只需描述任务目标，系统自动完成拆解、调度、执行、评估和进化 |
| **专业可控** | 高级用户可通过能力卡片精确控制每个环节的参数、策略和阈值 |
| **通用性** | 系统内核与行业完全解耦，通过可插拔的能力卡片适配任何领域 |
| **自我进化** | 内置评估-进化闭环，系统越用越聪明，持续沉淀经验与技能 |
| **安全与宪法约束** | 业界唯一的运行时硬约束机制，确保所有 Agent 行为安全可控 |

### 1.3 设计哲学：Windows 隐喻

> Yaxiio 不需要两套系统。它只需要**一个内核 + 两层界面**。

Windows 让普通用户看网页打游戏，让专业用户跑虚拟机写代码——同一个 Windows。普通用户从来没打开过注册表编辑器，但它就在那里。防呆设计的本质不是"隐藏功能"，而是"普通用户不需要知道这些功能存在，系统也能做出正确的决定"。

Yaxiio 借鉴的经典系统防呆模式：

| 系统 | 防呆手段 | Yaxiio 对应 |
|------|---------|------------|
| **macOS** | 系统偏好设置 vs 终端 defaults 命令 | 能力卡片普通视图 vs 高级 JSON 编辑 |
| **VSCode** | GUI 设置面板 vs settings.json | Dashboard 表单 vs 能力卡片 YAML |
| **Git** | `git commit` vs `git commit --amend` | 标准任务提交 vs 强制覆盖模式 |
| **Docker** | `docker run` 默认安全配置 | Agent 启动默认沙箱隔离 |
| **iOS** | 引导式访问（限制到单个 App） | 宪法白名单模式 |
| **Figma** | 组件属性面板 vs 底层 JSON 导出 | 能力卡片预置模板 vs 完整 Schema |

### 1.4 vs 市面方案

| 维度 | LangChain/CrewAI | AutoGPT/Ruflo | **Yaxiio** |
|------|-----------------|---------------|------------|
| Agent 怎么管 | 靠 prompt 约束 | 靠开发者自觉 | **硬编码宪法** |
| 记忆怎么处理 | Memory 抽象 | 累积对话历史 | **模板克隆 + 仓颉三层记忆** |
| 质量怎么保证 | 重试机制 | 人工检查 | **L5 自动评分 + 差距分析 + 自进化** |
| Agent 间通信 | 函数调用 | 自然语言 (高 token) | **结构化 Schema (低 token)** |
| 出问题怎么排查 | 看日志 | 黑盒 | **全链路 TraceLogger** |
| 能力复用 | 写代码 | 锁死生态 | **能力卡片 JSON, 可插拔可跨组织** |
| 普通用户体验 | 需编程知识 | 自然语言不可控 | **零配置智能 + 专业可控** |

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
| L1 感知 | 意图识别、输入解析、工具分发 | Redis/MongoDB 客户端、MCP Registry、Skill Loader、Chroma | 3401 |
| L2 规划 | 任务拆解、模型路由、能力卡片匹配 | SemanticIntentRouter、IntelligentModelRouter、AgentFactory、RAGManager | 3402 |
| L3 调度 | 依赖分析、并行编排、优先级调度 | AsyncOrchestrator、TaskDecomposer、11-StateMachine、RedisDataBus | 3403 |
| L4 执行 | Agent 分配、沙箱执行、结果收集 | Neuron 运行时、AutoScorer、FailureDetector、TraceCollector | 3404 |
| L5 进化 | 评估、差距分析、优化、经验沉淀 | UnifiedScorer、UniversalGapAnalyzer、ExperienceFlywheel、ABTester | 3405 |

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

## 三、核心创新点

### 3.1 宪法约束系统

Yaxiio 的宪法约束是**业界独一无二的运行时硬约束机制**。Anthropic 的 Constitutional AI 在模型训练层面，Yaxiio 在 Agent 运行时编排层面。

**审查链**：白名单检查 → 禁止直接执行列表 → 危险模式匹配 → 默认委托。

**四种裁决**：ALLOWED（直通）、DELEGATED（走流水线）、REJECTED（拒绝）、DEGRADED（沙箱降级）。

所有违宪行为写入 Redis `yaxiio:constitution:violations`，不可绕过审计。`FORBIDDEN_DIRECT` 支持从 Redis 动态加载。

**防呆关联**：宪法本身就是最大的防呆——确保"不小心"提交的危险操作在运行时被拦截，不靠用户记忆。

### 3.2 能力卡片

能力卡片是 Agent 的**标准定义语言**，也是防呆设计的核心载体。同时承载默认配置和专业覆盖。

```yaml
name: "翻译官"
role: "多语言产品描述翻译专家"
quadrant: "core"
version: "2.1.0"

# ── 普通用户关心的 ──
quality: "standard"            # fast | standard | premium
description: "把产品描述翻译成目标语言"

# ── 专业用户可以覆盖的 ──
model: "deepseek-chat"
thinking: "medium"
temperature: 0.3
system_prompt: |
  你是严谨的翻译专家。职责：将产品描述准确翻译为目标语言。
skills: ["translate-engine"]
tools: ["mongo_query", "redis_query"]
input_schema: { type: object, required: ["text", "target_language"], properties: { text: { type: string }, target_language: { type: string, enum: ["ar","en","ru","es"] } } }
output_schema: { type: object, required: ["translated_text", "language"] }
lifecycle: { task_timeout: 300s, max_retries: 3, idle_timeout: 600s }
```

**`quality` 字段就是防呆开关**：选择 `fast` → 系统自动设置 flash 模型，选择 `premium` → 自动设置 max 模型 + 深度思考。用户不需要知道 `temperature` 是什么。

### 3.3 仓颉记忆系统

三层记忆架构：工作记忆 (Redis, TTL 5min) → 短期经验 (L0 库, TTL 7天) → 长期知识 (Chroma 向量DB, 按领域 TTL 7d-365d)。

四大分析模块：模式提炼器、Schema 优化器、工作流拓扑变异器、Skill 片段生成器。

**经验飞轮**：高分(≥8) → 向量化索引 + 模板回写；中分(5-7) → 创建 A/B 变体测试；低分(<5) → 记录失败模式。

### 3.4 智能意图路由

替代硬编码的 `INTENT_TOOL_MAP`（19 条 if-else），基于能力卡片向量搜索进行语义匹配。多信号融合（向量 40% + 角色 20% + Schema 25% + Skill 15%）。无匹配时触发 L5 建议新建 Agent 类型。

### 3.5 统一评分总线

融合四套评分源：RuleScorer (零成本)、CardScorer (Schema 校验)、LLMJudge (深度语义)、HybridScorer (人类校准)。三种策略：fast / standard / deep。同时提取进化信号。

### 3.6 双层守护

PM2 → Guard-Primary + Guard-Secondary (Redis Leader 选举)。三层健康检查 + 故障诊断分类 + 自动修复 + 速率限制。30 秒内接管。

---

## 四、对标分析

| 特性 | LangChain | CrewAI | AutoGPT | Ruflo | **Yaxiio** |
|------|-----------|--------|---------|-------|------------|
| 架构 | 链式/图式 | 角色协作 | 自主循环 | Swarm | **5层 MCP + 宪法** |
| Agent 隔离 | 同进程 | 同进程 | 同进程 | 同进程 | **独立进程** |
| 上下文 | Memory 抽象 | 共享 | 累积式 | 虚拟 | **Template Clone** |
| 安全 | 依赖开发者 | 无 | 无 | 无 | **宪法硬约束** |
| 自进化 | 无 | 无 | 无 | 无 | **评估-进化闭环** |
| 普通用户门槛 | 需编程 | 需编程 | 需配置 | 自然语言 | **零配置可用** |

Yaxiio 的生态位是 Agent 时代的 CI/CD 与质量保证平台——不替代编排工具，而是它们的增强层。

---

## 五、实施路线图

### Phase 1：补齐基建 (已完成 ✅)

L2 经验注入 ✅ | 统一评分总线 ✅ | 通用化 GapAnalyzer ✅ | 语义路由 ✅ | 异步调度器 ✅ | Redis Stream 数据总线 ✅ | 智能模型路由 ✅ | 经验飞轮闭合 ✅

### Phase 2：飞轮加速 (3-6 个月)

DSPy MIPROv2 集成、多 Provider 自动切换、Chroma 语义检索全覆盖、A/B 测试统计增强、任务类型自发现。

### Phase 3：生态自生长 (6-18 个月)

能力卡片市场、自动卡片生成、多 Yaxiio 联邦、Yaxiio Desktop (Tauri 200MB)、Yaxiio Lite (树莓派可跑)。

---

## 六、防呆设计体系

### 6.1 为什么防呆是 Yaxiio 走向生产的关键

Agent 框架的现状是"给开发者用的工具"——需要写代码、配参数、读日志。这让 99% 的潜在用户（外贸老板、工厂厂长、运维工程师）被挡在门外。

Yaxiio 要做的不是另一个开发者工具，而是"Agent 操作系统"——像 Windows 一样，普通人能用，专家能深入。这中间的桥梁就是防呆设计。

### 6.2 理论基础

防呆设计（Poka-Yoke）源于丰田生产系统，核心思想是**在错误发生之前阻止它**。

**Nielsen 十大可用性原则在 Yaxiio 中的映射**：

| 原则 | Yaxiio 实现 |
|------|------------|
| 系统状态可见性 | TraceLogger 全链路追踪，`/trace/:id` 实时查询 |
| 系统与真实世界匹配 | 能力卡片用行业术语（翻译官、审计官），不暴露技术参数 |
| 用户控制与自由 | 所有自动操作支持手动覆盖，L5 进化保留旧版本 7 天可回滚 |
| 一致性与标准 | 所有 Agent 使用统一的能力卡片格式，输入输出 Schema 标准化 |
| 错误预防 | 宪法硬约束阻止危险操作，能力卡片 `quality` 替代裸参数 |
| 识别而非回忆 | Dashboard 下拉选择 Agent 而非手写 YAML，自动补全可用工具 |
| 灵活性与效率 | 普通视图一键操作，高级视图精确控制，VSCode 式渐进暴露 |
| 美学与极简设计 | 普通视图只显示 3-5 个关键字段，其余智能默认 |
| 帮助诊断和恢复错误 | 失败任务自动诊断 + 修复建议 + 一键重试 |
| 帮助与文档 | 每张能力卡片自带 description 和 system_prompt 作为内联文档 |

**Shneiderman 八条黄金规则**：

| 规则 | Yaxiio 落地 |
|------|------------|
| 追求一致性 | 所有 Agent 共享 neuron.py 运行时，行为模式统一 |
| 允许频繁用户使用快捷键 | CLI 快捷别名：`yx` 进入容器，`yx-logs` 查看日志 |
| 提供信息反馈 | 每个任务完成即时推送评分 + 改进建议 |
| 设计对话以产生闭合 | L5 verdict: pass/retry/reject 明确终止状态 |
| 提供简单错误处理 | Guardian 自动诊断 + 修复，用户无感 |
| 允许轻松逆转操作 | 模板版本历史，L5 进化一键回滚 |
| 支持内部控制点 | 专业用户可手动管理 Agent 生命周期 |
| 减少短期记忆负担 | 仓颉记忆系统自动检索历史经验，用户无需记忆 |

### 6.3 瑞士奶酪模型：五层重叠防护

参考 Reason 的 Swiss Cheese Model——不是一道屏障，而是**五层重叠防护**：

```
用户操作
  │
  ▼
[第一层] 默认值防护  ← 不做选择就不会选错
  quality: "standard" 替代裸参数，系统自动选最优
  │
  ▼
[第二层] 输入校验    ← 选了也检查是否合法
  Schema validation, enum/max/min 限制
  │
  ▼
[第三层] 宪法硬拦截  ← 通过了校验也不一定放行
  DANGEROUS_PATTERNS 运行时检测，FORBIDDEN_DIRECT 强制流水线
  │
  ▼
[第四层] 运行时沙箱  ← 放行了也在隔离环境跑
  DinD 容器隔离，资源限制 (4GB/2CPU)
  │
  ▼
[第五层] 事后审计+回滚 ← 跑完了也能追溯和撤销
  TraceLogger 全链路，模板版本历史 (7天)，违宪审计日志不可绕过
```

### 6.4 五种核心防呆模式

#### 模式一：用语义替代裸参数

**❌ 裸参数**：`model: "deepseek-chat", thinking: "high", temperature: 0.1, max_tokens: 8192, max_retries: 5`

**✅ 语义封装**：`quality: "premium"` → 系统自动映射到上面的参数组合。

```python
QUALITY_PRESETS = {
    "fast":     {"model": "deepseek-flash", "thinking": "off",  "max_retries": 1},
    "standard": {"model": "deepseek-chat",  "thinking": "medium", "max_retries": 3},
    "premium":  {"model": "deepseek-max",   "thinking": "high",  "max_retries": 5},
}
```

#### 模式二：渐进式信息披露

借鉴 VSCode 设置系统——默认 GUI 表单，高级用户通过 JSON 精确控制。

```
用户等级              看到什么                      可编辑什么
─────────────────────────────────────────────────────────
Level 0 (访客)       Dashboard 概览                 无
Level 1 (普通用户)    能力卡片普通视图 (3-5 字段)       quality, description
Level 2 (高级用户)    能力卡片完整视图 (20+ 字段)       所有参数
Level 3 (开发者)      能力卡片 YAML 源码 + API 文档     全部 + 自定义 Schema
```

#### 模式三：安全的默认值

借鉴 Docker 的安全默认——默认不挂载宿主机，默认限制资源。

| 参数 | 默认值 | 理由 |
|------|--------|------|
| `task_timeout` | 300s | 防止单任务无限运行 |
| `max_retries` | 3 | 避免无限重试浪费 |
| `sandbox` | 开启 | 代码执行默认隔离 |
| `audit_logging` | 开启 | 审计不可绕过 |
| `agent_quadrant` | ephemeral | 默认用完即弃 |

#### 模式四：后悔机制

借鉴 macOS 还原和 Git reflog。

| 操作 | 后悔窗口 | 实现 |
|------|---------|------|
| L5 自动进化替换模板 | 7 天 | 旧版本保存为 `card:v{N-1}` |
| 删除能力卡片 | 30 天 | 软删除，标记 deleted_at |
| 销毁 Agent | 按四象限 | ephemeral 不保留，strategic 保留 24h |
| 修改核心配置 | 永久 | 配置文件纳入 Git 版本控制 |

#### 模式五：智能降级而非崩溃

借鉴 Kubernetes 优雅降级——不是所有组件都必须健康。

```
L5 LLMJudge 挂了 → 降级到 RuleScorer + CardScorer → 评分继续
L2 MCP Server 挂了 → 降级到 LLM 直接拆解 → 规划继续
Agent 进程崩溃   → Guardian 自动重启 → 任务从检查点继续
Redis 挂了       → 降级到 SQLite 本地缓存 → 核心功能继续
```

### 6.5 危险操作分级保护

借鉴 Git 的 `--force-with-lease` 和 iOS 引导式访问：

| 等级 | 操作示例 | 防呆措施 |
|------|---------|---------|
| 🟢 低 | 创建新 Agent、修改非关键参数 | 无确认，直接执行 |
| 🟡 中 | 修改评分阈值、调整 Agent 四象限 | 轻提示："已修改，可在 7 天内恢复" |
| 🟠 高 | 删除 Skill、销毁 Strategic Agent | 弹窗确认 + 显示影响范围 |
| 🔴 严重 | 销毁 Core Agent、修改宪法白名单 | 双重确认 + 10 秒冷静期 + 输入名称确认 |
| ⚫ 灾难 | 重置经验库、删除所有 Agent | 输入 "I UNDERSTAND" + 二次验证码 |

### 6.6 能力卡片的高级模式

防呆设计最集中的体现——同一张卡片，三种视图，由 `_ui_meta` 控制渲染：

```yaml
name: "翻译官"
quality: "standard"           # ← ui_level:1 始终显示
model: "deepseek-chat"        # ← ui_level:2 高级模式显示
thinking: "medium"            # ← ui_level:2 高级模式显示
temperature: 0.3              # ← ui_level:3 开发者模式显示

_ui_meta:
  quality:
    ui_level: 1
    ui_control: "select"
    ui_options: ["fast", "standard", "premium"]
    ui_hint: "翻译质量等级"
  model:
    ui_level: 2
    ui_control: "select"
    ui_options: ["auto", "deepseek-flash", "deepseek-chat", "deepseek-max"]
    ui_hint: "默认 auto 由系统自动选择最优模型"
```

### 6.7 五层架构中的防呆分布

| 层 | 防呆机制 |
|----|---------|
| L1 感知 | 输入 Schema 校验、危险意图关键词过滤、重复任务去重 |
| L2 规划 | quality 预设映射、模型路由安全默认值、经验注入不覆盖用户意图 |
| L3 调度 | 优先级队列防饿死、并发数上限防耗尽、依赖图循环检测 |
| L4 执行 | DinD 沙箱隔离、超时自动 kill、输出大小限制、子进程数限制 |
| L5 进化 | 低分不污染经验库 (≥7 才写入)、模板回写保留历史、A/B 渐进放量 |

### 6.8 刻意避免的反模式

| 反模式 | 为什么不采用 |
|--------|------------|
| 每次操作都弹窗确认 | 弹窗疲劳，用户条件反射点"确定" |
| 隐藏所有高级功能 | 专业用户找不到入口，转而绕过系统 |
| 过度的输入校验 | 拒绝合法但不常见的输入，阻碍探索 |
| 强制向导模式 | 剥夺用户控制感，降低信任 |
| 禁止一切危险操作 | 专业场景需要灵活性，应允许但确认 |

**核心原则**：防呆不是限制能力，而是**让正确的操作更容易，让错误的操作更难**。

---

## 七、技术栈与部署

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.12 |
| 通信 | Redis Pub/Sub → Streams (Phase 2) |
| 存储 | Redis + MongoDB (可降级 SQLite) |
| 向量 | Chroma / MemVectorStore 兜底 |
| 进程 | PM2 双守护 |
| 容器 | Docker + DinD 沙箱 |
| 桌面 | Tauri (未来) |

### 最小部署规格

| 场景 | 内存 | 磁盘 |
|------|------|------|
| 开发环境 | 512MB | 200MB |
| 生产环境 | 2GB | 1GB |
| 树莓派 | 256MB | 100MB |

---

## 八、当前实现状态

### 已落地 (v3.2)

| 模块 | 状态 |
|------|------|
| 宪法约束 (四裁决 + Redis 动态配置) | ✅ |
| 能力卡片 (AGENT_CONFIG 文件+Redis 双源) | ✅ |
| 语义路由 (SemanticIntentRouter 零硬编码) | ✅ |
| 模型路由 (IntelligentModelRouter 多目标优化) | ✅ |
| 异步调度 (AsyncOrchestrator 默认启用) | ✅ |
| 数据总线 (RedisDataBus Stream 中转) | ✅ |
| 统一评分 (UnifiedScorer 4 源融合) | ✅ |
| 差距分析 (UniversalGapAnalyzer 零硬编码) | ✅ |
| 经验飞轮 (ExperienceFlywheel 闭合循环) | ✅ |
| 经验注入 (L0→LLM prompt) | ✅ |
| 双层守护 (诊断+修复+限速) | ✅ |

### 待建设 (Phase 2+)

| 模块 | 优先级 |
|------|--------|
| quality 预设映射（防呆模式一） | P1 |
| 渐进式信息披露 UI（防呆模式二） | P1 |
| DSPy MIPROv2 集成 | P1 |
| 多 Provider 自动切换 | P1 |
| 后悔机制（模板版本历史） | P2 |
| 智能降级网络 | P2 |
| 危险操作分级保护 | P2 |
| Chroma 语义检索全覆盖 | P2 |
| 能力卡片市场 | P3 |
| Yaxiio Desktop | P3 |

---

## 九、总结

Yaxiio 是一套有远见的设计，其**宪法约束、能力卡片、仓颉记忆系统、经验飞轮**构成了多层护城河。与市面上"功能堆叠型"的 Agent 框架不同，Yaxiio 走的是"自我进化型"路线——用户量不是护城河，**用户量 × 时间 = 数据飞轮加速度**才是。

防呆设计不是事后追加的 UI 美化，而是 Yaxiio 的内核哲学：**让正确的操作更容易，让错误的操作更难**。通过五层重叠防护、五种防呆模式、五级危险操作保护，以及借鉴 Windows/VSCode/Docker/Git 等成熟系统的设计智慧，Yaxiio 实现了"一个内核、两层界面"的产品架构——普通人三分钟上手，专家可以深入每个参数。

当前 v3.2 已完成基建闭环，防呆设计的内核（宪法约束、quality 字段、模板克隆）已在生产环境中运行。Phase 2 将把防呆体系从内核扩展到全链路，让 Yaxiio 真正成为 Agent 时代的"质量操作系统"。
