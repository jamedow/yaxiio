---
name: commander
description: LightingMetal多Agent系统总指挥。负责创建/调度/销毁子Agent，通过Redis Pub/Sub协调并行任务，提供Dashboard可视化，自我进化。当需要多Agent协作、并行任务、批量翻译、询价流程自动化时使用此技能。支持RAG知识库检索、MCP Server动态挂载、Docker一键部署。
---

# Commander — 多Agent系统总指挥 v2.3.1

## ⛔ Constitution

**R1**：Redis只读不删。禁止 DEL/FLUSH 任何 `page:*` `agent:*` `lightingmetal:*` 前缀的key。

**R2**：Agent上限10个。并行子Agent不超过10个，防止资源耗尽。

**R3**：所有报价先存草稿。客户报价在发送前必须先存储 MongoDB 草稿。

**R4**：消息格式标准化。所有Agent间通信强制使用标准JSON协议。

**R5**：故障自动降级。Agent 30s无响应重试3次，连续失败3次自动销毁重建。

**R6**：P2P优先。Agent间协作优先走直连通道，减少Commander瓶颈。仅生命周期管理（创建/销毁/监控）必须经过Commander。

**R7**：消息不可篡改。Agent收到消息后只可追加 `forwardedBy` 字段，不可修改原始 `payload`。

---

## 🎯 核心能力

### 通信架构：中心调度 + 扁平P2P

```
        ┌──────────────────────────┐
        │        Commander         │ ← 中心调度(创建/销毁/监控)
        └──────┬───────┬───────────┘
               │       │
        ┌──────▼───────▼──────────┐
        │    Redis Pub/Sub 总线    │
        └──┬────────┬──────────┬──┘
           │        │          │
     ┌─────▼──┐ ┌──▼────┐ ┌───▼──────┐
     │翻译官   │←──→│商务经理│←──→│售前经理   │  ← P2P直连
     └─────────┘ └───────┘ └──────────┘
```

Agent 之间可以绕过 Commander 直接通信（P2P），提升协作效率：
- 商务经理 直接请求 售前经理 生成报价
- 售前经理 直接请求 翻译官 翻译规格书
- 翻译官 直接通知 商务经理 翻译完成

消息通过 `replyTo` 字段指定回复目标，无需 Commander 中转。

### 1. Agent 集群管理

| Agent | 订阅频道 | 职责 |
|-------|---------|------|
| 翻译官 | `lightingmetal:agent:翻译官` | 审计中文残留、LLM批量翻译、写入MongoDB+Redis |
| 商务经理 | `lightingmetal:agent:商务经理` | 接待客户、挖掘需求、输出结构化需求清单 |
| 售前经理 | `lightingmetal:agent:售前经理` | 查询MongoDB产品库、生成报价方案 |

**创建命令**：
```bash
pm2 start /app/.pi/agents/runtime/agent.sh --name agent-translator -- 翻译官
pm2 start /app/.pi/agents/runtime/agent.sh --name agent-business -- 商务经理
pm2 start /app/.pi/agents/runtime/agent.sh --name agent-presales -- 售前经理
```

**销毁命令**：
```bash
pm2 delete agent-translator agent-business agent-presales
```

**Agent 脚本路径**：`/app/.pi/agents/runtime/agent.sh`

### 动态Agent工厂（按需创建/销毁）

不再需要预先定义所有Agent。根据任务自动分析→创建→启动：

```bash
# 1. 分析任务，建议需要什么Agent
bash /app/.pi/agents/runtime/agent-factory.sh analyze "审计俄语页面中文残留并翻译"
# → 建议: 翻译官, 审计官

# 2. 动态创建Skill + 注册Agent
bash /app/.pi/agents/runtime/agent-factory.sh create ru-auditor 俄语审计官 "审计俄语中文残留，翻译写入MongoDB"

# 3. 启动Agent进程
bash /app/.pi/agents/runtime/agent-factory.sh spawn ru-auditor 俄语审计官

# 4. 任务完成后销毁
bash /app/.pi/agents/runtime/agent-factory.sh destroy ru-auditor
```

