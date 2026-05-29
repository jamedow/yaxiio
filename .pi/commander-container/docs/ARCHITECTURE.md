# Commander v2.3 架构设计文档

## 1. 设计哲学

Commander 遵循 **中心调度 + 扁平P2P** 的混合架构：

- **中心调度**：Commander 负责 Agent 生命周期（创建/销毁/监控）、任务去重、弹性伸缩、A/B测试、LLM路由
- **扁平P2P**：Agent 之间通过 `replyTo` 字段直连协作，无需经过 Commander 中转

这解决了纯中心化架构的瓶颈问题，同时保留了中心调度的全局优化能力。

## 2. 通信架构

```
         ┌──────────────────────────┐
         │        Commander         │ ← 中心调度
         │  ┌────────────────────┐  │
         │  │ handle_task()       │  │
         │  │  1. ExtensionRouter │  │
         │  │  2. TaskAnalyzer    │  │
         │  │  3. ABTester        │  │
         │  │  4. TaskDegradation │  │
         │  │  5. AutoScaler      │  │
         │  │  6. LLMRouter       │  │
         │  │  7. ReliableComm    │  │
         │  └────────────────────┘  │
         └──────┬───────┬───────────┘
                │       │
         ┌──────▼───────▼──────────┐
         │    Redis 消息总线        │
         │  ┌─────────────────────┐ │
         │  │ Pub/Sub 通道         │ │ ← 广播/心跳/结果
         │  │ List 通道            │ │ ← 关键指令/ACK
         │  └─────────────────────┘ │
         └──┬────────┬──────────┬──┘
            │        │          │
      ┌─────▼──┐ ┌──▼────┐ ┌───▼──────┐
      │翻译官   │←──→│商务经理│←──→│售前经理   │  ← P2P
      └─────────┘ └───────┘ └──────────┘
```

### 消息类型（7种）

| type | 方向 | 通道 | 用途 |
|------|------|------|------|
| `task` | Commander→Agent | Pub/Sub | 分派任务 |
| `request` | Agent→Agent (P2P) | Pub/Sub | 直接请求协助 |
| `response` | Agent→任意 | Pub/Sub | 回复结果 |
| `error` | Agent→任意 | Pub/Sub | 错误报告 |
| `heartbeat` | Agent→Commander | Pub/Sub | 心跳上报 |
| `heartbeat_check` | Commander→Agent | Pub/Sub | 心跳检测 |
| `shutdown` | Commander→Agent | Pub/Sub | 关闭指令 |

### 双通道设计

- **Pub/Sub**：广播、心跳、结果通知（低延迟，尽力送达）
- **List**：关键指令（持久化、FIFO、ACK确认，绝不丢失）

## 3. 任务处理流水线

```
用户输入 "审计俄语页面中文残留并翻译"
  │
  ▼
ExtensionRouter.analyze_and_extend()  ← v2.3: 检查需要什么Skill/MCP
  │
  ▼
TaskAnalyzer.check_duplicate()        ← 优化一: MD5 指纹去重
  │
  ▼
ABTester.route_task()                 ← 优化四: A/B 分流
  │
  ├─ Group A → _split_default()       ← 默认拆分策略
  └─ Group B → _split_with_strategy() ← 实验拆分策略
  │
  ▼
TaskDegradation.get_degradation_level() ← 优化五: 降级检测
  │  ├─ L0: 全量 → 正常分发
  │  ├─ L1: 无售前 → 跳过报价
  │  ├─ L2: 无商务 → 跳过沟通
  │  ├─ L3: 无翻译 → 跳过翻译
  │  └─ L4: 全部不可用 → 兜底降级
  │
  ▼
AutoScaler.check_and_scale()          ← 优化二: 弹性伸缩
  │
  ▼
LLMRouter.route_task()                ← 优化六: LLM语义路由
  │
  ▼
ReliableComm.send_critical_command()  ← 优化三: 双通道分发+ACK
  │
  ▼
TaskAnalyzer.cache_task()             ← 缓存指纹
```

## 4. Agent 四象限生命周期

```
        │ 重要性高
        │
  Core  │  Strategic
  常驻   │  按需创建
  翻译官 │  审计官
  商务经理│  SEO专家
  售前经理│
────────┼────────── 临时性
  Utility│  Ephemeral
  工具型  │  一次性
  心跳监控│  ru-审计官
  日志清理│  数据导出
        │
```

- **Core**：始终在线的核心 Agent（≥2 个保护）
- **Strategic**：按需创建，任务完成后可销毁
- **Utility**：系统工具 Agent，长期运行
- **Ephemeral**：一次性任务 Agent，完成后自动销毁

## 5. 动态扩展系统（v2.3）

```
ExtensionRouter
  │
  ├─ AgentDesigner.analyze_and_design(task)
  │     └─ 消费已有LLM能力分析
  │
  ├─ _find_capability_gaps()
  │     ├─ skill 缺口 → SkillManager.get_global_skills()
  │     ├─ mcp 缺口   → MCPManager.get_registered_servers()
  │     └─ agent 缺口 → LifecycleManager 角色查询
  │
  ├─ _decide_extension_strategy()
  │     ├─ install_skill (npm/github/local)
  │     ├─ register_mcp  (npx command)
  │     ├─ create_agent  (ephemeral)
  │     └─ create_skill_blueprint (待审核)
  │
  └─ _execute_strategy()
        ├─ SkillManager.install_skill()
        ├─ MCPManager.register_mcp_server()
        └─ LifecycleManager.request_agent()
```

## 6. 安全边界

