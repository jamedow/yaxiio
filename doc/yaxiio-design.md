# 雅溪 Yaxiio — 设计文档

> 版本: v1.08 | 2026-05-27  
> 配套: [架构文档](./yaxiio-architecture-v2.md)

---

## 一、设计目标

Yaxiio 是一个为 LightingMetal 五金外贸 B2B 独立站服务的多 Agent 智能调度系统。核心目标三个：

1. **不替代人，替代重复劳动** — 翻译 6 语产品页、审计跨页面术语一致性、生成报价草稿，这些不该是人做的
2. **Commander 不做执行者** — 它只做编排和路由，具体工作由专业 Agent 完成
3. **自我进化** — 出错后能自己修，修不好能升级，最终能越做越好

---

## 二、核心设计决策

### 决策 1: 为什么是五层而不是微服务

**选了**: 五层 MCP (Model Context Protocol) 流水线，层间 HTTP 通信  
**没选**: 微服务 + 消息队列 (Kafka/RabbitMQ)

**理由**:
- 五层之间是**严格顺序依赖** (L1→L2→L3→L4→L5)，不是独立服务。微服务的优势（独立部署、独立扩容）在这里用不上
- MCP 协议简单 (JSON-RPC over HTTP)，调试方便。消息队列需要额外的运维复杂度
- 每层是独立进程，可以单独重启/升级，已经满足解耦需求
- 如果未来某层成为瓶颈，随时可以加实例 + 负载均衡

### 决策 2: 为什么 Commander 不能亲自执行

**选了**: 宪法 (YaxiioConstitution) 强制所有业务操作走五层流水线  
**没选**: Commander 自己 if/else 分发到 Python 函数

**理由**:
- v1.04 时 Commander 有 14 个硬编码的 `_run_xxx()` 方法，直接调用。这等于把五层架构架空
- 宪法只给 6 个系统管理操作开白名单 (agent_export/import, skill_export/import, session_end, status)
- 所有 `site_audit/fix/drill/build/deploy` 等业务操作强制走 L1→L5
- Commander 的精力留给编排和调度，不消耗在业务执行上
- 违宪行为被记录到 `yaxiio:constitution:violations`，可审计

### 决策 3: 为什么 Agent 是独立进程（神经元）而不是线程

**选了**: 每个 Agent = 一个 Python 子进程 (`neuron.py`)  
**没选**: Agent = 线程/协程

**理由**:
- 进程隔离: 一个 Agent 的 LLM 调用阻塞或 OOM 不影响其他 Agent
- Skill 隔离: 每个 Agent 加载自己的 SKILL.md，进程级隔离避免了 Prompt 泄露
- 生命周期独立: 可以单独重启某个 Agent 而不影响 Commander
- 多核利用: Python GIL 限制下，多进程是唯一真正并行的方式
- 开销可控: 每个 neuron 进程 ~25MB 内存，十个 Agent 也才 250MB

### 决策 4: 为什么用 Redis Pub/Sub 而不是 gRPC

**选了**: Redis Pub/Sub + JSON 消息  
**没选**: gRPC / REST API / WebSocket

**理由**:
- Redis 已经在架构里（缓存 + 状态存储），零额外依赖
- Pub/Sub 天然支持多对多通信：Commander → 多个 Agent，Agent → Agent (P2P)
- 消息格式简单 (JSON)，调试和审计直接 `redis-cli`
- 不需要服务发现 — Agent 订阅自己的频道名就行
- 如果需要可靠投递，未来可以在 Pub/Sub 上层加 ACK 机制

### 决策 5: 为什么是事件溯源而不是关系数据库存状态

**选了**: 任务状态机 + 不可变时间线 (Redis JSON)  
**没选**: MySQL 状态表

**理由**:
- 任务生命周期短 (最长几分钟到几小时)，不需要持久化到关系库
- 时间线不可变追加 → 天然审计轨迹，事后复盘一目了然
- Redis 读写亚毫秒级，状态机转换零延迟
- 大产出存独立 key (`yaxiio:output:{tid}:{sid}`)，主状态只存 hash 引用
- TTL 自动清理 (任务 24h，产出 7d)

### 决策 6: 为什么状态机是一层而不是五层

**选了**: 1 个任务 = 1 个状态机，5 个 milestone  
**没选**: 每个 L1-L5 层各一个独立状态机

**理由**:
- 五层之间是顺序依赖，不是独立状态转换。L2 不能从 IDLE 直接跳到 DONE 而不经过 L1
- 1 个状态机 + 5 个 milestone 已经足够表达所有状态
- 5 个独立 FSM 需要额外的协调逻辑（L1 DONE 触发 L2 ANALYZING），增加复杂度
- Dashboard 展示更简单：一行显示 `current_layer: L4, progress: 4/7`

### 决策 7: 双层互保而不是单 Guardian

**选了**: PM2 → Guardian-primary + Guardian-secondary, Redis leader 选举  
**没选**: 单 Guardian

**理由**:
- Guardian 本身也是进程，也会挂。单点故障不可接受
- Redis `SETNX` 实现 leader 选举，简单可靠
- Secondary 平时只监控心跳，不消耗资源
- Primary 挂了 → Secondary 在 30s 内接管
- PM2 守护 Primary（进程级），Secondary 守护 Primary（逻辑级），双重保险

### 决策 8: Commander 出问题由 Guardian 管，Agent 出问题由系统医生管