**完整闭环**：`analyze → create → spawn → 运行 → destroy`，全程自动。

### 2. 标准通信协议（v2 支持P2P）

**消息类型**：

| type | 方向 | 用途 | 示例 |
|------|------|------|------|
| `task` | Commander→Agent | 分派任务 | 审计页面 |
| `request` | Agent→Agent (P2P) | 直接请求协助 | 售前→翻译: 翻译规格书 |
| `response` | Agent→任意 | 回复结果 | 任务完成/失败 |
| `error` | Agent→任意 | 错误报告 | MongoDB写入失败 |
| `heartbeat` | Agent→Commander | 心跳上报 | 在线状态 |
| `heartbeat_check` | Commander→Agent | 心跳检测 | 你还在吗 |
| `shutdown` | Commander→Agent | 关闭指令 | 准备下线 |

**`replyTo` 字段**：指定回复目标Agent名（如 `翻译官`、`商务经理`），消息直达不经过Commander。

**P2P 直连示例**：

```bash
# 商务经理直接请求售前经理报价（不经过Commander）
docker exec redis-centos7 redis-cli -a '$REDIS_PASSWORD' PUBLISH lightingmetal:agent:售前经理 \
  '{"from":"商务经理","to":"售前经理","type":"request","taskId":"p2p-001","replyTo":"商务经理","payload":{"action":"generate_quote","data":{"product":"solar-ground-screw","qty":5000}}}'

# 售前经理收到后处理，回复到商务经理频道
# 售前经理也可以请求翻译官协助（嵌套P2P）
docker exec redis-centos7 redis-cli -a '$REDIS_PASSWORD' PUBLISH lightingmetal:agent:翻译官 \
  '{"from":"售前经理","to":"翻译官","type":"request","taskId":"p2p-002","replyTo":"售前经理","payload":{"action":"translate","data":{"text":"热镀锌螺旋地桩","target":"ru"}}}'
```

**消息转发**（Agent自动处理）：
```bash
# Agent内部：如果收到无法处理的任务，自动forward给正确Agent
forward "$payload" "商务经理"  # 转发任务到商务经理频道
```

### 3. Dashboard 可视化

**Dashboard v2** (推荐 — Flask, 四维可观测性 + 智能告警) :
```bash
# 无 MongoDB（仅 Redis 指标）:
python3 /app/.pi/agents/runtime/dashboard_v2.py
# 完整模式（Redis + MongoDB 持久化指标）:
python3 /app/.pi/agents/runtime/dashboard_v2.py 'mongodb://user:pass@host:27017/'
# 访问: http://localhost:3003/dashboard
```

功能：四维指标卡片（Agent集群/性能/成本/系统）、任务概览、五条智能告警规则（成功率/队列/成本/失联/Redis）、3秒自动刷新、Brand 色系 UI。

**Dashboard v1** (向后兼容 — 轻量 http.server) :
```bash
pm2 start /app/.pi/agents/runtime/dashboard.py --name dashboard --interpreter python3
# 访问: http://localhost:3002
```

功能：Agent 集群状态（在线/离线）、任务队列（实时刷新）、进度条、Commander 日志、一键操作按钮（启动/分派/重置/销毁）。

**终端 TUI**（备选）：
```bash
bash /app/.pi/agents/runtime/dashboard-tui.sh
# 按键: s)启动Agent  d)销毁  t)测试任务  q)退出
```

### 4. 并行任务分派

```bash
# 同时向3个Agent发布不同任务（并行执行）
docker exec redis-centos7 redis-cli -a '$REDIS_PASSWORD' PUBLISH lightingmetal:agent:翻译官 '{"from":"commander","to":"翻译官","type":"task","taskId":"T1"}' &
docker exec redis-centos7 redis-cli -a '$REDIS_PASSWORD' PUBLISH lightingmetal:agent:商务经理 '{"from":"commander","to":"商务经理","type":"task","taskId":"T2"}' &
docker exec redis-centos7 redis-cli -a '$REDIS_PASSWORD' PUBLISH lightingmetal:agent:售前经理 '{"from":"commander","to":"售前经理","type":"task","taskId":"T3"}' &
wait
```

