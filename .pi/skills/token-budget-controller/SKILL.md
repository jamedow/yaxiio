---
name: token-budget-controller
description: Token预算控制器。基于优先级队列的上下文裁剪机制，当上下文超过80%窗口时触发压缩。保留策略：当前任务指令 > 关键决策 > 最新3轮对话 > 24小时外丢弃。作为底层常驻Skill，每次Agent通信前自动执行裁剪。当用户需要token优化、上下文管理、预算控制、clip、truncate时使用此技能。
---

# Token Budget Controller — Token预算控制器 v1.0

## ⛔ Constitution

**R1**：无感裁剪。压缩过程对Agent透明，不改变消息语义。

**R2**：优先级保序。当前任务指令(Critical) → 关键决策(Key Decision) → 最新3轮对话 → 超过24h的低优先级消息 → 丢弃。

**R3**：阈值精确。上下文超过 LLM 窗口的 80% 时触发，压缩至 60% 以下。

**R4**：累计统计。每次裁剪记录 token 节省量到 Redis `commander:token:stats`，不记录消息内容。

**R5**：模型感知。自动从 LLMRouter 获取当前使用的模型窗口大小（DeepSeek: 64K, GPT-4o: 128K）。

## 🎯 核心能力

### 优先级队列裁剪

```
┌─────────────────────────────────────────────────────┐
│              Context Window (e.g., 64K tokens)       │
│  ┌───────────────────────────────────────────────┐  │
│  │ P0: Current Task Instruction  [NEVER CLIP]    │  │
│  ├───────────────────────────────────────────────┤  │
│  │ P1: Key Decisions             [keep last 5]   │  │
│  ├───────────────────────────────────────────────┤  │
│  │ P2: Recent 3 Rounds           [keep last 3]   │  │
│  ├───────────────────────────────────────────────┤  │
│  │ P3: Historical Messages       [clip > 24h]    │  │
│  ├───────────────────────────────────────────────┤  │
│  │ ═══ 80% THRESHOLD ═══════════════════════════ │  │
│  │ P4: Overflow Zone            [DISCARD]        │  │
│  └───────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### 压缩流程

```
Agent.通信前
  │
  ▼
TokenBudgetController.check(messages, model)
  │
  ├─ 计算当前 token 数（tiktoken 或字符估算）
  ├─ 获取模型窗口大小（DeepSeek=65536, GPT-4o=128000）
  ├─ token_count > window_size * 0.8 ?
  │   ├─ 否 → 放行
  │   └─ 是 → 触发压缩
  │         ├─ P3 裁剪: 丢弃 >24h 的历史消息
  │         ├─ P2 裁剪: 保留最新 3 轮
  │         ├─ P1 裁剪: 保留最新 5 个关键决策
  │         ├─ 统计节省量 → Redis commander:token:stats
  │         └─ 返回压缩后 messages
  ▼
Agent.通信正常
```

### 模型窗口配置

| 模型 | 窗口大小 | 80%阈值 |
|------|---------|---------|
| deepseek-chat | 65536 | 52428 |
| deepseek-reasoner | 65536 | 52428 |
| gpt-4o | 128000 | 102400 |
| gpt-4o-mini | 128000 | 102400 |
| gpt-4-turbo | 128000 | 102400 |

### 统计指标

Redis `commander:token:stats` (JSON):
```json
{
  "total_saved": 0,
  "total_clips": 0,
  "avg_saved_per_clip": 0,
  "last_clip_at": null,
  "per_agent": {}
}
```

## 🔧 文件清单

| 文件 | 职责 |
|------|------|
| `SKILL.md` | Skill 元数据 + 文档 |
| `token_budget.py` | 头实现（280行） |
