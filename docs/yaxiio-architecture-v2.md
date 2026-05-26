# 雅溪 Yaxiio v1.0 — 五层模块化架构 (修正版)

> 五金外贸B2B智能调度系统 · 2026 AI元年
> 设计来源: EvoAgentX 核心思想
> 原则: 每层独立职责，标准接口通信，可独立升级

---

## 架构全景

```
+------------------------------------------------------------------+
|                    L5 进化层 (Evolution)                          |
|  +----------+  +----------+  +----------+  +--------------+      |
|  |提示词优化|  |工作流拓扑|  |策略A/B   |  |技能自动生成  |      |
|  |GEPA算法  |  |优化      |  |测试      |  |              |      |
|  +----------+  +----------+  +----------+  +--------------+      |
|                         | 反馈信号 |                               |
+------------------------------------------------------------------+
|                    L4 评估层 (Evaluation)                         |
|  +----------+  +----------+  +----------+  +--------------+      |
|  |LLM自动   |  |审计日志  |  |失败检测  |  |质量报告      |      |
|  |评分1-10  |  |全量记录  |  |分类识别  |  |定期输出      |      |
|  +----------+  +----------+  +----------+  +--------------+      |
|                         | 质量数据 |                               |
+------------------------------------------------------------------+
|                    L3 工作流层 (Workflow)                         |
|  +----------+  +----------+  +----------+  +--------------+      |
|  |任务拆解器|  |依赖分析器|  |调度路由器|  |工作流快照    |      |
|  |->原子任务|  |并行/串行 |  |Agent分配 |  |审计+回滚     |      |
|  +----------+  +----------+  +----------+  +--------------+      |
|                         | 子任务 |                                 |
+------------------------------------------------------------------+
|                    L2 智能体层 (Agent)                            |
|  +----------+  +----------+  +----------+  +--------------+      |
|  |Agent工厂 |  |四象限管理|  |能力注册  |  |模型分配      |      |
|  |创建/销毁 |  |C/S/U/E   |  |Capability|  |DeepSeek系列  |      |
|  +----------+  +----------+  |Card      |  +--------------+      |
|                              +----------+                        |
|                         | 能力调用 |                               |
+------------------------------------------------------------------+
|                    L1 基础组件层 (Foundation)                     |
|  +----------+  +----------+  +----------+  +--------------+      |
|  |MCP Server|  |Skill库   |  |模型池    |  |数据存储      |      |
|  |Browser   |  |12个Skill |  |Max/High/ |  |Redis+MongoDB |      |
|  |MongoDB   |  |          |  |Flash     |  |              |      |
|  +----------+  +----------+  +----------+  +--------------+      |
+------------------------------------------------------------------+
```

---

## L1 — 基础组件层

> 雅溪的"工具箱"和"弹药库"。所有工具和技能都在这一层准备好，不涉及任何业务逻辑。

### 1.1 MCP Server 集合

| Server | 文件 | 协议 | 用途 |
|--------|------|------|------|
| `browser_harness` | `mcp_servers/browser_harness.py` | MCP stdio | Playwright浏览器自动化 |
| `L1_perception` | `layers/L1_perception/mcp_server.py` | MCP | 任务感知 |
| `L2_planning` | `layers/L2_planning/mcp_server.py` | MCP | 任务规划 |
| `L3_coordination` | `layers/L3_coordination/mcp_server.py` | MCP | Agent协调 |
| `L4_execution` | `layers/L4_execution/mcp_server.py` | MCP | 任务执行 |
| `L5_evolution` | `layers/L5_evolution/mcp_server.py` | MCP | 自我进化 |

MCP 协议栈: `mcp/protocol.py` + `mcp/__init__.py`
MCP 管理器: `mcp_manager.py` (生命周期) + `mcp_remote_client.py` (远程客户端)

### 1.2 Skill 库