### 5. 故障恢复

| 场景 | 处理 |
|------|------|
| Agent 无心跳 | 30s超时重试，最多3次 |
| Agent 连续失败3次 | `pm2 delete` 销毁，`pm2 start` 重建 |
| Redis 断连 | 暂停新任务，已运行任务继续 |
| MongoDB 写入失败 | 本地缓存，恢复后补写 |

---

## 🛠️ 标准工作流

### 流程一：俄语翻译审计

```
Commander → PUBLISH audit_request → 翻译官频道
翻译官 → 扫描ru页面中文 → 提取MongoDB zh源数据
翻译官 → PUBLISH status_report → Commander频道
Commander → 确认报告 → 决策是否翻译
```

### 流程二：客户询盘（P2P协作）

```
客户 → 商务经理: 询盘
商务经理 → 售前经理: request (P2P) "生成报价"
售前经理 → 翻译官: request (P2P) "翻译规格书为俄语"
翻译官 → 售前经理: response (P2P) "翻译完成"
售前经理 → 商务经理: response (P2P) "报价单已生成"
商务经理 → 客户: 发送报价
```

全程无需Commander介入，只在最后汇总时Commander记录日志。

### 流程三：批量翻译

```
Commander → 根据任务量创建 N 个翻译官(N≤5)
Commander → PUBLISH N个并行任务
翻译官1 → 处理 pages[0:N/5]
翻译官2 → 处理 pages[N/5:2N/5]
...
全部完成 → Commander汇总 → 写MongoDB → 销毁翻译官
```

---

## 📊 自我进化

### 日志记录
每次任务完成后记录到 `agent_optimization_log`：
```json
{
  "taskId": "ru-audit-20260523",
  "taskType": "audit",
  "totalDuration": 90000,
  "subTasks": [{"name":"index","status":"success","duration":3000}],
  "overallStatus": "success"
}
```

### 优化规则
1. 某类任务子任务经常超时 → 下次减小拆分粒度
2. 某Agent响应快、成功率高 → 优先分派
3. 重试频繁 → 延长超时阈值（上限60s）
4. 等待队列>3 → 允许创建更多Agent（上限10）

---

## 🧬 六大优化引擎 + A2A 协议层 (v2.3)

Commander 内置六个优化模块 + A2A 标准化通信层，均使用 `commander:*` Redis 前缀（遵守 R1），可独立启用/禁用。

### 优化一：智能任务去重 (TaskAnalyzer)

**文件**: `/app/.pi/skills/commander/task_analyzer.py`

```
任务描述 → 提取关键词(jieba/清洗) → MD5指纹 → Redis查重
                                              ├─ 精确匹配: 同指纹24h内 → 复用历史摘要
                                              └─ 模糊匹配: 关键词70%重叠 → 提示相似任务
```

- 支持 jieba 分词（可选，fallback 到清洗切分）
- 关键词倒排索引加速模糊查重
- 指纹缓存 7 天，任务记忆 24 小时
- 启发式任务拆分建议（按翻译/审计/报价/询盘关键词）

### 优化二：Agent 弹性伸缩 (AutoScaler)

**文件**: `/app/.pi/skills/commander/auto_scaler.py`

```
等待队列 ≥ 3 → 扩容 (每次1~2个Agent, 上限10)
Agent空闲 > 600s → 缩容 (保留至少2个核心Agent)
```

- 核心 Agent（翻译官/商务经理/售前经理）受保护，至少保留 2 个
- 通过 PM2 动态创建/销毁，全程自动
- 状态全部使用 TTL 自动回收（R1 合规）

### 优化三：双通道通信 + ACK (ReliableComm)

**文件**: `/app/.pi/skills/commander/reliable_comm.py`

