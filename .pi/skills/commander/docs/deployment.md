# Commander v2.3 — 多Agent系统总指挥

> **LightingMetal 开源项目** · Apache 2.0 License
>
> 一个具备自我进化能力的多Agent编排系统，专为外贸B2B独立站设计。
> 8,600+ 行生产级 Python 代码，六大优化引擎，四维可观测性。

---

## 🎯 一句话描述

**Commander** 是一个基于 Redis Pub/Sub 的轻量级多Agent调度系统。它能自动创建/调度/销毁子Agent，支持P2P直连协作、智能路由、弹性伸缩、故障转移——并且可以在运行时动态安装 Skill 和注册 MCP Server，实现真正的自我进化。

## 🏗️ 架构概览

```
                  ┌──────────────────────────────┐
                  │        Commander v2.3         │
                  │  ┌────────────────────────┐  │
                  │  │  六大优化引擎           │  │
                  │  │  ① 任务去重             │  │
                  │  │  ② 弹性伸缩             │  │
                  │  │  ③ 双通道可靠通信       │  │
                  │  │  ④ A/B测试自进化        │  │
                  │  │  ⑤ 故障转移+五级降级    │  │
                  │  │  ⑥ LLM智能路由          │  │
                  │  └────────────────────────┘  │
                  │  ┌────────────────────────┐  │
                  │  │  动态扩展系统 (v2.3)    │  │
                  │  │  Skill 动态挂载         │  │
                  │  │  MCP Server 动态注册     │  │
                  │  │  扩展路由决策器          │  │
                  │  └────────────────────────┘  │
                  └──────┬───────────┬───────────┘
                         │           │
              ┌──────────▼──┐  ┌─────▼──────────┐
              │  Redis 总线  │  │  PM2 进程管理  │
              │  (Pub/Sub +  │  │  (创建/销毁/   │
              │   List +ACK) │  │   守护)        │
              └──────┬───────┘  └────────────────┘
                     │
        ┌────────────┼────────────┐
        │            │            │
   ┌────▼───┐  ┌────▼───┐  ┌────▼───┐
   │ 翻译官  │  │商务经理 │  │售前经理 │  ← P2P 直连协作
   └────────┘  └────────┘  └────────┘
```

## ✨ 核心特性

### 六大优化引擎

| # | 引擎 | 文件 | 功能 |
|---|------|------|------|
| ① | **TaskAnalyzer** | `task_analyzer.py` | MD5指纹去重 + 关键词倒排索引 + 启发式拆分 |
| ② | **AutoScaler** | `auto_scaler.py` | 队列≥3扩容 / 空闲>600s缩容 / 核心Agent保护 |
| ③ | **ReliableComm** | `reliable_comm.py` | Pub/Sub广播 + List关键指令 + ACK超时重试 |
| ④ | **ABTester** | `ab_tester.py` | 50/50流量分流 → 24h评估 → 自动推广/废弃 |
| ⑤ | **Failover + Degradation** | `failover.py` | 心跳30s → 备选链式切换 → L0~L4五级降级 |
| ⑥ | **LLMRouter** | `llm_router.py` | LLM语义路由 + 规则vsLLM的A/B对比 |

### 动态扩展系统（v2.3 新增）

| 模块 | 文件 | 功能 |
|------|------|------|
| **SkillManager** | `skill_manager.py` | npm/github/local 三源安装/卸载 Skill + Redis注册表 |
| **MCPManager** | `mcp_manager.py` | MCP Server 注册/注销/健康检查 + 热重载通知 |
| **ExtensionRouter** | `extension_router.py` | 任务→能力缺口→扩展策略的自动决策引擎 |

### Agent 生命周期管理

- **四象限分级**：Core / Strategic / Utility / Ephemeral
- **动态工厂**：`agent-factory.sh` — analyze → create → spawn → destroy
- **自我进化**：`SelfEvolvingCommander` — 历史模式分析 + A/B验证
- **安全边界**：`SafetyBoundary` — 资源上限 + 操作白名单

### 可观测性

- **Dashboard v2**：四维指标卡片（集群/性能/成本/系统）+ 五条告警规则 + 3秒刷新
- **TUI 终端**：实时状态 + 一键操作
- **日志归档**：自动归档通信日志

## 🚀 快速开始

### Docker（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/lightingmetal/commander.git
cd commander

# 2. 启动（Redis + Commander + Dashboard）
make start

# 3. 查看 Dashboard
open http://localhost:3003

# 4. 查看日志
make logs

# 5. 停止
make stop
```

### 本地开发

```bash
# 安装依赖
make dev-setup

# 启动 Redis
make dev-redis

# 启动 Commander
make dev-start

