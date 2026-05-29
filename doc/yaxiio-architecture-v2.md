# 雅溪 Yaxiio v1.08 — 五层模块化智能调度系统

> 五金外贸B2B智能调度系统 · 2026 AI元年  
> 最后更新: 2026-05-27  
> Docker 镜像: yaxiio:v1.08-final

---

## 版本演进

| 版本 | 日期 | 核心变更 |
|------|------|---------|
| v1.04 | 05-25 | 初始五层架构, Redis Pub/Sub 通信 |
| v1.05 | 05-27 | 并发增强: 有界线程池 + 共享异步事件循环 + 结果回调 |
| v1.06 | 05-27 | 雅溪宪法: Commander 从执行者转型编排者, 白名单准入 |
| v1.07 | 05-27 | 状态机 v2.0: 五层里程碑 + 事件溯源 + 断点恢复 |
| v1.08 | 05-27 | 神经网络 + 模型路由 + 双层互保 + 系统医生 |

---

## 架构全景

```
┌──────────────────────────────────────────────────────────────────────┐
│  PM2 (最外层守护)                                                     │
│  ├─ guardian-primary  (Leader)    — 监控 Commander, 元评分, leader选举 │
│  └─ guardian-secondary (Standby) — 监控 Leader, 接管故障转移          │
├──────────────────────────────────────────────────────────────────────┤
│  Commander (编排核心)                                                 │
│  ├─ 宪法审查 (YaxiioConstitution) — 白名单准入, 业务操作强制走流水线  │
│  ├─ 模型路由 (ModelConfig)        — Agent × Task × Thinking 三级映射  │
│  ├─ Arsenal 工具注册               — 12 个可调用工具                   │
│  ├─ 断点恢复 (_recover_inflight)   — 重启后扫描未完成任务              │
│  └─ 故障处置 (handle_agent_failure)— crash→重启, low_quality→派医生   │
├──────────────────────────────────────────────────────────────────────┤
│  五层 MCP 流水线 (WorkflowEngine v2.1)                                │
│                                                                      │
│  L1 感知    → MCP关键词 + LLM深度理解 + 动作优先覆盖                   │
│  L2 规划    → 意图→Arsenal工具匹配 + 复杂任务LLM拆解为子任务DAG        │
│  L3 调度    → spawn_neuron() + Agent Redis频道分发 + 并行编排          │
│  L4 执行    → 沙箱隔离执行 + Arsenal调用 + 神经元轮询等待              │
│  L5 评估    → 规则基础分 + LLM四维评分 + 低分标记进化                  │
├──────────────────────────────────────────────────────────────────────┤
│  神经网络 (Neuron × N)                                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │翻译官     │ │审计官     │ │UI/UX设计师│ │品牌策略师 │ │前端工程师 │   │
│  │think:low  │ │think:high │ │think:med  │ │think:high │ │think:med  │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────┐ ┌──────────┐                                          │
│  │售前经理   │ │系统医生   │  ← 故障自动修复专家                       │
│  │think:low  │ │think:high │                                          │
│  └──────────┘ └──────────┘                                          │
├──────────────────────────────────────────────────────────────────────┤
│  基础设施 (Redis + MongoDB + PM2)                                     │
│  ├─ Redis: Pub/Sub 消息总线 + 状态存储 + 任务状态机                    │
│  ├─ MongoDB: page_content 业务数据 + 审计日志                          │
│  └─ PM2: 双守护进程管理                                               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## L1 基础设施层

### 容器运行时

| 资源 | 值 |
|------|-----|
| Docker 镜像 | `yaxiio:v1.08-final` |
| 入口脚本 | `/entrypoint.sh` |
| 关键环境变量 | `DEEPSEEK_API_KEY`, `LLM_MODEL`, `REDIS_HOST`, `REDIS_PASSWORD` |

### Redis Key 布局

| Key 前缀 | 用途 |
|---------|------|
| `yaxiio:task:{id}` | 任务状态机 (里程碑 + 子任务 + 时间线) |
| `yaxiio:output:{id}:{sid}` | 子任务完整产出 (独立存储, 7d TTL) |
| `yaxiio:task:active` | 活跃任务索引 (Set) |
| `agent:{name}:memory` | Agent 记忆 (最近 20 条) |
| `agent:{name}:prompt` | Agent 自定义 Prompt |
| `commander:agents:active` | 活跃神经元集合 |
| `commander:agent:heartbeat:{name}` | 神经元心跳时间戳 |
| `yaxiio:constitution:violations` | 宪法违宪记录 (List) |
| `yaxiio:guardian:leader` | 双守护 Leader 选举锁 |
| `yaxiio:guardian:role` | Guardian 角色 (primary/secondary) |
| `yaxiio:model:config:{agent}` | Agent 模型热配置 |
| `yaxiio:config:llm_api_key` | LLM API Key (Redis 兜底) |

### MCP Server 端口

| 层 | 端口 | 工具数 | 核心能力 |
|----|:----:|:------:|---------|
| L1 感知 | 3401 | 3 | analyze_intent, extract_keywords, check_duplicate |
| L2 规划 | 3402 | 3 | decompose_task, select_strategy, list_skills |
| L3 协调 | 3403 | 5 | schedule_agents, get_agent_load, report_crash, scale_check, release_agent |
| L4 执行 | 3404 | 4 | execute_task, agent_start, status_query, sandbox_exec |
| L5 进化 | 3405 | 8 | score_task, generate_skill, record_workflow, evaluate_topologies, optimize_prompt, audit_log, generate_design_enforcement, get_modification_stream |

---

## L2 智能体层

### 神经元 (Neuron)

每个 Agent 是一个独立的 Python 进程 (`neuron.py`), 拥有完整回路:

```
SUBSCRIBE → RECEIVE → LOAD_SKILL → LLM_THINK → EXECUTE → RESPOND → LEARN
```

**模型路由表** (三级优先级: payload覆盖 > Redis热配置 > Agent默认)

| Agent | 模型 | Thinking | Skill |
|------|------|:--:|------|
| 翻译官 | deepseek-chat | low | translate-engine |
| 审计官 | deepseek-chat | high | audit-engine |
| UI/UX设计师 | deepseek-chat | medium | ui-ux-designer |
| 品牌策略师 | deepseek-chat | high | strategic-partner |
| 前端工程师 | deepseek-chat | medium | infrastructure-engineer |
| 售前经理 | deepseek-chat | low | product-search |
| 商务经理 | deepseek-chat | low | product-search |
| 系统医生 | deepseek-chat | high | system-doctor |
| Commander | deepseek-chat | task-dependent | — |

### 神经元生命周期

```
spawn_neuron(name, skill)
  → ModelConfig 自动选择模型/thinking
  → subprocess.Popen 启动 neuron.py
  → neuron.py 连接 Redis + LLM + 加载 Skill
  → 订阅 lightingmetal:agent:{name}
  → 注册心跳到 commander:agents:active
  → 处理任务 → 更新记忆 → 发布回复