**选了**: 分层监督，互不越权  
**没选**: Commander 管一切

**理由**:
- Commander 的职责是编排调度，不是修 Agent。让它修 Agent 会回到 v1.04 的"万能执行者"老路
- Guardian 只做进程监控 + 元评分，不做业务逻辑
- 系统医生是一个普通 Agent，走正常的神经元启动 + Skill 加载 + LLM 处理流程
- 修 Agent 的业务逻辑（诊断 Prompt、A/B 测试）正好是 LLM 擅长的，交给医生做最合适

| 谁坏 | 谁修 | 修什么 |
|------|------|------|
| Agent 进程崩溃 | Commander | spawn_neuron 重启 |
| Agent 质量低 | 系统医生 | 分析 Prompt → 修复 → A/B |
| Commander 进程崩溃 | Guardian | 重启进程 |
| Commander 调度质量低 | Guardian | 元评分 → 渐进式修复 |
| Guardian 崩溃 | PM2 / 另一个 Guardian | 进程重启 / 接管 |

---

## 三、设计模式

### 模式 1: 宪法模式 (Constitution Pattern)

Commander 的 `handle_task()` 不直接判断 action 走哪个分支，而是先通过宪法审查：

```python
verdict, reason = constitution.review(action, payload)
# ALLOWED → 白名单直通
# DELEGATED → 强制五层流水线
# REJECTED → 违宪拒绝
```

好处: 规则集中管理，新增白名单只需改 `SYSTEM_OPS` 集合，不需要改业务逻辑。

### 模式 2: Arsenal 模式 (Tool Registry Pattern)

Commander 的能力注册为具名工具，由流水线按需调用，非 Commander 主动执行：

```python
arsenal = {
    "audit_codebase": audit_fn,
    "fix_codebase":  fix_fn,
    ...
}
# L2 规划: intent → tool_name
# L4 执行: arsenal.call(tool_name)
```

好处: Commander 和工具解耦，新增工具只需注册，不改变编排逻辑。

### 模式 3: 神经元模式 (Neuron Pattern)

每个 Agent 是标准化的独立进程，拥有:

```
感知器 (Redis Sub) → 大脑 (LLM) → 双手 (Tool Call) → 嘴巴 (Redis Pub) → 记忆 (Redis Set)
```

好处: 新增 Agent 类型只需写 SKILL.md + 在 `_agent_skill_map()` 注册，无需改 neuron.py。

### 模式 4: 依赖 DAG + 并行发射

复杂任务的子任务按依赖关系形成 DAG，无依赖的子任务并行发射到 ThreadPoolExecutor:

```
s1 ─┐
s2 ─┼─→ s4 ─┐
s3 ─┘       ├─→ s6 → s7
     s5 ────┘

发射顺序: {s1,s2,s3} 并行 → {s4,s5} 并行 → s6 → s7
```

好处: 最大化并行度，总耗时 = 关键路径长度而非子任务之和。

---

## 四、取舍与债务

### 已知取舍

| 取舍 | 选了 | 代价 |
|------|------|------|
| Redis 状态 vs MySQL | Redis JSON | 不支持复杂查询，只能按 key 查 |
| Pub/Sub vs 可靠队列 | Pub/Sub | 消息可能丢失（重启时），需要断点恢复弥补 |
| LLM 同步调用 vs 流式 | 同步 | 用户等待时间长（~20s/task），但实现简单 |
| 预设模板 vs LLM 动态拆解 | 预设优先 | 未覆盖的任务类型走 LLM 拆解，可能不稳定 |

### 技术债务

1. **LLMAdapter 双重调用** — `neuron.py` 用 OpenAI SDK 直接调，`workflow_engine.py` 用 LLMAdapter 异步调，两套代码
2. **神经元输出不可见** — `spawn_neuron` 的 stdout 去了 PIPE 但没人读，排查问题靠 Redis 记忆
3. **POLL_TIMEOUT 硬编码** — 子任务等待 60s 超时，复杂任务（如设计规范输出）可能不够
4. **双守护心跳共享一个 key** — primary 和 secondary 写同一个 `yaxiio:guardian:1:heartbeat`，区分不开
5. **Dashboard API 不稳定** — 端口 3003 经常 HTTP 500，健康检查误报

---

## 五、演进路线图

### 短期 (v1.09-v1.10)

- [ ] 子任务结果直接回复到 Commander 主循环（当前是轮询记忆）
- [ ] Dashboard 任务实时进度条 (`current_layer + progress_pct`)
- [ ] 神经元输出管道化（spawn_neuron 带上 `--log-to-redis`）
- [ ] Guardian CommanderScorer 接入实际评分逻辑

### 中期 (v1.11-v1.15)

- [ ] TaskDecomposer LLM 拆解替代预设模板
- [ ] 工作流可视化 (DAG 图, 实时进度)
- [ ] 模型路由接入 reasoning_effort A/B 测试
- [ ] Agent 记忆向量化 (RAG 检索历史经验)

### 长期 (v2.0+)

- [ ] 多 Commander 联邦（跨服务器分布式调度）
- [ ] Agent 自主注册（新 Skill 放目录 → 自动发现 → 自动注册）
- [ ] 完整 Ci/CD 集成（代码提交 → 自动审计 → 自动修 → 自动部署）

---

> 文档维护: 每次重大设计决策更新时同步  
> 配套: [架构文档](./yaxiio-architecture-v2.md) | [外部调用手册](./yaxiio-mcp-manual.md)
