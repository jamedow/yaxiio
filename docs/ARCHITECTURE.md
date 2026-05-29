> Version: 2.0 | Date: 2026-05-29 | 五层 MCP 架构 + Agent System v2

## 目录

1. [问题与方案](#问题与方案)
2. [五层架构全景图](#五层架构全景图)
3. [Template Clone 机制](#模板克隆机制)
4. [Bash 工具执行](#bash-工具执行的考量)
5. [宪法框架](#宪法框架)
6. [评分系统](#评分系统)
7. [Agent 系统设计](#agent系统设计)
8. [架构演进记录](#架构演进记录)

---

## 五层架构全景图

```
                          ┌──────────────────────────┐
                          │      Commander (L3)       │
                          │  宪法审查 + 任务路由 + DAG  │
                          └──────────┬───────────────┘
                                     │
       ┌─────────────┬───────────────┼───────────────┬─────────────┐
       ▼             ▼               ▼               ▼             ▼
  ┌─────────┐  ┌─────────┐    ┌─────────┐    ┌─────────┐   ┌─────────┐
  │L1 感知  │  │L2 规划  │    │L3 调度  │    │L4 执行  │   │L5 进化  │
  │ 意图识别│→│ 任务拆解 │→──→│ 并行编排│→──→│ Agent池 │→─→│ 评分优化 │
  │ :3401   │  │ :3402   │    │ :3403   │    │ :3404   │   │ :3405   │
  └─────────┘  └─────────┘    └─────────┘    └────┬────┘   └─────────┘
                                                  │
          ┌───────────────────────────────────────┼───────────────────┐
          │                                       │                   │
     ┌────▼────┐                           ┌──────▼──────┐    ┌──────▼──────┐
     │ 翻译官   │                           │  审计官      │    │  系统医生   │
     │ (Core)  │                           │ (Strategic)  │    │ (Strategic) │
     └─────────┘                           └─────────────┘    └─────────────┘
          │                                       │                   │
     ┌────▼────┐                           ┌──────▼──────┐    ┌──────▼──────┐
     │LM工程师  │                           │  前端工程师   │    │  SEO分析师  │
     │(Utility)│                           │  (Utility)   │    │ (Strategic) │
     └─────────┘                           └─────────────┘    └─────────────┘

┌──────────────────────────────────────────────────────────────────┐
│              双层守护 (Guardian)                                  │
│  PM2 → Guard-Primary + Guard-Secondary → Commander               │
│  故障诊断 → 自动修复 → 速率限制 → Leader选举                        │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│              L0 记忆层                                            │
│  工作记忆(Redis Session) → 短期经验(L0库) → 长期知识(Web缓存)       │
└──────────────────────────────────────────────────────────────────┘
```

---

# Yaxiio Architecture Design

## 五层MCP架构
## English Design

### Problem & Solution

Most AI agent frameworks suffer from three fundamental issues:

| Problem | Yaxiio Solution |
|---------|----------------|
| **Context Inflation**: Agents accumulate stale history across tasks | **Template Clone**: Fresh agent per task, destroyed after completion |
| **Orchestrator Overreach**: Commander directly executes business logic | **Constitution**: Commander only routes and schedules |
| **Static Quality**: No systematic feedback after task completion | **Self-Check Loop**: L5 scores → gap analysis → auto-retry |

### Why Five Layers?

Each layer is an independent MCP server communicating via HTTP/JSON-RPC:

```
Perception → Planning → Coordination → Execution → Evolution
  (What?)     (How?)     (Who?)        (Do it!)    (How good?)
```

This separation enables independent development, testing, and scaling. Failures in one layer do not cascade.

### Template Clone

Traditional approach — memory accumulates across tasks:
```
Agent Memory = accumulate(task_1, task_2, task_3, ...)  // Grows unbounded
```

Yaxiio approach — fresh instances, template evolution:
```
Template → clone_for(task_1) → execute → destroy
Template → clone_for(task_2) → execute → destroy
// Template improves via L5 evolution feedback
```

### Why Bash-Based Tool Execution?

Agents execute tools via bash commands extracted from LLM output. This is intentionally primitive:

```
LLM: ```bash python3 tools/mongo_query.py --industry power```
     → Command extracted → Executed → Output fed back to LLM → Analysis
```

Benefits: zero additional API surface, plain Python scripts, natural sandboxing.

### Constitution Framework

```
Before any action: constitution.review(action, payload)
  → ALLOWED:   System management (bypass pipeline)
  → DELEGATED: Business operations (must go L1→L5)
  → REJECTED:  Dangerous patterns (blocked)
  → DEGRADED:  High-risk operations (forced sandbox)
```

---

## Chinese Design

### 问题与方案

主流 AI 智能体框架普遍存在三个根本问题：

| 问题 | Yaxiio 方案 |
|------|------------|
| **上下文膨胀**: 智能体跨任务积累陈旧历史 | **模板克隆**: 每次任务创建全新实例，完成后销毁 |
| **编排器越权**: Commander 直接执行业务逻辑 | **宪法审查**: Commander 只负责路由和调度 |
| **静态质量**: 任务完成后无系统反馈 | **自检循环**: L5 评分→差距分析→自动重试 |

### 为什么是五层？

每层都是独立的 MCP 服务器，通过 HTTP/JSON-RPC 通信：

```
感知 → 规划 → 协调 → 执行 → 进化
(是什么?) (怎么做?) (谁来?) (执行!) (多好?)
```

分层设计使各层可独立开发、测试、扩展。单层故障不会级联扩散。

### 模板克隆机制

传统方式——记忆跨任务累积膨胀：
```
智能体记忆 = 累积(任务1, 任务2, 任务3, ...)  // 无限增长
```

Yaxiio 方式——新鲜实例，模板进化：
```
模板 → 克隆用于任务1 → 执行 → 销毁
模板 → 克隆用于任务2 → 执行 → 销毁
// 模板通过 L5 进化反馈持续改进
```

### Bash 工具执行的考量

智能体通过 LLM 输出的 bash 命令调用工具。刻意保持简约：

```
LLM输出: ```bash python3 tools/mongo_query.py --industry power```
       → 提取命令 → 执行 → 结果回喂 LLM → 分析输出
```

优势：零额外 API、纯 Python 脚本、天然沙箱隔离。

### 宪法框架

```
每次操作前: constitution.review(action, payload)
  → ALLOWED:   系统管理操作（可绕过流水线）
  → DELEGATED: 业务操作（必须走 L1→L5）
  → REJECTED:  危险模式（拦截）
  → DEGRADED:  高风险操作（强制沙箱）
```

---

## Scoring System / 评分系统

| Dimension / 维度 | Description / 说明 |
|------------------|-------------------|
| Accuracy / 准确性 | Factual correctness, terminology / 事实正确性、术语准确度 |
| Completeness / 完整性 | Coverage of all requirements / 任务需求覆盖度 |
| Professionalism / 专业性 | Industry-standard quality / 行业专业水准 |
| Actionability / 可操作性 | Can output be used directly? / 产出是否可直接使用 |
| Consistency / 一致性 | Alignment with conventions / 与系统规范一致 |

Self-check loop: score < 7 → gap analysis → auto-retry (max 3 rounds).  
自检循环：评分 < 7 → 差距分析 → 自动重试（最多 3 轮）。

---

© 2026 Yaxiio Contributors. AGPLv3.

## Agent系统设计
# Yaxiio Agent 系统完整设计方案 v2.0

## 目录

1. [Agent 定义：标准化容器 + 能力卡片](#1-agent-定义)
2. [能力卡片系统](#2-能力卡片系统)
3. [AgentFactory：创建流水线](#3-agentfactory)
4. [三层适配器](#4-三层适配器)
5. [Commander 定位：流程作者](#5-commander-定位)
6. [人类评分系统](#6-人类评分系统)
7. [Agent 生命周期管理](#7-agent-生命周期管理)
8. [实施路线图](#8-实施路线图)

---

## 1. Agent 定义

```
Agent = 固定接口（MCP/Redis Pub/Sub）
      + 角色提示词（System Prompt）
      + 状态机（空闲→执行→故障→恢复→销毁）
      + 上下文空间（独立 Redis Session）
      + 独立进程（PM2 管理的 PID）
```

Agent 之间的差异只在于**能力卡片的内容不同**。壳是统一的——同一个 `neuron.py` 容器，套不同的配置就变成不同的 Agent。

---

## 2. 能力卡片系统

### 2.1 标准模板

```yaml
# capability-card.yaml — AgentFactory 标准配置

# ========== 1. 身份信息 ==========
name: "审计官"
role: "内容质量审计专家"
quadrant: "strategic"          # core | strategic | utility | ephemeral
version: "2.1.0"

# ========== 2. 大脑配置 ==========
model: "deepseek-chat"         # 推荐模型
thinking: "medium"             # off | low | medium | high
temperature: 0.3
max_tokens: 2000
system_prompt: |
  你是严谨的内容质量审计专家。
  职责：检查术语一致性、参数准确性、格式规范性。
  原则：不猜测，只基于给定标准判断。
  输出：明确指出错误位置、错误内容和正确标准。

# ========== 3. 工具箱 ==========
skills:
  - audit-engine
mcp_servers:
  - name: mongodb
    mode: read_only
    collections: ["page_content"]
tools:
  - mongo_query
  - redis_query
  - multilang_audit

# ========== 4. 接口定义 ==========
input_schema:
  type: object
  required: ["content"]
  properties:
    content:    { type: string, description: "待审计的文本内容" }
    standard:   { type: string, description: "审计标准文件ID或内容" }
    target:     { type: string, enum: ["all", "power", "mining", "agriculture", "industrial", "municipal"] }

output_schema:
  type: object
  required: ["score", "issues"]
  properties:
    score:       { type: number, minimum: 0, maximum: 10 }
    issues:      { type: array, items: { type: object } }
    summary:     { type: string }
    suggestions: { type: array }

# ========== 5. 状态机 ==========
lifecycle:
  init_timeout: 30s
  task_timeout: 300s
  max_retries: 3
  retry_backoff: "exponential"   # exponential | linear | fixed
  idle_timeout: 600s             # Strategic Agent 闲置超时后休眠
  heartbeat_interval: 30s

# ========== 6. 资源限制 ==========
resource_limits:
  max_memory: 256MB
  max_concurrent_tasks: 1

# ========== 7. 人类评价维度（可选）==========
human_review_dimensions:
  - accuracy           # 准确性：审计结论是否正确
  - completeness       # 完整性：是否覆盖所有检查项
  - actionability      # 可操作性：建议是否可直接执行
  - clarity            # 清晰度：报告是否易于理解
```

### 2.2 能力注册表

所有 Agent 的能力卡片集中注册在 Redis：

```
agent:card:审计官          → 完整 YAML
agent:card:翻译官          → 完整 YAML
agent:card:LM内容工程师    → 完整 YAML
...
agent:registry             → ["审计官", "翻译官", ...]  # Agent 列表
agent:quadrant:core        → ["翻译官", "售前经理"]      # 按象限索引
agent:quadrant:strategic   → ["审计官", "品牌策略师"]
agent:quadrant:utility     → ["UI/UX设计师", "前端工程师"]
agent:quadrant:ephemeral   → []                          # 用完即弃，不持久化
```

### 2.3 三问法快速设计

给定一个新角色需求，回答三个问题即可完成能力卡片：

| 问题 | 对应区块 | 示例（SEO分析师） |
|------|---------|------------------|
| **你是谁？** | name, role, system_prompt | "SEO分析师"，"网站SEO诊断与优化专家" |
| **你会用什么工具？** | skills, mcp_servers, tools | seo-engineer, browser_harness |
| **你怎么活？** | lifecycle, resource_limits | quadrant: strategic, task_timeout: 600s |

---

## 3. AgentFactory

### 3.1 创建流水线

```
需求到达
    │
    ▼
① 查找能力注册表 → 找到匹配的能力卡片
    │                   │ 未找到
    │                   ▼
    │              L5 分析需求 → 自动生成能力卡片 → 人工审核
    │
    ▼
② 创建隔离工作目录
    /tmp/yaxiio-agent/{agent_name}-{task_id}/
    │
    ▼
③ 写入 agent.json（从能力卡片生成）
    │
    ▼
④ 启动 PM2 进程 → 加载通用 Agent 容器（neuron.py）
    │
    ▼
⑤ 进程初始化：
    ├─ 读取 agent.json
    ├─ 加载 system_prompt
    ├─ 挂载 Skill
    ├─ 初始化状态机（空闲）
    └─ 向 HeartbeatManager 注册心跳
    │
    ▼
⑥ Agent 就绪 → Redis Pub/Sub 频道订阅
```

### 3.2 代码实现

```python
# AgentFactory 核心方法
class AgentFactory:
    def create(self, name: str, task_id: str, overrides: dict = None) -> str:
        """从能力卡片创建 Agent 实例"""
        # 1. 查找能力卡片
        card = self.registry.get(name)
        if not card:
            card = self._generate_card(name)  # L5 自动生成
        
        # 2. 应用覆盖（如 task_id 特定的配置）
        config = deep_merge(card, overrides or {})
        config["task_id"] = task_id
        
        # 3. 写入 agent.json
        work_dir = f"/tmp/yaxiio-agent/{name}-{task_id}"
        os.makedirs(work_dir, exist_ok=True)
        with open(f"{work_dir}/agent.json", "w") as f:
            json.dump(config, f)
        
        # 4. 启动进程
        env = {
            "AGENT_NAME": name,
            "AGENT_CONFIG": f"{work_dir}/agent.json",
            "TASK_ID": task_id,
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PASSWORD": os.environ["REDIS_PASSWORD"],
            "DEEPSEEK_API_KEY": os.environ["DEEPSEEK_API_KEY"],
        }
        proc = subprocess.Popen(
            [sys.executable, "/opt/commander/neuron.py"],
            env={**os.environ, **env},
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        
        # 5. 等待就绪信号
        self._wait_ready(name, task_id, timeout=30)
        return f"{name}-{task_id}"
    
    def destroy(self, agent_instance_id: str):
        """安全销毁 Agent 实例"""
        name, task_id = agent_instance_id.split("-", 1)
        # 1. 发送 shutdown 信号
        redis.publish(f"lightingmetal:agent:{name}", 
                      json.dumps({"type": "shutdown", "task_id": task_id}))
        # 2. 等待优雅退出
        time.sleep(3)
        # 3. 清理资源
        redis.delete(f"agent:{name}:{task_id}:memory")
        redis.delete(f"agent:{name}:{task_id}:heartbeat")
        shutil.rmtree(f"/tmp/yaxiio-agent/{agent_instance_id}", ignore_errors=True)
```

---

## 4. 三层适配器

不同 Agent 的输入输出格式不同。Yaxiio 不新增中间件，而是拆分到三个已有组件协作：

### 架构

```
Agent A 输出 ──→ 工作流快照 ──→ 字段映射 ──→ Schema 校验 ──→ Agent B 输入
                  (L3存储)     (L3拆解器)    (L2能力卡片)
```

### 4.1 第一层：能力卡片 Schema（格式定义）

```yaml
# 翻译官 input_schema
input_schema:
  type: object
  properties:
    text: { type: string }
    target_language: { type: string, enum: ["ar", "en", "ru", "es"] }
```

Commander 不需要知道"翻译"的业务细节，只知道这个 Agent 吃什么格式、拉什么格式。

### 4.2 第二层：任务拆解器字段映射（动态生成）

```python
# TaskDecomposer 生成的子任务依赖映射
subtask_mapping = {
    "task_3": {
        "depends_on": "task_1",
        "input_transform": {
            "text": "task_1.output.quotation_text",       # 字段映射
            "target_language": "task_2.output.market.language"
        }
    }
}
```

### 4.3 第三层：工作流快照（数据中转）

```python
class WorkflowScheduler:
    def dispatch_to_agent(self, subtask, target_agent):
        # 1. 从快照读取上游数据
        upstream = self.snapshot.get(subtask.depends_on)
        
        # 2. 根据映射关系提取字段
        raw = self.apply_mapping(upstream, subtask.input_transform)
        
        # 3. 校验是否符合目标 Agent 的 input_schema
        validated = self.validate_schema(raw, target_agent.card.input_schema)
        
        # 4. 分发给目标 Agent
        redis.publish(target_agent.task_channel, validated)
```

---

## 5. Commander 定位

### 5.1 Commander vs 传统流程引擎

| 维度 | 传统引擎 (Jenkins/Airflow) | Yaxiio Commander |
|------|--------------------------|-----------------|
| 流程来源 | 人预先画 DAG 图 | Commander 自己设计 DAG 图 |
| 新任务处理 | 找不到流程→报错 | Commander 自己拆解新任务 |
| 流程优化 | 人手动调整 | Commander 发现瓶颈，重组流程 |
| 异常处理 | 预设规则重试/跳过 | Commander 判断该重试还是换方案 |
| Agent 不够 | 人手动扩容 | Commander 创建新 Agent |

**核心差异**：人在回路后面。Commander 自己想、自己执行，人审核结果。

### 5.2 创建/销毁 Agent 的决策权

| 触发条件 | 决策主体 | 依据 |
|---------|---------|------|
| 任务需要，现有 Agent 类型不匹配 | Commander (L3) | LLM 分析需求 vs 能力注册表 |
| Agent 全部忙碌，队列深度 > 阈值 | Commander + AutoScaler | 队列深度 → 计算临时实例数 |
| L5 建议沉淀为新类型 | L5 进化层 | 历史数据 → 频繁出现的 Ephemeral 值得提升 |
| 人类主动要求 | 你 | 直接告诉雅溪需要什么新 Agent |

### 5.3 Commander 的智能化

```python
# Commander 的能力边界
class Commander:
    CAN_DO = [
        "理解模糊意图 → 设计工作流",        # LLM
        "发现瓶颈 → 动态调整拓扑",           # L3 调度
        "Agent 不够 → 调用 AgentFactory",    # 决策（非执行）
        "任务完成 → 总结经验 → 更新经验库",  # L0/L5
        "人类打分 → 纳入评分 → 调整权重",    # HybridScorer
    ]
    
    CANNOT_DO = [
        "直接创建 Agent 进程",    # → AgentFactory 执行
        "绕过宪法执行操作",       # → Constitution 拦截
        "私自销毁 Core Agent",    # → 四象限规则限制
    ]
```

---

## 6. 人类评分系统

### 6.1 评分卡结构

```yaml
human_review:
  task_id: "迪拜市场EPDM垫片报价-20260528-001"
  reviewer: "human"
  reviewer_id: "jamedow"         # 评价者身份 → 计算信用分
  scores:
    accuracy: 8/10
    completeness: 7/10
    professionalism: 9/10
    timeliness: 6/10
  overall: 7.5
  comment: "报价准确，回复速度偏慢，建议优化翻译环节"
  reviewed_at: "2026-05-28T14:30:00Z"
  weight: 0.85                   # 评价者信用分（基于历史评价质量）
```

### 6.2 不同任务类型的评价维度

| 任务类型 | 评价维度 |
|---------|---------|
| 翻译任务 | 准确性、术语一致性、流畅度、交付速度 |
| 报价任务 | 准确性、完整性、专业性、时效性、价格竞争力 |
| 审计任务 | 检出率、误报率、报告清晰度、建议可行性 |
| SEO优化 | 关键词匹配度、标题吸引力、数据支撑度 |
| 网站设计 | 视觉美观度、品牌一致性、转化引导性、移动端适配 |

### 6.3 混合评分器

```python
class HybridScorer:
    """AI 评分 + 人类评分，加权融合"""
    
    HUMAN_WEIGHT = 0.7   # 人类评分权重（宪法可配置）
    AI_WEIGHT = 0.3      # AI 评分权重
    ANOMALY_THRESHOLD = 3  # 人机分差超过此值触发审查
    
    def calculate(self, task_result, task_id):
        ai_score = self.ai_scorer.evaluate(task_result)
        human_review = self.get_human_review(task_id)
        
        if human_review is None:
            return {"score": ai_score, "source": "ai_only"}
        
        final = human_review["overall"] * self.HUMAN_WEIGHT + ai_score * self.AI_WEIGHT
        
        # 人机分差异常 → 触发审查
        if abs(human_review["overall"] - ai_score) > self.ANOMALY_THRESHOLD:
            self.trigger_anomaly_review(task_result, ai_score, human_review)
        
        # 更新评价者信用分
        self.update_reviewer_credit(human_review["reviewer_id"], 
                                     human_review["overall"], ai_score)
        
        return {"score": final, "source": "hybrid", 
                "ai": ai_score, "human": human_review["overall"]}
```

### 6.4 评价者信用分

```python
def update_reviewer_credit(reviewer_id, human_score, ai_score):
    """评价者信用分：基于评分一致性、频率、历史"""
    profile = redis.hgetall(f"reviewer:{reviewer_id}:profile")
    
    consistency = 1.0 - abs(human_score - ai_score) / 10.0  # 越接近AI评分越可信
    frequency = min(1.0, int(profile.get("review_count", 0)) / 20)  # 评多了更可信
    history = float(profile.get("credit", 0.8))
    
    new_credit = history * 0.7 + (consistency * 0.5 + frequency * 0.5) * 0.3
    redis.hset(f"reviewer:{reviewer_id}:profile", "credit", new_credit)
```

---

## 7. Agent 生命周期管理

### 7.1 四象限规则

| 象限 | 特征 | 默认策略 | 评分的影响 |
|------|------|---------|-----------|
| **Core** | 核心业务，永不销毁 | 只重建，不销毁 | 连续 10 次 <7 → Prompt 优化 → 仍 <7 → 人工审核 |
| **Strategic** | 按需创建，闲置回收 | 闲置超时后休眠 | 均分 ≥8 → 延长闲置；<6 → 加速淘汰 |
| **Utility** | 长期驻留 | 异常 >20% 才重建 | 评分不作为销毁依据 |
| **Ephemeral** | 用完即弃 | 任务完成即销毁 | 评分仅用于 L5 进化 |

### 7.2 Agent 状态机

```
IDLE ──→ EXECUTING ──→ COMPLETED ──→ (Ephemeral) DESTROYED
  │          │              │
  │          ├──→ FAULT ──→ RECOVERING ──→ IDLE (retry)
  │          │                   │
  │          │                   └──→ FAULT (max_retries exceeded)
  │          │
  │          └──→ TIMEOUT ──→ RECOVERING
  │
  └──→ (idle_timeout exceeded + Strategic) HIBERNATING
```

### 7.3 销毁决策矩阵

```python
def should_destroy(agent, scores_history, human_reviews):
    quadrant = agent.card["quadrant"]
    
    rules = {
        "core": lambda: False,  # 永不销毁
        "strategic": lambda: (
            agent.idle_time > agent.card["lifecycle"]["idle_timeout"]
            and mean(scores_history[-5:]) < 6
        ),
        "utility": lambda: agent.exception_rate > 0.2,
        "ephemeral": lambda: agent.status == "COMPLETED",
    }
    
    return rules[quadrant]()
```

---

## 8. 实施路线图

### Phase 1：能力卡片落地（当前）

- [ ] 创建 `agent:card:{name}` 标准 YAML 模板（7 个现有 Agent）
- [ ] 实现 `AgentFactory.create()` 从能力卡片创建 Agent
- [ ] agent.json 写入 + neuron.py 读取能力卡片
- [ ] 能力注册表 Redis 索引

### Phase 2：三层适配器

- [ ] 能力卡片 input_schema / output_schema 校验
- [ ] TaskDecomposer 动态字段映射
- [ ] WorkflowSnapshot 中间数据中转

### Phase 3：人类评分系统

- [ ] human_review_card 数据结构
- [ ] HybridScorer（AI + 人类加权）
- [ ] 评价者信用分系统
- [ ] 人机分差异常审查

### Phase 4：Commander 智能化

- [ ] Agent 不够时自动调用 AgentFactory
- [ ] 动态拓扑调整
- [ ] L5 自动生成能力卡片

---

> **设计原则**：Commander 是流程的作者，不是执行者。Agent 是标准化容器 + 能力配置。人类评分是 AI 进化的校准基准。

---

## 附录：Neuron（神经元）与 Agent System 的映射

### 神经元就是标准化容器

`neuron.py` 已经是 Agent System v2 中「标准化容器」的完整实现。

```
能力卡片（YAML）          neuron.py 容器              Agent 实例
─────────────           ─────────────              ──────────
name: "审计官"    ──套入──→  Redis Pub/Sub 接口  ──→  审计官进程
model: deepseek   ──套入──→  LLM 大脑             ──→  使用 deepseek
skills: [...]     ──套入──→  Skill 加载器          ──→  挂载 audit-engine
lifecycle: {...}  ──套入──→  状态机                ──→  超时/重试策略
tools: [...]      ──套入──→  bash 执行器           ──→  mongo_query 可用
```

### 已对齐的设计（✅）

| 设计要素 | neuron.py 实现 |
|---------|---------------|
| 固定接口 | `CHANNEL = f"lightingmetal:agent:{AGENT_NAME}"` |
| LLM 大脑 | `_llm_think()` + `_llm_analyze_results()` |
| 工具调用 | `_extract_commands()` → bash 执行 → 反馈循环 |
| 上下文隔离 | `MEMORY_KEY = f"agent:{AGENT_NAME}:{TASK_ID}:memory"` |
| 心跳上报 | `commander:agent:heartbeat:{name}` (30s 间隔) |
| Skill 加载 | `system_prompt` 从 SKILL.md 文件读取 |
| 工具注册表 | `_load_tool_descriptions()` 注入 LLM 上下文 |

### 需增强的部分（Phase 1 落地）

| 设计要素 | 当前状态 | 目标 |
|---------|---------|------|
| 能力卡片加载 | ❌ 环境变量驱动 | 读取 `agent.json`（从能力卡片生成） |
| 状态机 | ❌ 只有 running=True/False | IDLE→EXECUTING→FAULT→RECOVERING→HIBERNATING |
| Schema 校验 | ❌ 无 | 根据 `input_schema` 校验入参，按 `output_schema` 格式化出参 |
| 优雅关闭 | ❌ 无 | 接收 `shutdown` 消息 → 完成当前任务 → 清理 → 退出 |
| 重试退避 | ❌ 无 | 根据 `retry_backoff` 配置（exponential/linear/fixed） |
| 资源限制 | ❌ 无 | `max_memory` 超限告警，`task_timeout` 超时自杀 |

### 增强方案（改动量 ~100 行）

```python
# neuron.py 启动时读取能力卡片
class Neuron:
    def __init__(self):
        config_path = os.environ.get("AGENT_CONFIG", "")
        if config_path and os.path.exists(config_path):
            with open(config_path) as f:
                self.card = json.load(f)
        else:
            self.card = self._default_card()
        
        # 从能力卡片初始化
        self.name = self.card["name"]
        self.role = self.card["role"]
        self.state = "IDLE"
        self.task_timeout = self.card.get("lifecycle", {}).get("task_timeout", 300)
        self.max_retries = self.card.get("lifecycle", {}).get("max_retries", 3)
        self.retry_count = 0
        # ... LLM配置、Skill加载、心跳启动
    
    def process_task(self, task):
        self.state = "EXECUTING"
        try:
            result = self.think_and_act(task)
            self.state = "IDLE"
            self.retry_count = 0
            return result
        except TimeoutError:
            self.state = "TIMEOUT"
            if self.retry_count < self.max_retries:
                self.retry_count += 1
                self.state = "RECOVERING"
        except Exception:
            self.state = "FAULT"
```

这个改动直接嵌入在 `neuron.py` 的现有 `think_and_act()` 循环里，不破坏现有功能。

---

## 附录 B：评分面板 (Human Review Dashboard)

### 访问地址

```
http://容器IP:3005
```

### 功能

- **任务列表**：最近完成的任务，展示 AI 评分和人类评分状态
- **评分表单**：按 Agent 类型显示不同评价维度（滑块 1-10）
- **异常告警**：AI 与人类评分差距 > 3 的任务自动标记
- **评价者面板**：信用分、累计评价数、最近评价记录
- **自动刷新**：每 30 秒

### API

```
GET  /api/tasks     → 所有任务及评分状态
POST /api/review    → 提交评分 {task_id, reviewer_id, scores: {dim: val}, comment}
GET  /              → 可视化面板
```


---

## 架构演进记录 (v1.1 → v1.7)

### v1.7 — 代码质量防火墙 + 模块化重构

**四道审查防火墙：**

```
AI生成代码 → 代码审查官 → 架构审查官 → 安全审查官 → 测试审查官 → 合入
              可读性        层级归属      OWASP       覆盖率
              健壮性        依赖方向      依赖CVE     边界条件
              性能          接口规范      数据流       Mock
```

**模块化重构：**

```
重构前: workflow_engine.py 1384行 单文件 架构评分 3/10

重构后: 8个模块, 最大1036行
  agent_factory.py           74行  Agent 工厂
  l0_memory.py               80行  L0 记忆层
  mcp_bridge.py              27行  MCP 通信桥
  workflow_snapshot.py        34行  工作流快照
  gap_analyzer.py             56行  差距分析器
  workflow_utils.py           53行  工具函数
  parallel_orchestrator.py   100行  并行调度器
  workflow_engine.py        1036行  核心编排
```

**L3/L4 MCP 协调层完全落地：**

- `_execute_subtask` 改走 `call_layer(4, "dispatch_and_await")` 
- `_poll_for_response` 移除，内置于 L4 MCP Server
- 每个模块独立可测，无需启动完整 Commander

### v1.4-v1.6 — Agent System v2

**Phase 1: 能力卡片 + Neuron 状态机**
- 7→11 张能力卡片，存 Redis `agent:card:{name}`
- 四象限注册表 (`agent:quadrant:{core|strategic|utility}`)
- Neuron 状态机: IDLE→EXECUTING→TIMEOUT→FAULT→RECOVERING

**Phase 2: 三层适配器**
- SchemaValidator: input_schema/output_schema 校验
- WorkflowSnapshot: 跨子任务数据中转
- FieldMapping: 上游数据字段自动映射

**Phase 3: 人类评分系统**
- HybridScorer: AI(30%) + Human(70%) 加权
- 评价者信用分 + 人机分差异常检测
- 评分面板 (port 3005)

**Phase 4: AgentFactory + 自动扩缩**
- 能力卡片驱动的 Agent 创建
- `recommend_agents()` 关键词匹配
- `bg_fix_loop.sh` 自主后台修复

**AnySearch + Browser Harness MCP 集成**
- 3 个 MCP Server 注册: mongodb, anysearch, browser_harness
- 11 个 Agent 按需分配 MCP 工具
- `mcp:registry` Redis Hash 动态管理

### v1.1-v1.3 — 基础架构

- Template Clone: 每任务独立 Agent 实例
- Sandbox Session: clone→execute→evaluate→destroy
- L0 记忆层: 经验存储 + Web 知识缓存
- 数据驱动批量拆解: 提取数字→计算批次→自动分配 Agent
- 验证循环: 每轮结束重新审计，数字不归零不停止

## 项目结构

```
.pi/skills/commander/
├── README.md                 # 项目首页
├── LICENSE                   # AGPLv3
├── yaxiio.py                 # Commander 主程序
├── workflow_engine.py        # 核心编排 (1036行)
├── neuron.py                 # Agent 运行时
├── constitution.py           # 宪法审查
├── config.py                 # 配置中心
│
├── agent_factory.py          # Agent 工厂
├── l0_memory.py              # L0 记忆层
├── mcp_bridge.py             # MCP 通信桥
├── workflow_snapshot.py       # 工作流快照
├── gap_analyzer.py           # 差距分析器
├── workflow_utils.py         # 工具函数
├── parallel_orchestrator.py   # 并行调度器
│
├── layers/                   # L1-L5 MCP 服务器
├── mcp/                      # MCP 协议
├── tools/                    # Agent 工具集
├── docs/                     # 文档中心
└── skills/                   # 其他 Agent Skill 定义
```

---

## 未来方向：Yaxiio Desktop

### 架构

```
┌────────────────────────────────────────┐
│  Tauri / Electron Shell                │
│  ┌──────────────────────────────────┐  │
│  │  React Flow Dashboard (已有)     │  │
│  │  localhost:3004                  │  │
│  └──────────────────────────────────┘  │
│              ↕ HTTP                     │
│  ┌──────────────────────────────────┐  │
│  │  Yaxiio Python Runtime           │  │
│  │  ├─ Commander + 五层MCP          │  │
│  │  ├─ SQLite (yaxiio.db)           │  │
│  │  │   ├─ 能力卡片                  │  │
│  │  │   ├─ 任务队列 (替代Redis Pub)  │  │
│  │  │   ├─ 经验存储                  │  │
│  │  │   └─ 状态机                    │  │
│  │  └─ Agent 运行时 (neuron.py)     │  │
│  └──────────────────────────────────┘  │
│  [系统托盘] [开机自启]                 │
└────────────────────────────────────────┘
```

### 相比服务端的差异

| 组件 | 服务端 | 桌面版 |
|------|--------|--------|
| 数据库 | MongoDB | SQLite（已有） |
| 消息队列 | Redis Pub/Sub | SQLite 轮询（1s） |
| LLM | DeepSeek API | 相同 + 本地模型 |
| 仪表盘 | 3004 端口 | 相同 |
| 部署 | Docker | Tauri 安装包 |

### 性能评估

- Agent 响应延迟：实时 → 1 秒轮询（桌面场景可接受）
- 磁盘：SQLite 读写 < 10MB
- 内存：Python 运行时 ~150MB + Tauri 壳 ~50MB = ~200MB
- 安装包：PyInstaller 打包 Python + Tauri 壳 ≈ 250MB

---

## 愿景：Yaxiio 的未来使用场景

### 1. 个人 AI 操作系统

不是聊天，是在后台默默干活的 AI 管家。

```
09:00  自动扫描全网竞品价格变动 → 更新数据库 → 推送报告
       "昨天有3家对手涨价，2家推出新品，建议调整报价策略"

14:00  检测到日语版17处翻译过期 → 自动修复 → 同步 → 部署
       "日语页面已更新，无需人工操作"

周末   收到客户邮件 → 提取需求 → 查产品库 → 生成报价单 → 发送
       "报价已发送，预计48小时内回复"
```

桌面版开机自启，系统托盘静默运行。人不需要找她，她在帮人赚钱。

### 2. 外贸 AI 工厂

```
客户询盘 → L1意图理解 → L2+L3产品匹配 → L4技术方案 → L5评分 → 部署预览 → 人确认
```

一条询盘从进来到报价发出，人只参与最后一步。其余全由 Agent 团队并行完成。

### 3. 知识库进化引擎

```
Phase 1: Agent 遇障碍 → L5 分析模式 → 生成工具脚本 (已实现)
Phase 2: Agent 遇障碍 → L5 检索互联网 → 学习解决方案 → 生成工具
Phase 3: Agent 遇障碍 → L5 发现行业共性问题 → 生成新 Skill → 发布到市场
```

未来的 Yaxiio 不只"用工具"，而是"发现缺什么就自己造什么"。

### 4. 垂直行业 AI 代理

换一套能力卡片，Yaxiio 从外贸系统变成任何行业系统：

| 行业 | Agent 团队 |
|------|-----------|
| 外贸（已有） | 审计官/翻译官/售前经理/品牌策略师 |
| 法律 | 合同审查官/判例检索官/法规变更监控官 |
| 医疗 | 病历摘要官/药物相互作用检查官/影像预筛官 |
| 教育 | 教案生成官/学生进度追踪官/知识点图谱官 |

壳不变，大脑换。

### 5. 边缘端 IoT 编排

SQLite 版 Yaxiio 仅 200MB，跑在树莓派上：

```
传感器 → L1感知异常 → L2规划诊断 → L3调度Agent → L4执行 → L5评估
```

一条产线的智能调度，不需要云端。

### 6. 开源社区 AI 维护者

```
PR提交 → 代码审查官 → 架构审查官 → 安全审查官 → 测试审查官
         全部通过 → 自动合并
         任一失败 → 自动生成修复建议 → @提交者
```

一个 Yaxiio 实例维护一个开源项目。不是"LGTM"机器人，是真正的代码质量防火墙。

### 7. 多 Yaxiio 联邦

```
你的Yaxiio(外贸) ←→ 他的Yaxiio(物流) ←→ 她的Yaxiio(支付)
       │                    │                   │
   产品匹配              运费计算            汇率转换
       │                    │                   │
       └────────────────────┴───────────────────┘
                           │
                 客户收到完整报价单
```

多个 Yaxiio 通过标准 MCP 协议互联，各自管自己的领域，协作完成复杂任务。五层 MCP 架构不是为了内部通信设计的，是为了万物互联设计的。

---

> **Yaxiio 不是一个外贸工具，是一个通用的 Agent 操作系统内核。**