```
┌─ Pub/Sub 通道 ──── 广播、心跳、结果通知 (低延迟, 尽力送达)
└─ List 通道 ─────── 关键指令 (持久化、FIFO、ACK确认, 绝不丢失)
                       └─ ACK超时 → 重试3次 → 降级处理
```

- 后台线程阻塞监听 `commander:agent:command:{agent_id}` List
- 指令通过 `send_critical_command(target, cmd, expect_ack=True)` 发送
- ACK 超时 5s，最多重试 3 次
- 对外暴露 `register_handler()` 注册自定义指令处理器

### 优化四：A/B 测试自进化 (ABTester)

**文件**: `/app/.pi/skills/commander/ab_tester.py`

```
提出策略 → route_task(A/B分流50%) → 记录结果 → 24h评估
                                               ├─ B组提升>10% → 自动推广
                                               └─ 无明显提升 → 自动废弃
```

- 最小样本数门槛（每组10个），不足自动延长测试周期
- 所有测试数据归档到 `commander:ab_test:archive:*`，永久保留
- 推广历史记录到 `commander:ab_test:promotions`
- 支持自定义拆分策略配置（granularity/parallel_limit/agent_preference）

### 优化五：故障转移 + Redis高可用 + 五级降级 (AgentFailover/TaskDegradation)

**文件**: `/app/.pi/skills/commander/failover.py`

```
心跳监测(30s) → 超时判定失联 → 备选Agent切换
                                   ├─ 售前经理失败 → 通用报价Agent
                                   ├─ 商务经理失败 → 智能客服Agent
                                   ├─ 翻译官失败   → 通用翻译Agent
                                   └─ 全部失败     → L4 兜底降级

五级降级: L0(全量) → L1(无售前) → L2(无商务) → L3(无翻译) → L4(仅Commander)
```

- `AgentFailover`: 30s心跳超时 → 备选链式切换 → 最终降级模板
- `TaskDegradation`: 按任务类型(询盘/翻译/审计/报价) + 可用Agent 动态判定
- `RedisHAWrapper`: Sentinel 读写分离 + 自动故障切换
- CommanderV2集成：heartbeat同步 → 连续3次失败触发故障转移 → L4直接降级

### 🧩 A2A 协议适配层 (AgentDiscovery)

**文件**: `/app/.pi/skills/commander/a2a_protocol.py`

```
A2AAdapter:   Redis ↔ A2A 双向转换 (Google A2A-compatible)
AgentCard:    角色 → 输入/输出 JSON Schema (6种预置)
AgentDiscovery: 能力注册 → 倒排索引 → 能力发现 → 模糊匹配
```

- `find_best_match(capability)`: 能力+状态双重筛选（idle优先）
- 6个标准角色预置 Schema：商务经理/售前经理/翻译官/审计官/俄语审计官 + 通用
- 精确 → 模糊匹配自动回退
- LLMRouter 优先走 AgentDiscovery（含完整Schema），fallback到 commander:agents:active
- CommanderV2 启动时自动注册所有静态 Agent 能力卡片

### 优化六：LLM 智能路由 + 路由策略 A/B 测试 (LLMRouter/RouteABTester)

**文件**: `/app/.pi/skills/commander/llm_router.py`

```
任务 + Agent能力(AgentDiscovery) + 历史表现 → LLM决策 → 最优Agent
                                                 ├─ OpenAI兼容
                                                 ├─ LLM不可用 → fallback规则路由
                                                 └─ RouteABTester 24h A/B对比
```

- `LLMRouter`: 语义理解路由（兼容OpenAI/DeepSeek/通义千问）
- `RouteABTester`: 规则 vs LLM 路由 24h A/B 自动决策
- 与 `ab_tester.py` 互补（后者测调度参数，本模块测路由策略）

### 集成入口: CommanderV2

**文件**: `/app/.pi/skills/commander/commander.py`

