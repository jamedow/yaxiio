# 雅溪 Yaxiio 记忆系统详解

> Version: 1.0 | Date: 2026-05-29
> 三层记忆：工作记忆（Redis Session）→ 短期经验（L0 经验库）→ 长期知识（Web 缓存 + 术语表）

---

## 一、核心矛盾与解法

AI Agent 面临一个根本矛盾：

| 需求 | 矛盾 |
|------|------|
| 每个任务需要独立的上下文 | 避免历史污染 |
| 好的经验应该被继承 | 避免重复犯错 |

Yaxiio 的解法：**用完就忘，但把好东西存起来**。

---

## 二、三层记忆架构

```
┌─────────────────────────────────────────────────┐
│                                                 │
│  ① 工作记忆 (Working Memory)                     │
│     Redis Key: agent:{name}:{task_id}:memory    │
│     生命周期: 与任务同生同灭                       │
│     内容: LLM 对话历史 + 工具执行结果               │
│     容量: 最近 20 条                              │
│                                                 │
│  ┌──────────────────────┐                       │
│  │  ② 短期经验 (L0 Experience)  │                 │
│  │  Redis Key: exp:{intent}:{agent}              │
│  │  生命周期: 50 条/意图 (FIFO)  │                 │
│  │  内容: 任务摘要 + 评分 + Agent 组合             │
│  │  用途: 同类任务规划时参考        │               │
│  │                 │                             │
│  │  ┌──────────────────────┐   │                 │
│  │  │  ③ 长期知识 (Web Cache) │   │               │
│  │  │  Redis Key: web:{intent}:{concept}         │
│  │  │  生命周期: 按领域 TTL (7d-365d)             │
│  │  │  内容: Web 搜索结果 + 行业知识               │
│  │  │  用途: 填补任务知识缺口     │   │             │
│  │  └──────────────────────┘   │                 │
│  └──────────────────────┘                       │
│                                                 │
└─────────────────────────────────────────────────┘
```

---

## 三、工作记忆（第一层）

### 3.1 设计

```python
# neuron.py
MEMORY_KEY = f"agent:{AGENT_NAME}:{TASK_ID}:memory"
```

每个 Agent 每任务一份独立记忆空间。任务完成后销毁。

### 3.2 内容

```python
self.memory.append({
    "ts": time.time(),
    "task_id": task_id,
    "action": action,
    "summary": str(final_thought)[:300],  # LLM 最终分析结果摘要
})
```

### 3.3 隔离机制

```
Agent "审计官" 处理 任务A:
  memory key = agent:审计官:task-A:memory

Agent "审计官" 处理 任务B:
  memory key = agent:审计官:task-B:memory

→ 两个任务完全隔离，互不干扰
```

### 3.4 生命周期

```
任务开始 → 创建 memory key (空列表)
LLM 思考 → 追加对话记录
工具执行 → 追加执行结果
任务完成 → L0 提取经验
         → memory key 删除 (destroy)
```

---

## 四、短期经验（第二层：L0 经验库）

### 4.1 设计

```python
# l0_memory.py
class L0Memory:
    def _save_experience(self, task_id, subtasks, final_score, agents_used, r):
        intent = self._current_intent or "general"
        for agent in agents_used:
            exp = {
                "task_id": task_id,
                "agent": agent,
                "intent": intent,
                "score": final_score,
                "subtask_count": len(subtasks),
                "success": final_score >= 7,
                "agents_involved": list(agents_used),
                "subtask_actions": [s.get("action", "")[:60] for s in subtasks[:5]],
            }
            key = f"exp:{intent}:{agent}"
            r.lpush(key, json.dumps(exp))
            r.ltrim(key, 0, 49)  # 保留最近 50 条
```

### 4.2 存储结构

```
exp:audit:审计官        → List ["{task_001...}", "{task_002...}", ...] (最多 50 条)
exp:translate:翻译官    → List [...]
exp:audit:all           → List [所有 audit 意图的经验] (agent-agnostic)
exp:translate:all       → List [...]
```

### 4.3 检索时机

```python
# workflow_engine._decompose_via_l2()
past_exp = self.l0._retrieve_experiences(action_clean, available[:5])
if past_exp:
    print(f"[L0] {task_id} found {len(past_exp)} past experiences")
```

在 L2 规划阶段，系统检索该意图的历史经验，作为 LLM 拆解任务的参考上下文。

### 4.4 经验内容

```json
{
  "task_id": "audit-2026-001",
  "agent": "审计官",
  "intent": "audit",
  "score": 8.5,
  "subtask_count": 4,
  "success": true,
  "agents_involved": ["审计官", "翻译官"],
  "subtask_actions": ["审计 power 行业", "审计 mining 行业", ...]
}
```

---

