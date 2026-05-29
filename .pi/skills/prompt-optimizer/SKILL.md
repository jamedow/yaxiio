---
name: prompt-optimizer
description: 提示词优化器。基于GEPA算法(Generate→Evaluate→Pick→Apply)自动迭代Agent提示词质量。触发条件：Agent错误率>10%或累计运行50次任务。执行流程：读取执行轨迹→LLM分析→生成2-5个候选→A/B测试→自动选优。当用户需要优化提示词、提升Agent质量、prompt engineering、prompt optimization时使用此技能。
---

# Prompt Optimizer — 提示词优化器 v1.0

## ⛔ Constitution

**R1**：可回滚。每次优化前备份原始prompt到 Redis `commander:prompt:backup:{agent}:{version}`。

**R2**：数据驱动。不靠直觉判断好坏，必须基于 A/B 测试的统计显著结果（p < 0.05 或胜率 > 55%）。

**R3**：逐步放量。新 prompt 先在 10% 流量上测试，稳定后扩展到 50%，最终全量。

**R4**：保留原始。Agent 的原始 prompt 永远保留在 `commander:prompt:original:{agent}`，不可覆盖。

**R5**：单Agent隔离。一次只优化一个Agent的prompt，避免交互效应混淆结果。

## 🎯 GEPA 算法

### Generate → Evaluate → Pick → Apply

```
Agent 错误率 > 10% 或 累计 50 次任务
  │
  ▼
[G] Generate — LLM分析执行轨迹
  │  输入: 最近50次失败案例 + 当前prompt
  │  输出: 2-5个优化候选版本
  │
  ▼
[E] Evaluate — 离线评估
  │  对每个候选: 在历史数据集上模拟执行
  │  评分维度: 准确率 / 完整度 / 格式合规
  │
  ▼
[P] Pick — 选择优胜者
  │  评估分最高的2个候选 → 进入A/B测试
  │  流量分配: 原始(80%) vs 候选A(10%) vs 候选B(10%)
  │  24h后自动决策: 选择胜率最高的
  │
  ▼
[A] Apply — 应用新prompt
  │  备份原始 → Redis commander:prompt:backup
  │  更新 agent-registry 中的 prompt 字段
  │  通知 Commander 重新加载 Agent 配置
  │
  ▼
监控 48h → 如退化 → 自动回滚
```

### 触发条件

| 条件 | 阈值 | 检测频率 |
|------|------|---------|
| 错误率 | > 10% (最近50次) | 每10次任务 |
| 累计任务 | ≥ 50 次 | 每10次任务 |
| 手动触发 | `pi optimize-prompt <agent>` | 随时 |

### Prompt 候选生成策略

```
LLM 分析模板：
  你是Prompt优化专家。分析以下Agent的执行轨迹，找出prompt中的弱点。
  
  Agent: {agent_name}
  当前Prompt: {current_prompt}
  最近50次任务结果: {execution_traces}
  
  请生成2-5个优化后的prompt候选版本，每个版本必须：
  1. 保持原有任务目标不变
  2. 改进明确的弱点（如歧义、缺少约束、格式不一致）
  3. 标注改进点（格式: ## IMPROVEMENT: ...）
```

### A/B 测试参数

```json
{
  "test_id": "prompt-opt-{agent}-{timestamp}",
  "control": {
    "prompt": "<原始prompt>",
    "traffic_share": 0.80
  },
  "variants": [
    {"prompt": "<候选A>", "traffic_share": 0.10},
    {"prompt": "<候选B>", "traffic_share": 0.10}
  ],
  "metrics": ["success_rate", "avg_score", "format_compliance"],
  "duration_hours": 24,
  "auto_decision": true
}
```

## 🔧 文件清单

| 文件 | 职责 |
|------|------|
| `SKILL.md` | Skill 元数据 + GEPA 算法文档 |
| `prompt_optimizer.py` | 实现（320行） |