```python
from task_analyzer import TaskAnalyzer
from auto_scaler import AutoScaler
from reliable_comm import ReliableComm
from ab_tester import ABTester
from failover import AgentFailover, TaskDegradation
from llm_router import LLMRouter, RouteABTester

# 基础模式：六大引擎全部激活
commander = CommanderV2()
result = commander.handle_task("审计俄语页面中文残留并翻译")

# LLM 增强模式：启用 DeepSeek 路由
commander = CommanderV2(
    llm_api_key="sk-xxx",
    llm_base_url="https://api.deepseek.com/v1",
    llm_model="deepseek-chat",
)

# Sentinel 高可用模式
commander = CommanderV2(
    use_sentinel=True,
    sentinel_hosts=["redis-sentinel-1", "redis-sentinel-2"],
)
```

完整流程：`去重 → A/B分流(调度策略) → 拆分 → 降级检测 → 伸缩 → LLM路由(A/B路由策略) → 双通道分发+故障转移 → 指纹缓存`

**与 v1 兼容**：`commander.py` 的 `main()` 函数提供与 `agent-commander.py` 完全相同的 Pub/Sub 主循环，可无缝替换。

## 🔧 文件清单

| 文件 | 用途 | 状态 |
|------|------|------|
| `task_analyzer.py` | 任务指纹去重 + 关键词拆分建议 | ✅ v2.1 |
| `auto_scaler.py` | Agent 弹性扩缩容 (PM2) | ✅ v2.1 |
| `reliable_comm.py` | 双通道通信 + ACK 确认 | ✅ v2.1 |
| `ab_tester.py` | A/B 测试自进化策略 | ✅ v2.1 |
| `failover.py` | 故障转移 + Sentinel高可用 + 五级降级 | ✅ v2.2 |
| `a2a_protocol.py` | A2A适配器 + 能力发现 + JSON Schema | ✅ v2.3 |
| `llm_router.py` | LLM 智能路由 + 路由策略 A/B 对比 | ✅ v2.3 |
| `commander.py` | 集成六大优化的 Commander 编排引擎 | ✅ v0.2.6 |
| `gateway.py` | WebSocket/HTTP 统一接入网关 + 会话管理 | ✅ v0.2.6 |
| `yaxiio.py` | 雅溪核心引擎（宪法 + 五层流水线） | ✅ v0.2.6 |
| `experience/patterns.json` | 经验积累与已知模式 | ✅ |
| `../agents/runtime/dashboard_v2.py` | 增强可观测性 + 五条告警 + 四维指标 (Flask, port 3003) | ✅ v2.2 |
| `../agents/runtime/agent-commander.py` | v1 运行时入口 | ✅ v1.0 |
| `../agents/runtime/agent.sh` | Agent 通用启动脚本 | ✅ |
| `../agents/runtime/agent-factory.sh` | 动态 Agent 工厂 | ✅ |
| `../agents/runtime/dashboard.py` | Web 仪表盘 (v1, http.server, port 3002) | ✅ |

## 🧠 RAG 知识库检索 (v2.3.1)

Commander 集成 lightingmetal-rag MCP Server，为 Agent 提供产品知识库语义检索能力。

### 架构

```
MongoDB → BGE-M3 向量化 → Redis Stack (HNSW COSINE) → MCP Server → Agent
                                                              │
                                                    自动降级: 基础 Redis 手动余弦
```

### Commander 自主挂载

```
任务关键词: "产品查询" / "知识库" / "RAG"
  → ExtensionRouter 检测缺口
  → register_mcp lightingmetal-rag (risk=auto)
  → Agent 可以直接调用 search_product_knowledge / get_product_context
```

### Agent 调用示例

```python
# 商务经理在回复客户前检索知识库
from server import LightingMetalRAG
rag = LightingMetalRAG(redis_host="redis-stack")
context = rag.get_product_context("沙特光伏电站螺旋地桩防腐方案")
reply = llm.chat(f"基于以下产品知识回复客户:\n{context}\n客户问题:{query}")
```

### 相关文件