| # | Skill | 目录 | 经验数据 | 核心能力 |
|---|-------|------|----------|----------|
| 1 | **translate-engine** | `skills/translate-engine/` | glossary.json, locale-patterns | 中->5语翻译，术语一致性 |
| 2 | **audit-engine** | `skills/audit-engine/` | power-patterns.json | DQF-MQM质量审计 |
| 3 | **product-search** | `skills/product-search/` | — | MongoDB产品查询+规格对比 |
| 4 | **seo-engineer** | `skills/seo-engineer/` | seo-baseline.json | 全站SEO诊断+Schema |
| 5 | **backend-engineer** | `skills/backend-engineer/` | patterns.json | Spring Boot+MyBatis-Plus |
| 6 | **cms-engineer** | `skills/cms-engineer/` | patterns.json | CMS文章多语言管理 |
| 7 | **infrastructure-engineer** | `skills/infrastructure-engineer/` | deploy-log.json | Nuxt3+ISR+CI/CD |
| 8 | **strategic-partner** | `skills/strategic-partner/` | project-memory.json | 战略决策+品牌写作 |
| 9 | **ui-ux-designer** | `skills/ui-ux-designer/` | design-tokens.json | 设计系统+组件规范 |
| 10 | **prompt-optimizer** | `skills/prompt-optimizer/` | — | GEPA算法自动迭代 |
| 11 | **token-budget-controller** | `skills/token-budget-controller/` | — | 优先级上下文裁剪 |
| 12 | **commander-evolution** | `skills/commander-evolution/` | — | Commander自我进化 |

### 1.3 模型池

| 模型 | 用途 | 配置位置 |
|------|------|----------|
| DeepSeek Max | 复杂推理 (L2 Agent设计, L5 进化) | 环境变量 `LLM_MODEL` |
| DeepSeek High | 评估打分 (L4 质量评估) | `agent_lifecycle_v2.py` LLMAdapter |
| DeepSeek Flash | 快速响应 (L1 Skill调用) | 各Agent `models.json` |

LLM 路由: `llm_router.py` — 根据任务复杂度+成本预算自动选模型

### 1.4 数据存储

| 存储 | 地址 | 角色 |
|------|------|------|
| **Redis** | `127.0.0.1:6379` | 缓存 + 消息总线(Pub/Sub) + 状态存储(41 keys) |
| **MongoDB** | `127.0.0.1:27017` | 持久化 (agent状态/workflow快照/审计日志) |
| **宿主机MongoDB** | `mongodb:27017` | 业务数据 (page_content产品库) |

存储适配: `mongo_persist.py` + `redis-recovery.py`

### 1.5 基础设施支撑

| 组件 | 文件 | 说明 |
|------|------|------|
| 容器入口 | `/entrypoint.sh` | 按序启动 Redis->MongoDB->PM2双守护 |
| 进程管理 | PM2 (8进程) | 双守护互保：Guardian <-> Commander 互相唤醒 |
| 配置中心 | `config.py` | 全局配置常量 |
| 日志归档 | `archive-comm-logs.py` | 通信日志自动归档 |

---

## L2 — 智能体层

> 雅溪的"员工管理中心"。决定"谁"来做这件事，给这个员工配什么"大脑"。

### 2.1 Agent工厂

| 组件 | 文件 | 核心能力 |
|------|------|----------|
| **IsolatedAgentFactory** | `agent_isolation.py` | 创建隔离Agent进程 (独立目录+PM2管理) |
| **AsyncAgentFactory** | `agent_lifecycle_v2.py` | 异步Agent创建 (配合生命周期) |
| **agent-factory.sh** | `agents/runtime/agent-factory.sh` | Shell工厂脚本 |
| **agent.sh** | `agents/runtime/agent.sh` | 通用Agent启动模板 (Redis订阅循环) |

### 2.2 四象限生命周期管理

| 象限 | 标识 | 说明 | 当前Agent | 策略 |
|------|------|------|-----------|------|
| **Core** | C | 核心业务，常驻内存 | 翻译官、商务经理、售前经理 | keep_warm=true |
| **Strategic** | S | 战略分析，按需创建 | 审计官 | keep_warm=false, max_idle=600s |
| **Utility** | U | 工具辅助 | 心跳监控 | keep_warm=true |
| **Ephemeral** | E | 一次性任务 | ru-审计官 | 任务完成即销毁 |

实现: `AgentLifecycleManagerV2` (agent_lifecycle_v2.py, 66KB)
- 评估循环：定期检查各象限Agent状态
- 自动决策：创建/休眠/唤醒/销毁
- 安全边界: `SafetyBoundary` — 资源限制 + 操作黑/白名单

### 2.3 能力注册 (Capability Card)

| 注册项 | Redis Key | 内容 |
|--------|-----------|------|
| Agent卡片 | `commander:a2a:agent_cards` | 各Agent的Capability Card |
| 能力列表 | `commander:a2a:capability:*` | 翻译/审计/报价/查询等能力标签 |
| A2A注册表 | `commander:a2a:registry` | Agent间通信注册 |
| 心跳追踪 | `commander:agent:heartbeat:*` | 各Agent最后心跳时间戳 |