```

---

## L3 工作流层

### 任务编排流程

```
handle_task(data)
  → 宪法审查 (YaxiioConstitution.review)
    ├─ ALLOWED   → 系统白名单直通 (agent_export/import, session_end, status)
    ├─ DELEGATED → 强制走 L1→L5 流水线
    ├─ REJECTED  → 违宪拒绝
    └─ DEGRADED  → 高危操作降级 sandbox

流水线内:
  process()
    ├─ 简单任务 → _process_simple() → L1→L2→L3→L4→L5 顺序执行
    └─ 复杂任务 → _process_complex()
         ├─ L1: intent 识别
         ├─ L2: 模板拆解 / LLM拆解 → 7 子任务 DAG
         ├─ L3: spawn_neuron × N (并行发射无依赖子任务)
         ├─ L4: 轮询等待 → 收集结果 → _check_and_heal()
         └─ L5: 整体评分
```

### 子任务依赖编排

```
redesign 模板 (7 子任务):
  s1(启发式评估) ─┐
  s2(竞品分析)   ─┼─→ s4(首页布局) ─┐
  s3(品牌转译)   ─┘                ├─→ s6(移动端) ─┐
                   s5(坦克页) ─────┘               ├─→ s7(规范输出)
                                                    ┘
  并行组: {s1,s2,s3} → {s4,s5} → s6 → s7