## 五、长期知识（第三层：Web 缓存）

### 5.1 触发条件

```python
# l0_memory.py
def _should_search_web(self, l5_result, intent):
    knowledge_gap = any(kw in issues_str for kw in
        ["knowledge", "data", "unknown", "not found",
         "缺少", "未知", "没有", "不确定"])
    no_internal = internal_count == 0
    can_improve = score < 5 and verdict in ("retry", "reject")

    if (knowledge_gap or no_internal) and can_improve:
        return {"should_search": True, ...}
```

**触发逻辑**：L5 评分低 + 问题涉及"知识不足" + 本地无经验 → 触发 Web 搜索。

### 5.2 存储结构

```python
def _save_web_knowledge(self, intent, concept, facts, domain):
    ttl = {
        "standard": 86400 * 180,   # 标准: 180 天
        "price": 86400 * 7,        # 价格: 7 天 (快速过期)
        "tech_spec": 86400 * 90,   # 技术规格: 90 天
        "regulation": 86400 * 365, # 法规: 365 天
    }.get(domain, 86400 * 30)

    r.setex(f"web:{intent}:{concept[:40]}", ttl, json.dumps(entry))
```

### 5.3 使用场景

```
任务: 翻译 100 条工业产品描述到阿拉伯语
L5 评分: 4 分
问题: "术语不一致，缺少阿拉伯语电力行业术语参考"

L0 检测到 knowledge_gap
  → call_layer(5, "web_research", topic="阿拉伯语电力行业术语标准")
  → 搜索结果存入 web:translate:arabic_power_terms
  → 下次翻译任务自动参考此术语表
  → TTL 180 天（standard 领域）
```

---

## 六、记忆恢复五阶段流程

Agent 启动时，不是从零开始，而是一步步恢复记忆：

```
Phase 1: 能力卡片解析
  → 读取 agent:card:{name}
  → 确定 model / thinking / lifecycle / skills

Phase 2: 连接鉴权
  → 连接 Redis / MongoDB / MCP Server
  → 验证 API Key 有效性

Phase 3: 长期知识加载
  → 检索 web:{intent}:* 缓存
  → 检索 exp:{intent}:all 经验摘要

Phase 4: 经验注入
  → 将匹配的过去经验作为 few-shot 示例
  → 注入 LLM system prompt 的 "参考经验" 段落

Phase 5: 工作记忆初始化
  → 创建新的 agent:{name}:{task_id}:memory
  → 空列表，等待任务开始
```

---

## 七、隔离机制详解

### 7.1 相同能力卡片的不同任务

```
Agent "审计官" 处理:
  任务A → agent:审计官:task-A:memory   → 销毁
  任务B → agent:审计官:task-B:memory   → 销毁
```

Key 中包含 `task_id`，天然隔离。

### 7.2 不同 Agent 之间

```
审计官 → agent:审计官:{task}:memory
翻译官 → agent:翻译官:{task}:memory
```

Key 中包含 `AGENT_NAME`，两个 Agent 永不串味。

### 7.3 经验层的共享

经验层 (`exp:{intent}:{agent}`) 是共享的——这是刻意的。因为"审计官上次怎么审的"对所有审计任务都有参考价值。

---

## 八、记忆进化流程

```
任务完成
    │
    ▼
L5 评分 ≥ 7? ──No──→ 不写入经验（避免污染经验库）
    │
   Yes
    │
    ▼
提取经验摘要
    │
    ├─→ 写入 exp:{intent}:{agent} (50 条 FIFO)
    │
    └─→ 如果是新发现的领域知识
         └─→ 写入 web:{intent}:{concept} (TTL 按领域)
              │
              ▼
         下次同类任务自动检索到
```

**设计原则**：只有成功的经验才写入（score ≥ 7）。失败的教训通过 L5 的 `meta_reflect` 单独记录，不混入经验库。

---

## 九、当前实现状态

| 功能 | 代码位置 | 状态 |
|------|---------|:----:|
| 工作记忆隔离 | neuron.py `MEMORY_KEY` | ✅ |
| 经验存储 | l0_memory.py `_save_experience` | ✅ |
| 经验检索 | l0_memory.py `_retrieve_experiences` | ✅ |
| Web 知识搜索 | workflow_engine `call_layer(5, "web_research")` | ⚠️ 依赖 MCP L5 |
| Web 知识缓存 | l0_memory.py `_save_web_knowledge` | ✅ |
| 向量语义检索 | modules/layer1/vector_store.py | ❌ MemVectorStore 空实现 |
| 分层 TTL | l0_memory.py domain→TTL 映射 | ✅ |

---

*上一步：阅读 `STATE-MACHINE.md` 了解状态转换规则。*
*下一步：阅读 `ARCHITECTURE.md` 了解整体架构。*
