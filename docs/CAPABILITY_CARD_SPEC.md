# 雅溪 Yaxiio 能力卡片规范

> Version: 1.0 | Date: 2026-05-29
> 能力卡片 = Agent 的"身份证 + 简历 + 合同"三合一

---

## 一、能力卡片是什么？

能力卡片（Capability Card）是 Yaxiio 中定义 Agent 的**唯一标准格式**。一个 Agent 的所有配置——它叫什么、用什么模型、有什么工具、怎么活、怎么死——全在一张 JSON 卡片里。

Agent 本身是统一的 `neuron.py` 壳，套不同的能力卡片就变成不同的 Agent。壳不变，大脑换。

---

## 二、六要素

```json
{
  "identity":    {},   // ① 身份信息：名字、角色、版本
  "brain":       {},   // ② 大脑配置：模型、温度、system prompt
  "toolbox":     {},   // ③ 工具箱：Skill、MCP Server、工具脚本
  "interface":   {},   // ④ 接口定义：输入/输出 Schema
  "state_machine": {}, // ⑤ 状态机：超时、重试、心跳
  "resources":   {}    // ⑥ 资源限制：内存、并发数
}
```

---

## 三、完整模板

```json
{
  "name": "审计官",
  "role": "内容质量审计专家",
  "quadrant": "strategic",
  "version": "2.1.0",

  "model": "deepseek-chat",
  "thinking": "medium",
  "temperature": 0.3,
  "max_tokens": 2000,
  "system_prompt": "你是严谨的内容质量审计专家。\n职责：检查术语一致性、参数准确性、格式规范性。\n原则：不猜测，只基于给定标准判断。\n输出：明确指出错误位置、错误内容和正确标准。",

  "skills": ["audit-engine"],
  "mcp_servers": [
    {
      "name": "mongodb",
      "mode": "read_only",
      "collections": ["page_content"]
    }
  ],
  "tools": ["mongo_query", "redis_query", "multilang_audit", "terminology_check"],

  "input_schema": {
    "type": "object",
    "required": ["content"],
    "properties": {
      "content":  { "type": "string", "description": "待审计的文本内容" },
      "standard": { "type": "string", "description": "审计标准文件ID或内容" },
      "target":   { "type": "string", "enum": ["all", "power", "mining", "agriculture"] }
    }
  },
  "output_schema": {
    "type": "object",
    "required": ["score", "issues"],
    "properties": {
      "score":       { "type": "number", "minimum": 0, "maximum": 10 },
      "issues":      { "type": "array", "items": { "type": "object" } },
      "summary":     { "type": "string" },
      "suggestions": { "type": "array" }
    }
  },

  "lifecycle": {
    "init_timeout": 30,
    "task_timeout": 300,
    "max_retries": 3,
    "retry_backoff": "exponential",
    "idle_timeout": 600,
    "heartbeat_interval": 30
  },

  "resource_limits": {
    "max_memory": 256,
    "max_concurrent_tasks": 1
  }
}
```

---

## 四、字段详解

### 4.1 身份信息

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `name` | string | ✅ | Agent 唯一名称，中文。用作 Redis 频道名 `lightingmetal:agent:{name}` |
| `role` | string | ✅ | 一句话角色描述 |
| `quadrant` | string | ✅ | 四象限分类：`core` / `strategic` / `utility` / `ephemeral` |
| `version` | string | ❌ | 语义化版本号 |

### 4.2 大脑配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `model` | string | `deepseek-chat` | LLM 模型名。推荐 `deepseek-chat` / `deepseek-reasoner` |
| `thinking` | string | `medium` | 推理深度：`off` / `low` / `medium` / `high` / `max` |
| `temperature` | number | 0.3 | 0-2，越低越保守 |
| `max_tokens` | number | 2000 | 单次回复最大 token 数 |
| `system_prompt` | string | — | 系统提示词，定义 Agent 行为 |

**模型选择指南**：

| 场景 | 推荐模型 | thinking |
|------|---------|----------|
| 翻译、查询 | `deepseek-chat` | `off` / `low` |
| 修复、创建 | `deepseek-chat` | `medium` / `high` |
| 审计、分析、拆解 | `deepseek-reasoner` | `high` / `max` |

### 4.3 工具箱

| 字段 | 类型 | 说明 |
|------|------|------|
| `skills` | string[] | Skill 名称列表，对应 `skills/{name}/SKILL.md` |
| `mcp_servers` | object[] | MCP 服务器列表，每项含 `name`、`mode`、`collections` |
| `tools` | string[] | 可用工具脚本名，对应 `tools/{name}.py` |