实现: `a2a_protocol.py` — `AgentDiscovery` + `AgentCard`

**已注册的 5 个 Agent：**

| Agent | 角色 | 象限 | 能力卡片 |
|-------|------|------|----------|
| 翻译官 | 翻译官 | Core | 多语翻译、术语词典 |
| 商务经理 | 商务经理 | Core | 客户接待、需求挖掘 |
| 售前经理 | 售前经理 | Core | 产品查询、报价生成、规格对比、方案推荐 |
| 审计官 | 审计官 | Strategic | 内容审计、术语一致性、参数核查、质量报告 |
| 心跳监控 | 心跳监控 | Utility | Agent存活检测 |

### 2.4 模型分配

| 组件 | 文件 | 说明 |
|------|------|------|
| **LLMRouter** | `llm_router.py` | 根据任务类型+复杂度选择合适的模型 |
| **RouteABTester** | `llm_router.py` | 路由策略A/B测试 |
| **Agent Models** | `agents/isolated/*/models.json` | 每个隔离Agent的独立模型配置 |

### 2.5 Agent 隔离沙箱

```
/app/.pi/agents/isolated/
|-- 翻译官-{8位hash}/    x16 实例 (独立 agent.json + models.json + skills/)
|-- 商务经理-{8位hash}/  x15 实例
|-- 售前经理-{8位hash}/  x15 实例
`-- 总计: 46 个隔离实例
```

管理组件:
- `AgentWorkspaceManager` (agent_isolation.py) — 隔离工作区创建/清理
- `SandboxManager` (sandbox_manager.py, 31KB) — 安全沙箱
- `AgentFaultIsolation` (agent_isolation.py) — 故障检测+自动修复

### 2.6 Agent运行时

| 文件 | 大小 | 说明 |
|------|------|------|
| `yaxiio_agent.py` | 21KB | 通用AI Agent (任务分发+工具调用+浏览器自动化) |
| `agent-core.py` | 5KB | Agent核心循环 |
| `agent-translator.py` | — | 翻译官专用Agent |
| `agent-business.py` | — | 商务经理专用Agent |
| `agent-presales.py` | — | 售前经理专用Agent |
| `agent-commander.py` | — | Commander Agent |

---

## L3 — 工作流层

> 雅溪的"项目经理"。决定"怎么做"——先做什么、后做什么、谁和谁配合。

### 3.1 任务拆解器

| 组件 | 文件 | 核心功能 |
|------|------|----------|
| **AutonomousTaskDecomposer** | `agent_lifecycle_v2.py` | LLM驱动：模糊意图->原子任务序列 |
| **TaskAnalyzer** | `task_analyzer.py` (9KB) | 任务指纹去重，复用历史摘要 |

拆解流程:
```
用户请求 "分析GSC数据->找关键词->生成标题"
    |
    v
AutonomousTaskDecomposer (LLM分析)
    |
    |-- 子任务1: 拉取GSC数据 (MCP: browser_harness)
    |-- 子任务2: 分析关键词机会 (Skill: seo-engineer)
    `-- 子任务3: 生成优化标题 (Skill: strategic-partner)
```

### 3.2 依赖分析器

| 组件 | 文件 | 核心功能 |
|------|------|----------|
| **依赖分析** | `agent_lifecycle_v2.py` (内嵌) | 分析子任务间的并行/串行关系 |
| **规划协调** | `modules/optimization/planner_coordinator.py` | 多Agent协作规划 |

依赖类型:
- **串行**: 子任务2 依赖 子任务1 的输出 -> 顺序执行
- **并行**: 子任务A和B无依赖 -> 同时分配不同Agent

### 3.3 调度路由器

| 组件 | 文件 | 核心功能 |
|------|------|----------|
| **CommanderV2** | `commander_v2.py` (987行) | 总指挥：接收任务->拆解->路由->监控 |
| **ExtensionRouter** | `extension_router.py` (31KB) | 根据能力标签匹配Agent |
| **AutoScaler** | `auto_scaler.py` (8KB) | 按队列深度弹性扩缩Agent实例 |