| 文件 | 职责 |
|------|------|
| `ai-server/mcp-servers/lightingmetal-rag/server.py` | MCP Server (JSON-RPC stdio) |
| `ai-server/scripts/build_knowledge_base.py` | MongoDB → 向量化 → Redis Stack |
| `mcp_manager.py::KNOWN_SERVERS` | 8个 RAG 关键词映射 |
| `extension_router.py::CAPABILITY_KEYWORD_MAP` | RAG 自动检测 |

## 🐳 Docker 部署 (v0.2.6)

> **首次使用？** 先看 [环境变量配置指南](docs/ENV.md) 了解需要填哪些密钥。

```bash
# 1. 配置环境变量
cp deploy/commander/.env.example deploy/commander/.env
# 编辑 .env: 必须填入 REDIS_PASSWORD 和 LLM_API_KEY

# 2. 启动
docker compose up -d                     # 生产模式 (Redis + MongoDB + Commander)
docker compose --profile rag up -d       # RAG 增强模式

# 访问
http://localhost:3003/dashboard          # Dashboard Web UI
http://localhost:7681                    # Commander TUI
```

### 部署文件

| 文件 | 职责 |
|------|------|
| `deploy/commander/docker-compose.yml` | 主配置（3模式/6服务） |
| `deploy/commander/Dockerfile` | 生产镜像 (Supervisor 5进程) |
| `deploy/commander/Dockerfile.rag` | RAG 镜像 (BGE-M3) |
| `deploy/commander/config/redis.conf` | AOF+RDB 混合持久化 |
| `deploy/commander/config/mongo-init.js` | 9集合+索引初始化 |
| `deploy/commander/config/supervisord.conf` | 进程管理 |
| `deploy/commander/docs/DEPLOY.md` | 完整运维手册 |

## 📚 完整文档

| 文档 | 内容 |
|------|------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构 + RAG + Docker 拓扑 |
| [INTEGRATION.md](docs/INTEGRATION.md) | 全栈集成指南（数据流/部署/故障恢复） |
| [API.md](docs/API.md) | Commander API + MCP 工具接口 |
| [CONSTITUTION.md](docs/CONSTITUTION.md) | 安全边界与约束 |
| [DEPLOY.md](../../../deploy/commander/docs/DEPLOY.md) | 10章运维手册 |

## 🔧 Version
v0.2.6 | 2026-05-29 | 安全问题修复：Arsenal Bug修复 + 移除所有硬编码密码 + Shell脚本Docker解耦；文件重命名：commander_v2→commander / commander_v3→gateway / yaxiio核心引擎保留
v2.3.1 | 2026-05-24 | +RAG知识库检索：BGE-M3向量化 + Redis Stack HNSW + lightingmetal-rag MCP Server + Commander自动挂载 + 双模降级存储 + Agent集成示例；+Docker一体化部署：3种模式(分离/RAG增强/开发单容器) + Supervisor进程管理 + AOF+RDB持久化 + 健康检查 + 一键启动
v2.3 | 2026-05-23 | +优化六：LLMRouter LLM智能路由(OpenAI兼容+DeepSeek) + RouteABTester 路由策略A/B对比(规则 vs LLM 24h自动决策)；CommanderV2分发前自动LLM路由+RouteABTester分流；dashboard_v2.py增强可观测性+告警
v2.2 | 2026-05-23 | +优化五：AgentFailover故障转移(心跳→备选切换→降级模板)、TaskDegradation五级降级(L0~L4)、RedisHAWrapper Sentinel高可用(读写分离+自动故障切换)；CommanderV2集成五大优化+Sentinel可选启动
v2.1 | 2026-05-23 | +四大优化引擎：TaskAnalyzer任务去重、AutoScaler弹性伸缩(PM2)、ReliableComm双通道List+Pub/Sub+ACK确认、ABTester自进化策略(A/B测试)；CommanderV2集成入口；全部使用commander:*前缀合规R1
v2.0 | 2026-05-23 | +P2P扁平化通信：Agent间直连协作，replyTo字段，forward/request_help函数，4种消息类型扩展
v1.0 | 2026-05-23 | 初始版本：Redis Pub/Sub通信、PM2进程管理、Dashboard可视化、并行任务分派、故障恢复、自我进化