```

---

## L4 评估层

### 任务状态机 (TaskStateMachine v2.0)

```
yaxiio:task:{id}:
{
  status: "EXECUTING",
  current_layer: "L4_execution",
  progress_pct: 66,
  milestones: {
    L1_perception:  {status: "done", completed_at: ...},
    L2_planning:    {status: "done", ...},
    L3_dispatch:   {status: "done", ...},
    L4_execution:   {status: "running", ...},
    L5_evaluation:  {status: "pending"}
  },
  subtasks: {
    s1: {agent:"UI/UX设计师", status:"DONE", duration_ms:14004, output_hash:"f80a..."},
    s2: {agent:"品牌策略师", status:"DONE", duration_ms:24000, output_hash:"2538..."}
  },
  timeline: [
    {ts:..., event:"TASK_CREATED"},
    {ts:..., event:"SUBTASK_START", detail:"s1 agent=UI/UX设计师"},
    {ts:..., event:"SUBTASK_DONE", detail:"s1 ✅ 14004ms"}
  ]
}
```

### 故障处置闭环

| 故障类型 | 检测 | 动作 |
|---------|------|------|
| crash | 心跳丢失 > 60s | Commander 直接重启 spawn_neuron |
| low_quality | 连续 L5 < 5 | Commander 派系统医生 → 诊断 Prompt → A/B 测试 → 选优 |
| slow_response | 平均 > 60s | 系统医生检查模型配置 |
| prompt_drift | 一致性低 | 系统医生回滚 Prompt |

### Commander 元评分 (Guardian → Commander)

Guardian 每 10 周期对 Commander 的调度质量打分:

- 采样最近任务 L5 评分 → 计算平均
- 连续低分 → 渐进式修复 (建议 → 改温度 → 回滚)

---

## L5 进化层

### Prompt 优化 (GEPA)

- 触发: Agent 连续 3 次 L5 < 5
- 流程: 读记忆 → LLM分析 → 生成 2-3 候选 → A/B测试 → 选优
- 执行者: 系统医生 (非 Commander 自己)

### 工作流拓扑优化

- `_check_and_heal()`: 同 Agent 连续失败 ≥ 2 → 触发医生
- `evolution_queue()`: 低分子任务队列

---

## 关键文件清单

| 文件 | 行数 | 说明 |
|------|:--:|------|
| `yaxiio.py` | ~850 | Commander 核心: 宪法 + 模型路由 + 神经元管理 + 故障处置 |
| `workflow_engine.py` | ~550 | 五层流程编排: 简单/复杂任务 + 并行子任务 + 状态机集成 |
| `task_state_machine.py` | ~350 | 任务状态机: 里程碑 + 子任务 + 时间线 + 恢复 |
| `neuron.py` | ~380 | Agent 运行时: Redis订阅 + LLM思考 + Skill加载 + 记忆 |
| `constitution.py` | ~180 | 雅溪宪法: 白名单/违禁/高危审查 |
| `model_router_v2.py` | ~180 | 模型路由: Agent×Task×Thinking 三级映射 |
| `pi_guardian_v3.py` | ~780 | 双守护: CommanderScorer + DualGuard + leader选举 |
| `agent_lifecycle_v2.py` | ~1650 | LLMAdapter + 生命周期管理 + 四象限 |
| `SKILL.md` × 13 | — | 各 Agent Skill (含 system-doctor) |
| `dashboard_v2.py` | ~920 | Web Dashboard (端口 3003) |

---

## 部署拓扑

```
新加坡服务器 (47.79.20.2)
├── Docker: yaxiio:v1.08-final
│   ├── PM2
│   │   └── guardian-primary (Leader)
│   ├── guardian-secondary (Standby, nohup)
│   ├── Commander (yaxiio.py)
│   │   └── 订阅 yaxiio:agent:commander
│   ├── MCP Servers (L1-L5, 端口 3401-3405)
│   ├── Dashboard (端口 3003)
│   ├── Redis (端口 6379, 内部)
│   └── MongoDB (端口 27017, 内部)
│
└── Nginx → yaxiio.lightingmetal.com
    ├── / → Dashboard:3003
    └── /ws → WebSocket:3398
```

---

## 外部调用

```bash
# 任务派发
redis-cli PUBLISH yaxiio:agent:commander \
  '{"type":"task","taskId":"...","from":"api","to":"commander",
    "replyTo":"...","payload":{"action":"redesign","task":"..."}}'

# 查询状态
redis-cli GET yaxiio:task:{task_id}

# Dashboard
curl http://yaxiio.lightingmetal.com/api/dashboard/realtime

# 热更新模型配置
redis-cli SETEX "yaxiio:model:config:翻译官" 86400 \
  '{"default":{"model":"deepseek-v4-flash","thinking":"off"}}'
```

---

> 文档维护: 每次 Yaxiio 版本升级时同步更新  
> 主仓库: codeup.aliyun.com/AI/commander.git (`ai` remote)