路由决策矩阵:
| 子任务类型 | 匹配Agent | 优先级 |
|-----------|-----------|--------|
| 翻译类 | 翻译官 | Core |
| 审计类 | 审计官 | Strategic |
| 报价类 | 售前经理 | Core |
| 咨询类 | 商务经理 | Core |

### 3.4 工作流快照

| 组件 | 文件 | 说明 |
|------|------|------|
| 会话管理 | `modules/session/session_manager.py` | 工作流会话状态 |
| WebSocket桥接 | `modules/session/ws_bridge_v3.py` | 实时推送工作流状态 |
| 日志归档 | `archive-comm-logs.py` | 每次编排结果存档 |

### 3.5 工作流可视化

| 组件 | 文件 | 说明 |
|------|------|------|
| Dashboard | `dashboard_v2.py` | Web界面 (端口3003) |
| TUI | `commander-tui.py` | 终端界面 |

---

## L4 — 评估层

> 雅溪的"质检员"。评估"做得好不好"，把结果反馈给进化层。

### 4.1 LLM 自动评分

| 组件 | 文件 | 评分维度 |
|------|------|----------|
| **LLMScorer** | `modules/optimization/llm_scorer.py` | 准确性 / 完整性 / 术语一致性 / 格式规范 |
| **评分标准** | `commander:evolution:failures` (Redis) | 阈值 7 分以下触发进化 |

评分流程:
```
Agent完成任务
    |
    v
LLMScorer (使用 DeepSeek High)
    |
    |-- 评分 >= 7 -> 正常归档
    `-- 评分 < 7 -> 标记失败 -> 触发 L5 进化
```

### 4.2 审计日志

| 组件 | 文件 | 记录内容 |
|------|------|----------|
| **AuditLogger** | `modules/optimization/audit_logger.py` | LLM调用 / 工具执行 / 状态变更 |
| **A2A审计** | `commander:log:failures` (Redis) | Agent间通信失败记录 |

### 4.3 失败检测

| 组件 | 文件 | 检测类型 |
|------|------|----------|
| **AgentFaultIsolation** | `agent_isolation.py` (44KB) | 进程崩溃 / 心跳超时 / 资源耗尽 |
| **HeartbeatManager** | `heartbeat_manager.py` (26KB) | Agent存活监控 + 自动告警 |
| **AgentFailover** | `failover.py` (20KB) | 五级降级策略 + 故障转移 |
| **TaskDegradation** | `failover.py` | 任务降级 (功能裁剪保核心) |

失败分类与处理:
| 失败类型 | 识别方式 | 处理策略 |
|----------|----------|----------|
| 超时 | 心跳>60s无响应 | 重启Agent，标记失败 |
| 报错 | 进程崩溃 | 隔离+自动修复(最多3次) |
| 质量低 | LLM评分<7 | 标记->触发L5进化 |
| 资源耗尽 | CPU/内存超标 | 降级->限流->告警 |

### 4.4 质量报告

| 组件 | 路径/Key | 说明 |
|------|----------|------|
| **Blackboard报告库** | `/app/.pi/blackboard/reports/` | 结构化审计报告存储 |
| **失败日志** | `commander:log:failures` (Redis) | 失败任务汇总 |
| **进化失败** | `commander:evolution:failures` (Redis) | 进化过程失败记录 |

---

## L5 — 进化层

> 雅溪的"学习能力"。决定"下次怎么做得更好"，把优化结果反馈到 L2 和 L3。

### 5.1 提示词优化

| 组件 | 文件 | 算法 |
|------|------|------|
| **PromptOptimizer** | `skills/prompt-optimizer/prompt_optimizer.py` | GEPA (Generate->Evaluate->Pick->Apply) |
| **GEPA引擎** | `commander-evolution/gepa_engine.py` | 自动迭代Agent提示词 |
| **进化模块** | `commander-evolution/evolution.py` | 基于失败案例改进Prompt |

触发条件: Agent错误率 >10% 或 累计运行 50 次任务
执行流程: 读取执行轨迹 -> LLM分析 -> 生成2-5个候选 -> A/B测试 -> 自动选优

### 5.2 工作流拓扑优化

| 组件 | 文件 | 核心功能 |
|------|------|----------|
| **SelfEvolvingCommander** | `agent_lifecycle_v2.py` | 历史模式分析+自我优化 |
| **WorkflowOptimizer** | `modules/optimization/workflow_optimizer.py` | 自动发现更优的Agent协作流程 |
| **SupervisionTree** | `modules/optimization/supervision_tree.py` | 监督树结构优化 |

### 5.3 策略 A/B 测试

| 组件 | 文件 | 说明 |
|------|------|------|
| **ABTester** | `ab_tester.py` (10KB) | 新旧策略各跑50%任务，自动选优 |
| **RouteABTester** | `llm_router.py` | LLM路由策略A/B测试 |
| **扩展决策** | `extensions:decisions` (Redis) | 记录每次A/B决策结果 |

### 5.4 技能自动生成

| 组件 | 文件 | 说明 |
|------|------|------|
| **SkillGenerator** | `modules/optimization/skill_generator.py` | Agent成功完成任务后，自动提炼为Skill |
| **ExtensionRouter** | `extension_router.py` | 动态注册新生成的Skill |
| **Skill注册表** | `skills:registry` (Redis) | 所有可用Skill的注册信息 |

### 5.5 进化循环

```
         +----------------------------------+
         |         L5 进化引擎               |
         |                                  |
         |  L4评估信号 ---> 分析失败模式       |
         |                    |             |
         |         +----------+----------+  |
         |         v          v          v  |
         |    提示词优化  拓扑优化  技能生成  |
         |         |          |          |  |
         |         +----------+----------+  |
         |                    v             |
         |         A/B测试验证               |
         |                    |             |
         |         -----------+---------    |
         |                    v             |
         |         反馈 L2 (Agent Prompt)    |
         |         反馈 L3 (工作流模板)       |
         +----------------------------------+