| 资源 | 限制 | 实现 |
|------|------|------|
| Agent 总数 | ≤ 10 | SafetyBoundary |
| 并行子Agent | ≤ 10 | AutoScaler |
| Redis Key 操作 | 只读 page:*/lightingmetal:* | Constitution R1 |
| 报价 | 先存MongoDB草稿 | Constitution R3 |
| 消息格式 | 强制JSON | Constitution R4 |
| 消息内容 | 不可篡改payload | Constitution R7 |

## 7. 依赖关系

```
commander_v2.py
  ├── task_analyzer.py       (Redis)
  ├── auto_scaler.py         (Redis + PM2)
  ├── reliable_comm.py       (Redis)
  ├── ab_tester.py           (Redis + MongoDB)
  ├── failover.py            (Redis + MongoDB + Sentinel)
  ├── llm_router.py          (Redis + MongoDB + LLM API)
  ├── a2a_protocol.py        (Redis)
  ├── agent_lifecycle_v2.py  (Redis + MongoDB + LLM API + PM2)
  ├── skill_manager.py       (Redis + MongoDB + npm)
  ├── mcp_manager.py         (Redis + MongoDB)
  └── extension_router.py    (上面两个 + LifecycleManager)
```

## 8. 扩展点

| 扩展点 | 方式 | 示例 |
|--------|------|------|
| 新优化引擎 | 新增 .py 模块 → commander_v2 集成 | 优化七：成本控制 |
| 新 Skill | `SkillManager.install_skill("my-skill")` | 行业垂直翻译 |
| 新 MCP | `MCPManager.register_mcp_server("my-tool")` | 新数据源 |
| 新 Agent 角色 | `agent-factory.sh create my-role` | 客服机器人 |
| 新路由策略 | `ABTester.create_test(strategy_config)` | 基于优先级路由 |
| 新 MCP (内部) | `MCPManager.register_mcp_server("lightingmetal-rag", command="python3")` | RAG 知识库 |

## 9. RAG 知识库检索层（v2.3.1）

```
MongoDB (page_content / cms_article_main)
    │
    ▼  build_knowledge_base.py
BGE-M3 向量化 (1024维 dense embedding)
    │
    ▼  VectorStore
┌─────────────────────────────────────┐
│ Redis Stack (RediSearch HNSW)      │  ← 主模式
│ 或 基础 Redis (手动余弦相似度)      │  ← 自动降级
└─────────────────────────────────────┘
    │
    ▼  MCP JSON-RPC (stdio)
lightingmetal-rag MCP Server
    │
    ├─ search_product_knowledge  → 混合向量搜索
    └─ get_product_context       → 格式化知识上下文
    │
    ▼  ExtensionRouter 自主发现
关键词: "知识库"→"产品查询"→"RAG" → lightingmetal-rag
    │
    ▼  Agent 直接调用
商务经理.询盘 → RAG 检索 → LLM 回复
售前经理.报价 → RAG 规格 → 报价方案
```

### 双模存储策略

| 模式 | 需求 | 检索方式 | 性能 |
|------|------|---------|------|
| **Redis Stack** | RediSearch + RedisJSON 模块 | KNN HNSW (M=16, EF=200) | ~10ms |
| **手动余弦** | 纯 Python + numpy | scan 全量 + 余弦相似度排序 | ~100ms (1万条) |

### MCP 工具

| 工具 | 输入 | 输出 | 使用方 |
|------|------|------|--------|
| `search_product_knowledge` | query, top_k, category | [{product_name, description, score}] | 所有 Agent |
| `get_product_context` | query, top_k | 格式化中文上下文文本 | 商务经理 / 售前经理 |

## 10. Docker 部署拓扑

```
┌─────────────────────────────────────────────────────────┐
│                  Docker Compose (v3.9)                   │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐                    │
│  │  Redis 7     │  │ MongoDB 7    │                    │
│  │  AOF+RDB 混合│  │  9 集合 初始化│                    │
│  │  256MB max   │  │  认证+索引    │                    │
│  └──────┬───────┘  └──────┬───────┘                    │
│         │                  │                            │
│         └────────┬─────────┘                            │
│                  │                                      │
│  ┌──────────────▼──────────────────────────────────┐   │
│  │  Commander 容器 (Supervisord 5进程)              │   │
│  │  ┌──────────────────────────────────────────┐   │   │
│  │  │ commander        → 六大引擎 + 扩展路由    │   │   │
│  │  │ dashboard        → Web UI (3003)          │   │   │
│  │  │ agent-business   → 商务经理                │   │   │
│  │  │ agent-presales   → 售前经理                │   │   │
│  │  │ agent-translator → 翻译官                  │   │   │
│  │  └──────────────────────────────────────────┘   │   │
│  └────────────────────────────────────────────────┘   │
│                                                         │
│  ┌─ Profile: rag ─────────────────────────────────┐    │
│  │ Redis Stack (向量搜索) + RAG Server (MCP)       │    │
│  └────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

### 三种部署模式

| 模式 | 命令 | 适用 |
|------|------|------|
| **分离架构** | `docker compose up -d` | 生产环境 |
| **RAG 增强** | `docker compose --profile rag up -d` | 知识检索 |
| **开发单容器** | `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` | 本地验证 |

### 启动序列

```
entrypoint.sh
  ├─ 阶段1: 等待 Redis (60次重试) + MongoDB (30次重试)
  ├─ 阶段2: 检查 RAG 知识库索引 (如启用)
  ├─ 阶段3: 从 mcp-servers.json 注册到 Redis mcp:registry
  └─ 阶段4: Supervisor 启动 5 进程
        ├─ commander (priority 100)
        ├─ dashboard (priority 200)
        ├─ agent-business / agent-presales / agent-translator (priority 300)
        └─ commander-tui (priority 400)
```
