# 雅溪 Yaxiio v1.0 — 五层模块化架构

> 五金外贸B2B智能调度系统 · 2026 AI元年
> 生成时间: 2026-05-25

---

## 架构全景图

```
┌─────────────────────────────────────────────────────────────────────┐
│                       L5  能力扩展层                                  │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│   │翻译引擎   │ │审计引擎   │ │产品搜索   │ │SEO工程师  │ │UI/UX设计 │ │
│   │后端工程师 │ │CMS工程师 │ │架构工程师 │ │战略合伙人 │ │...更多   │ │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│   │MCP Server│ │Token预算 │ │Prompt优化│ │  Blackboard 黑板系统  │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                       L4  Agent运行时层                              │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│   │四象限生命 │ │Agent隔离  │ │安全沙箱   │ │Agent注册  │ │Agent工厂 │ │
│   │周期管理   │ │进程隔离   │ │           │ │表         │ │(sh)      │ │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│   │Guardian  │ │yaxiio-   │ │心跳管理   │ │  隔离工作区(70+实例)  │  │
│   │双守护    │ │agent     │ │           │ │  /agents/isolated/    │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                       L3  调度引擎层                                  │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│   │Commander │ │任务分析   │ │自动伸缩   │ │故障转移   │ │LLM路由   │ │
│   │V2 总指挥 │ │指纹去重   │ │弹性扩缩   │ │五级降级   │ │智能路由   │ │
│   └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│   │A/B测试   │ │扩展路由   │ │Skill管理 │ │ 五层内感知层         │  │
│   │          │ │           │ │MCP管理   │ │ L1-L5 MCP Servers   │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                       L2  通信层                                     │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│   │可靠通信   │ │A2A协议   │ │WebSocket │ │  Redis Pub/Sub       │  │
│   │ACK确认   │ │Agent发现  │ │桥接V2/V3 │ │  异步桥接 EventLoop   │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│                       L1  基础设施层                                  │
│   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐  │
│   │Redis:6379│ │MongoDB   │ │PM2进程   │ │  entrypoint.sh       │  │
│   │41 keys   │ │:27017    │ │管理(8进程)│ │  双守护互保启动       │  │
│   └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## L1 — 基础设施层

运行载体与基础服务，所有上层模块的底座。

### 1.1 容器运行时

| 资源 | 路径/标识 | 说明 |
|------|-----------|------|
| 容器镜像 | `yaxiio:v1.0` | Ubuntu 24.04 LTS |
| 入口脚本 | `/entrypoint.sh` | 启动Redis→MongoDB→PM2双守护→Dashboard |
| 环境变量 | `DEEPSEEK_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` | LLM API + Redis连接 |

### 1.2 Redis (消息总线 + 状态存储)

| Key 前缀/名称 | 用途 |
|---------------|------|
| `commander:agents:active` | 活跃Agent列表 |
| `commander:agent:heartbeat:*` | 各Agent心跳时间戳 |
| `commander:a2a:agent_cards` | A2A Agent能力卡片注册表 |
| `commander:a2a:capability:*` | 各Agent能力注册 (翻译/审计/报价/查询...) |
| `commander:a2a:registry` | A2A注册表 |
| `commander:log:failures` | 失败日志 |
| `commander:evolution:failures` | 进化失败记录 |
| `agent:*:prompt` / `agent:*:memory` | Agent的Prompt/记忆 |
| `skills:registry` | Skill注册表 |
| `mcp:registry` / `mcp:status` | MCP Server注册与状态 |
| `extensions:capability_index` / `extensions:decisions` | 扩展系统能力索引/决策 |
| `lifecycle:roles:core` / `lifecycle:roles:strategic` | 生命周期角色定义 |
| `yaxiio:arsenal` / `yaxiio:law:0` / `yaxiio:guardian:1:heartbeat` | 系统军械库/法则/守护心跳 |

### 1.3 MongoDB (持久化存储)

| 数据库 | 说明 |
|--------|------|
| `admin` | 系统管理 |
| `config` | 配置存储 |
| `local` | 本地数据 |

> 注：产品数据(lightingmetal/page_content)存储在宿主机MongoDB `mongodb:7` 容器中

### 1.4 PM2 进程管理 (双守护互保)

| 进程名 | 文件 | 内存 | 角色 |
|--------|------|------|------|
| `yaxiio-guardian` | `pi_guardian.py` | 61MB | 守护进程 (0次重启) |
| `yaxiio-core` | `commander_v2.py` | 104MB | 总指挥核心 |
| `yaxiio-agent` | `yaxiio_agent.py` | 62MB | 通用AI Agent |
| `agent-auditor` | `agent.sh 审计官` | 2MB | 审计Agent |
| `agent-*` (×5) | `agent-factory.sh` | ~2MB | 测试/调试Agent |

### 1.5 配置与工具

| 文件 | 说明 |
|------|------|
| `config.py` | Commander全局配置 |
| `mongo_persist.py` | MongoDB持久化适配器 |
| `redis-recovery.py` | Redis故障恢复脚本 |
| `archive-comm-logs.py` | 通信日志归档 |

---

## L2 — 通信层

Agent间、Agent与Commander间、系统对外通信。

### 2.1 Redis Pub/Sub 通道

| Channel | 订阅者 | 用途 |
|---------|--------|------|
| `lightingmetal:agent:commander` | Commander V2 | 接收任务指令 |
| `lightingmetal:agent:{agent_name}` | 各Agent | 接收专属任务 |

消息格式: 兼容 v1 的 JSON 格式，支持 ACK 确认

### 2.2 通信模块

| 文件 | 大小 | 核心类/功能 |
|------|------|------------|
| `reliable_comm.py` | 8KB | `ReliableComm` — List+Pub/Sub双通道 + ACK确认 |
| `a2a_protocol.py` | 17KB | `AgentDiscovery`, `AgentCard` — Agent发现+能力注册 |
| `ws_bridge.py` | 15KB | WebSocket桥接 (外部系统集成) |
| `modules/ws_bridge_v3.py` | — | WebSocket桥接V3升级版 |
| `modules/session/ws_bridge_v3.py` | — | Session化WebSocket桥接 |

### 2.3 异步事件桥接

- `AsyncEventLoop` — 在后台线程运行asyncio事件循环，使同步Pub/Sub主循环可调用异步生命周期方法
- 桥接对象: `commander.lifecycle` ←→ AsyncEventLoop

---

## L3 — 调度引擎层

Commander核心，多Agent系统的"大脑"。

### 3.1 总指挥 Commander V2

| 文件 | 行数 | 说明 |
|------|------|------|
| `commander_v2.py` | 987行 | 多Agent总指挥，集成所有优化模块 |
| `core/orchestrator.py` | — | 核心编排器 |

**CommanderV2 类属性：**
- 6个静态Agent: 翻译官、商务经理、售前经理 (静态) + 审计官、俄语审计官 (可扩展)
- 集成10+优化模块

### 3.2 任务调度引擎

| 文件 | 大小 | 核心功能 |
|------|------|----------|
| `task_analyzer.py` | 9KB | `TaskAnalyzer` — 任务指纹去重，复用历史摘要 |
| `auto_scaler.py` | 8KB | `AutoScaler` — 按队列深度弹性扩缩Agent实例 |
| `ab_tester.py` | 10KB | `ABTester` — A/B测试策略自进化 |
| `failover.py` | 20KB | `AgentFailover` + `TaskDegradation` — 故障转移+五级降级+Redis Sentinel |

### 3.3 LLM智能路由

| 文件 | 大小 | 核心功能 |
|------|------|----------|
| `llm_router.py` | 23KB | `LLMRouter` + `RouteABTester` — 多模型智能路由+路由策略A/B |
| `modules/optimization/zero_token_router.py` | — | 零Token路由(规则匹配绕过LLM) |

### 3.4 扩展与动态管理

| 文件 | 大小 | 核心功能 |
|------|------|----------|
| `extension_router.py` | 31KB | `ExtensionRouter` — Skill动态挂载+MCP动态注册+自主进化 |
| `skill_manager.py` | 16KB | 本地Skill发现+注册到Redis |
| `mcp_manager.py` | 20KB | MCP Server生命周期管理 |
| `mcp_remote_client.py` | 22KB | 远程MCP客户端 |

### 3.5 五层内部感知层 (Layers)

| 层 | 路径 | MCP Server |
|----|------|------------|
| L1 感知 | `layers/L1_perception/` | 任务感知服务 |
| L2 规划 | `layers/L2_planning/` | 任务规划服务 |
| L3 协调 | `layers/L3_coordination/` | Agent协调服务 |
| L4 执行 | `layers/L4_execution/` | 任务执行服务 |
| L5 进化 | `layers/L5_evolution/` | 自我进化服务 |

### 3.6 优化算法模块

| 文件 | 说明 |
|------|------|
| `modules/optimization/optimization_algorithms.py` | 调度优化算法集 |
| `modules/optimization/planner_coordinator.py` | 规划协调器 |
| `modules/optimization/supervision_tree.py` | 监督树 |
| `modules/optimization/workflow_optimizer.py` | 工作流优化器 |
| `modules/optimization/skill_generator.py` | Skill生成器 |
| `modules/optimization/llm_scorer.py` | LLM评分器 |
| `modules/optimization/failure_recovery.py` | 故障恢复 |
| `modules/optimization/audit_logger.py` | 审计日志 |

---

## L4 — Agent运行时层

Agent的完整生命周期：创建→隔离→运行→监控→销毁。

### 4.1 生命周期管理

| 文件 | 大小 | 核心类/功能 |
|------|------|------------|
| `agent_lifecycle_v2.py` | **66KB** | `AgentLifecycleManagerV2` — 四象限生命周期管理<br>`AgentDesigner` — LLM驱动的Agent能力规格设计<br>`AutonomousTaskDecomposer` — 模糊意图→原子任务序列<br>`SelfEvolvingCommander` — 历史模式分析+自我优化<br>`SafetyBoundary` — 资源限制+操作黑/白名单<br>`AgentQuadrant` — Core/Strategic/Utility/Ephemeral |

**四象限模型：**

| 象限 | 说明 | 示例Agent | 特性 |
|------|------|-----------|------|
| **Core** | 核心业务Agent | 翻译官、商务经理、售前经理 | keep_warm=true, 常驻 |
| **Strategic** | 战略分析Agent | 审计官 | 按需创建，长空闲超时 |
| **Utility** | 工具类Agent | 心跳监控 | 系统运维辅助 |
| **Ephemeral** | 一次性Agent | ru-审计官 | 任务完成即销毁 |

### 4.2 隔离与沙箱

| 文件 | 大小 | 核心类/功能 |
|------|------|------------|
| `agent_isolation.py` | 44KB | `IsolatedAgentFactory` — 创建隔离Agent进程<br>`AgentFaultIsolation` — 故障检测+隔离+自动修复<br>`AgentWorkspaceManager` — 隔离工作区文件管理<br>`IsolatedAgentManager` — 一体化隔离管理(含monitor) |
| `sandbox_manager.py` | 31KB | `SandboxManager` — 安全沙箱管理 |

### 4.3 隔离工作区

```
/app/.pi/agents/isolated/
├── 翻译官-{hash}/       (×16个实例)
│   ├── agent.json        Agent配置
│   ├── models.json       LLM模型配置
│   └── skills/           Skill副本
│       └── translate-engine/
├── 商务经理-{hash}/      (×15个实例)
│   └── skills/product-search/
├── 售前经理-{hash}/      (×15个实例)
│   └── skills/product-search/
└── ...                   总计 46 个隔离实例
```

### 4.4 心跳管理

| 文件 | 大小 | 说明 |
|------|------|------|
| `heartbeat_manager.py` | 26KB | Agent心跳检测+自动告警+失联恢复 |

### 4.5 Agent 工厂与注册

| 文件 | 说明 |
|------|------|
| `agent-factory.sh` | Shell Agent工厂脚本 |
| `agent.sh` | 通用Agent启动脚本 (Redis订阅+循环) |
| `agent-registry.py` | Python Agent注册表管理 |
| `agent-registry.json` | 静态Agent注册表 (6个Agent定义) |
| `agent-core.py` | Agent核心运行时 |
| `agent-translator.py` | 翻译官Agent实现 |
| `agent-business.py` | 商务经理Agent实现 |
| `agent-presales.py` | 售前经理Agent实现 |
| `agent-commander.py` | Commander Agent实现 |

### 4.6 守护与自我修复

| 文件 | 说明 |
|------|------|
| `pi_guardian.py` | 守护进程 v1 — 监控+重启 |
| `pi_guardian_v2.py` | 守护进程 v2 — 增强版 |
| `memory-manager.sh` | Agent记忆管理 (过期清理) |

### 4.7 通用 Agent 运行时

| 文件 | 大小 | 说明 |
|------|------|------|
| `yaxiio_agent.py` | 21KB | Yaxiio通用Agent — 支持任务分发+工具调用+浏览器自动化 |

---

## L5 — 能力扩展层

业务Skill + MCP Server + 知识管理，赋予Agent专业领域能力。

### 5.1 业务 Skill 清单

| Skill | 目录 | 触发词 | 经验积累 |
|-------|------|--------|----------|
| **translate-engine** | `skills/translate-engine/` | 翻译、本地化、i18n | ✅ glossary.json, locale-patterns, glossary-candidates |
| **audit-engine** | `skills/audit-engine/` | 审计、检查、audit、review | ✅ power-patterns.json |
| **product-search** | `skills/product-search/` | 查产品、搜规格、报价 | — |
| **seo-engineer** | `skills/seo-engineer/` | SEO、排名、sitemap | ✅ seo-baseline.json |
| **backend-engineer** | `skills/backend-engineer/` | 后端、API、SQL、Redis | ✅ patterns.json |
| **cms-engineer** | `skills/cms-engineer/` | CMS、文章、白皮书 | ✅ patterns.json |
| **infrastructure-engineer** | `skills/infrastructure-engineer/` | 部署、deploy、CDN | ✅ deploy-log.json |
| **strategic-partner** | `skills/strategic-partner/` | 战略、品牌、写作 | ✅ project-memory.json |
| **ui-ux-designer** | `skills/ui-ux-designer/` | 设计、UI、UX、配色 | ✅ design-tokens.json |
| **prompt-optimizer** | `skills/prompt-optimizer/` | prompt优化、GEPA | — |
| **token-budget-controller** | `skills/token-budget-controller/` | token优化、上下文裁剪 | — |
| **commander-evolution** | `skills/commander-evolution/` | 进化、优化 | — |

### 5.2 MCP Servers (Model Context Protocol)

| Server | 文件 | 说明 |
|--------|------|------|
| `browser_harness` | `mcp_servers/browser_harness.py` | Playwright浏览器自动化 (审核/截屏/翻译验证) |
| L1-L5 感知层 | `layers/L*/mcp_server.py` | 五层内部感知服务 |

**MCP 协议核心：**
- `mcp/protocol.py` — MCP协议实现
- `mcp/__init__.py` — MCP模块入口

### 5.3 Blackboard 黑板系统

| 组件 | 路径 | 说明 |
|------|------|------|
| **收件箱** | `/app/.pi/blackboard/inbox/` | Agent间异步任务投递 |
| **报告库** | `/app/.pi/blackboard/reports/` | 审计报告存储 (当前为空) |

### 5.4 经验积累系统

各Skill下的 `experience/` 目录存储结构化经验数据，支持：
- 翻译引擎: 术语词典 + 语言模式 + 候选术语
- 审计引擎: 最优审计路径模式
- SEO引擎: 搜索基线数据
- UI设计: 设计令牌系统
- 基础设施: 部署日志

### 5.5 Dashboard & TUI

| 文件 | 说明 |
|------|------|
| `dashboard_v2.py` | Web Dashboard (端口3003) |
| `dashboard.py` | Dashboard核心 |
| `dashboard-tui.sh` | TUI版Dashboard启动脚本 |
| `commander-tui.py` | Commander TUI界面 |

---

## 数据流示意

```
外部请求 → Dashboard:3003
              │
              ▼
         Redis Pub/Sub (lightingmetal:agent:commander)
              │
              ▼
         CommanderV2.main()
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
 TaskAnalyzer  LLMRouter  LifecycleManager
 (去重)      (路由)     (四象限决策)
    │         │         │
    └─────────┼─────────┘
              ▼
         AgentFactory → 创建/复用隔离Agent
              │
              ▼
         Redis Pub/Sub (lightingmetal:agent:{name})
              │
              ▼
         Agent.sh + yaxiio_agent.py
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
  Skill调用  MCP调用   LLM API
              │
              ▼
         结果写回 Redis/Balckboard
              │
              ▼
         Heartbeat + 评估循环
```

---

## 当前状态 (2026-05-25)

| 指标 | 值 |
|------|-----|
| 运行时间 | 3h+ |
| PM2进程数 | 8 |
| 活跃Agent | 0 (全部archived) |
| 隔离实例 | 46个 (70%翻译官) |
| Redis Keys | 41 |
| Commander重启 | 25次 (已修复isolation_mgr bug) |
| ⚠️ token-budget-controller | 未找到 (路径问题) |
| ⚠️ prompt-optimizer | 未找到 (路径问题) |

### 待优化项
1. `token-budget-controller` 和 `prompt-optimizer` Skill路径修复
2. `agent_isolation.py` 的 `IsolatedAgentManager` 完整接入Commander
3. 清理已归档的历史隔离实例
4. 测试Agent(×5)清理
5. i18n-backup 目录归档