# 启动 Dashboard
make dev-dashboard
```

### 配置环境变量

```bash
# .env
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=commander-secret
LLM_API_KEY=sk-xxx         # 可选：启用 LLM 智能路由
LLM_BASE_URL=https://api.deepseek.com/v1
MONGO_URI=mongodb://...    # 可选：启用持久化
```

## 📦 文件清单

```
commander/
├── src/                          # 核心引擎（12个Python模块, 6,380行）
│   ├── commander_v2.py           # Commander v2.3 主入口
│   ├── task_analyzer.py          # 优化一：任务去重
│   ├── auto_scaler.py            # 优化二：弹性伸缩
│   ├── reliable_comm.py          # 优化三：双通道通信
│   ├── ab_tester.py              # 优化四：A/B测试
│   ├── failover.py               # 优化五：故障转移+五级降级
│   ├── llm_router.py             # 优化六：LLM智能路由
│   ├── a2a_protocol.py           # A2A协议适配层
│   ├── agent_lifecycle_v2.py     # Agent生命周期管理
│   ├── skill_manager.py          # 扩展系统：Skill管理
│   ├── mcp_manager.py            # 扩展系统：MCP管理
│   ├── extension_router.py       # 扩展系统：路由决策
│   └── experience/
│       └── patterns.json         # 经验积累
│
├── agents/                       # Agent 运行时（16个文件, 2,254行）
│   ├── agent.sh                  # Agent 通用启动脚本（v2 P2P）
│   ├── agent-factory.sh          # 动态 Agent 工厂
│   ├── agent-core.py             # 通信核心（Pub/Sub + P2P）
│   ├── agent-commander.py        # v1 Commander入口
│   ├── agent-translator.py       # 翻译官 Agent
│   ├── agent-business.py         # 商务经理 Agent
│   ├── agent-presales.py         # 售前经理 Agent
│   ├── agent-registry.py         # 注册表管理
│   ├── dashboard_v2.py           # Dashboard v2（Flask, port 3003）
│   ├── dashboard.py              # Dashboard v1（http.server）
│   ├── commander-tui.py          # TUI 终端
│   ├── dashboard-tui.sh          # TUI 脚本
│   ├── memory-manager.sh         # 内存管理
│   ├── archive-comm-logs.py      # 日志归档
│   ├── redis-recovery.py         # Redis 恢复
│   └── ecosystem.agents.cjs      # PM2 配置
│
├── scripts/
│   ├── entrypoint.sh             # Docker 入口
│   └── setup.sh                  # 初始化脚本
│
├── docs/
│   ├── ARCHITECTURE.md           # 架构设计文档
│   ├── API.md                    # API 参考
│   └── CONSTITUTION.md           # 宪法（约束规则）
│
├── LICENSE                       # Apache 2.0
├── NOTICE                        # 版权声明
├── VERSION                       # 版本号
├── README.md                     # 本文件
├── CHANGELOG.md                  # 变更日志
├── requirements.txt              # Python 依赖
├── Dockerfile                    # 容器构建
├── docker-compose.yml            # 一键部署
└── Makefile                      # 常用命令
```

## 📊 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|----------|
| **v2.3** | 2026-05-24 | + 动态扩展系统：SkillManager + MCPManager + ExtensionRouter |
| **v2.2** | 2026-05-23 | + 优化五：AgentFailover + TaskDegradation L0~L4 + Redis Sentinel |
| **v2.1** | 2026-05-23 | + 优化一至四：TaskAnalyzer + AutoScaler + ReliableComm + ABTester |
| **v2.0** | 2026-05-23 | + P2P扁平通信 + replyTo + forward |
| **v1.0** | 2026-05-23 | 初始：Redis Pub/Sub + PM2 + Dashboard |

## 🔧 宪法（Constitution）

所有模块必须遵守七条宪法规则：

| 规则 | 内容 |
|------|------|
| **R1** | Redis只读不删。禁止 DEL/FLUSH `page:*` `agent:*` `lightingmetal:*` 前缀 |
| **R2** | Agent上限10个。并行子Agent不超过10个 |
| **R3** | 报价先存草稿。发送前必须存储MongoDB |
| **R4** | 消息格式标准化。强制JSON协议 |
| **R5** | 故障自动降级。30s超时重试3次，连续失败3次重建 |
| **R6** | P2P优先。Agent协作优先直连，减少Commander瓶颈 |
| **R7** | 消息不可篡改。只可追加`forwardedBy`，不可改`payload` |

## 📄 许可证

本项目基于 **Apache License 2.0** 开源。

```
Copyright 2026 LightingMetal

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。请确保：

1. 遵守七条宪法规则
2. 所有消息使用标准JSON格式
3. Redis 前缀使用 `commander:*` / `extensions:*`
4. 新模块通过 `try/except ImportError` 实现可选依赖