**MCP Server mode**：
- `read_only`：只能读取，不能写入（审计官只读 MongoDB）
- `read_write`：可读写（LM 内容工程师需要修改内容）
- `admin`：完全访问（仅 Core 象限 Agent 可用）

### 4.4 接口定义

`input_schema` 和 `output_schema` 遵循 JSON Schema 规范。Commander 在分配任务前会用此校验数据格式。

### 4.5 状态机

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `init_timeout` | number | 30 | Agent 初始化超时（秒） |
| `task_timeout` | number | 300 | 单个任务超时（秒） |
| `max_retries` | number | 3 | 最大重试次数 |
| `retry_backoff` | string | `exponential` | 重试策略：`exponential` / `linear` / `fixed` |
| `idle_timeout` | number | 600 | 闲置超时（秒），Strategic Agent 超时后休眠 |
| `heartbeat_interval` | number | 30 | 心跳间隔（秒） |

### 4.6 资源限制

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_memory` | number | 256 | 最大内存（MB），超限告警 |
| `max_concurrent_tasks` | number | 1 | 最大并发任务数 |

---

## 五、四象限分类规则

```
高价值 │
       │  Strategic          Core
       │  按需创建，闲置回收   永不销毁
       │  例：审计官、SEO分析师  例：翻译官、售前经理
       │
       │  Ephemeral          Utility
       │  用完即弃            长期驻留
低价值 │  例：临时数据分析师    例：UI设计师、前端工程师
       └──────────────────────────
         临时性                长期性
```

**判断标准**：

| 条件 | 象限 |
|------|------|
| 每次任务都需要 + 不可替代 + 有状态 | `core` |
| 按需使用 + 可替代 + 有状态 | `strategic` |
| 长期需要 + 可替代 + 无状态 | `utility` |
| 一次性 + 可替代 + 无状态 | `ephemeral` |

---

## 六、Skill 挂载规范

每个 Skill 是一个目录，目录名 = Skill 名称：

```
skills/
  audit-engine/
    SKILL.md        ← 核心技能描述（system prompt）
    experience/     ← 经验数据
      2026-05.json
  translate-engine/
    SKILL.md
    glossary.json   ← 术语表
```

**命名空间规则**：
- Skill 名称全小写，用连字符连接：`audit-engine`、`seo-optimizer`
- 不与 tools 脚本名冲突
- 版本号通过 Skill 目录下的 `VERSION` 文件管理

---

## 七、完整示例

### 翻译官（Core）

```json
{
  "name": "翻译官",
  "role": "多语言翻译专家",
  "quadrant": "core",
  "version": "3.0.0",
  "model": "deepseek-chat",
  "thinking": "low",
  "temperature": 0.2,
  "system_prompt": "你是专业的多语言翻译专家。职责：准确翻译、保持术语一致、保留格式。",
  "skills": ["translate-engine"],
  "tools": ["batch_translate", "fast_translate", "terminology_check"],
  "lifecycle": {
    "task_timeout": 600,
    "max_retries": 2,
    "heartbeat_interval": 30
  }
}
```

### SEO 分析师（Strategic）

```json
{
  "name": "SEO分析师",
  "role": "网站SEO诊断与优化专家",
  "quadrant": "strategic",
  "version": "1.0.0",
  "model": "deepseek-reasoner",
  "thinking": "high",
  "temperature": 0.3,
  "system_prompt": "你是SEO分析专家。分析页面SEO表现，输出诊断报告和优化建议。使用工具获取真实数据。",
  "skills": ["seo-engineer"],
  "tools": ["mongo_query", "content_sync"],
  "lifecycle": {
    "task_timeout": 600,
    "idle_timeout": 1800,
    "max_retries": 3
  }
}
```

---

## 八、注册与发现

能力卡片存储在 Redis：

```
agent:card:审计官        → 完整 JSON 卡片
agent:card:翻译官        → 完整 JSON 卡片
agent:registry           → Set ["审计官", "翻译官", ...]
agent:quadrant:core      → Set ["翻译官", "售前经理"]
agent:quadrant:strategic → Set ["审计官", "SEO分析师"]
agent:quadrant:utility   → Set ["UI/UX设计师", "前端工程师"]
agent:quadrant:ephemeral → Set []
```

Commander 通过 `agent:registry` 发现所有可用 Agent，通过 `agent:quadrant:{type}` 按象限筛选。

---

*下一步：阅读 `STATE-MACHINE.md` 了解 Agent 状态转换规则。*