```

---

## 五层协作示例

> 任务: "分析上周GSC数据，找出需要优化的关键词，生成优化后标题"

| 层级 | 动作 | 使用组件 | 模型 |
|------|------|----------|------|
| **L1** | 提供 GSC MCP Server + SEO Audit Skill | browser_harness + seo-engineer | — |
| **L2** | 创建"SEO分析师Agent"，分配模型 | AgentFactory + LLMRouter | DeepSeek Max |
| **L3** | 拆解为 拉数据->分析->生成标题，串行执行 | AutonomousTaskDecomposer | — |
| **L4** | 评估生成标题质量，打分 | LLMScorer | DeepSeek High |
| **L5** | 如果评分<7，优化Agent提示词 | PromptOptimizer + GEPA | DeepSeek Max |

---

## 资源清单 (当前状态)

### 文件归属总览

| 文件 | L1 | L2 | L3 | L4 | L5 |
|------|:--:|:--:|:--:|:--:|:--:|
| `commander_v2.py` (42KB) | | | X | | |
| `agent_lifecycle_v2.py` (66KB) | | X | X | | X |
| `agent_isolation.py` (44KB) | | X | | X | |
| `sandbox_manager.py` (31KB) | | X | | | |
| `extension_router.py` (31KB) | | X | X | | X |
| `heartbeat_manager.py` (26KB) | | X | | X | |
| `llm_router.py` (23KB) | X | X | X | | |
| `mcp_remote_client.py` (22KB) | X | | | | |
| `mcp_manager.py` (20KB) | X | | | | |
| `failover.py` (20KB) | | | X | X | |
| `a2a_protocol.py` (17KB) | | X | X | | |
| `skill_manager.py` (16KB) | X | | | | |
| `ws_bridge.py` (15KB) | | | X | | |
| `task_analyzer.py` (9KB) | | | X | | |
| `ab_tester.py` (10KB) | | | | | X |
| `auto_scaler.py` (8KB) | | | X | | |
| `reliable_comm.py` (8KB) | | | X | | |
| `config.py` (5KB) | X | | | | |

### 进程归属

| PM2进程 | 归属层 | 角色 |
|---------|--------|------|
| `yaxiio-guardian` | L1 | 基础设施守护 |
| `yaxiio-core` | L3 | 工作流总指挥 |
| `yaxiio-agent` | L2 | 智能体运行时 |
| `agent-auditor` | L2 | 审计Agent实例 |
| `agent-*` (x5) | L2 | 测试Agent实例 |

---

## 设计原则

1. **单向依赖**: L5->L4->L3->L2->L1，上层可调下层，反向只通过事件/消息
2. **标准接口**: 层间通过 Redis Pub/Sub + MCP 协议通信
3. **独立升级**: 替换任一层的实现不影响其他层
4. **经验闭环**: L4评估->L5进化->L2/L3优化->L1能力沉淀

> 生成时间: 2026-05-25 | 版本: v2.0
