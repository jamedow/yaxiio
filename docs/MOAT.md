# Yaxiio 护城河 — 核心竞争优势

> 版本: v3.3 | 日期: 2026-05-30
> Yaxiio 不只是任务执行器，而是一个会侦察、会预热、会自我进化的 AI 编排系统。

---

## 一、护城河全景

```
任务到达
  │
  ├─ 1. 侦察框架 (Recon)         ← 探明体量/范围/复杂度/风险
  │     └─ 4 个可插拔维度，纯文件系统操作，毫秒级
  │
  ├─ 2. 预热门控 (WarmupGate)    ← 小样本策略寻优
  │     └─ 试跑 → L5评分 → gap调整 → 重试 → 锁定最优策略
  │
  ├─ 3. 心跳进度 (Heartbeat)     ← 神经元主动汇报，防止误判超时
  │     └─ 进度增长 → 自动延长超时；进度停滞 → 判定真超时
  │
  ├─ 4. Stream 全链路             ← 消息持久化不丢失
  │     └─ Pub/Sub 回退兼容
  │
  ├─ 5. L5 自进化                 ← 评分 → gap分析 → 自动重试优化
  │     └─ ExperienceFlywheel 经验飞轮
  │
  └─ 6. 防呆体系 (Foolproof)     ← 四层渐进披露，平民→大神
```

---

## 二、侦察框架 (Task Reconnaissance)

**文件:** `task_recon.py`

### 设计理念
接到新任务后，不急于执行。先派遣侦察 Agent 探明任务体量，Commander 据此决定拆分策略。

### 四维度 (可插拔，Commander 按任务类型调配)

| 维度 | 探测内容 | 适用场景 |
|------|---------|---------|
| volume | 文件数/大小/类型分布 | 全部 |
| scope | 目录结构/深度/广度 | 构建、国际化 |
| complexity | 平均文件大小/嵌套深度/二进制检测 | 审计、诊断 |
| risk | 超大文件/高密度单类型/超大型项目 | 审计、修复 |

### 维度调配 (三级优先级)

```
显式指定 (_recon_dimensions) > action 匹配 (ACTION_DIMENSIONS) > 默认兜底
```

例:
- `site_audit` → `[volume, complexity, risk]` (3 维)
- `translate` → `[volume]` (1 维)
- `_recon_dimensions: ["volume", "risk"]` → 显式覆盖

### 输出
侦察报告注入 payload，包含:
- `suggested_timeout`: 建议超时秒数
- `max_concurrent`: 建议并发数
- `chunk_size`: 建议分片大小

---

## 三、预热门控 (Warmup Gate)

**文件:** `warmup_gate.py`

### 设计理念
大任务在执行前，先在小样本上跑通并找到最优策略。灵感来自 CTM 论文的 "探知边界" 思想。

### 流程

```
1. 侦察报告 → should_warmup? (文件数 > chunk_size?)
2. 是 → 生成初始策略 (thinking/agent/chunk/flow)
3. 提取小样本 (默认 5 个文件)
4. 跑完整 L1→L5 (含 round 重试)
5. L5 评分
   ├─ ≥ 6.0 → 锁定策略 → 注入全量 payload → 规模化执行
   └─ < 6.0 → gap 分析 → 调整策略 → 重试 (max 3 轮)
```

### 策略维度 (预热阶段可调整)

| 参数 | 说明 | 调整触发 |
|------|------|---------|
| thinking | medium → high → max | prompt_needs_optimization |
| agent | 更换 Agent | agent_mismatch |
| chunk | 分片粒度 | 侦察报告建议 |
| use_complex_flow | 简单 vs LLM 拆解 | knowledge_gap + round ≥ 2 |

### 关键设计
- 预热阶段不产生副作用（`_is_sample` 标记隔离）
- 达标后策略锁定，全量执行复用
- 不达标也继续执行，但不锁定策略

---

## 四、心跳进度 (Heartbeat Progress)

**文件:** `neuron.py` (Neuron 端) + `async_orchestrator.py` / `workflow_engine.py` (Commander 端)

### 设计理念
神经元在执行长任务时主动汇报进度，Commander 据此动态调整超时，避免误判。

### Neuron 端

```python
def _report_progress(self, task_id, pct, msg):
    """向 Commander 汇报执行进度"""
    self.redis.setex(f"agent:{self.name}:{task_id}:state", 3600,
        json.dumps({"progress": pct, "ts": time.time(), "msg": msg}))
```

在 `think_and_act` 关键节点调用:
- `_report_progress(task_id, 5, "开始分析")`
- `_report_progress(task_id, 20, "LLM 思考中...")`
- `_report_progress(task_id, 50, "LLM 完成")`
- `_report_progress(task_id, 75, "工具反馈分析中...")`

### Commander 端

在 `_wait_neuron` 循环中:
```python
# 心跳检查
raw = r.get(f"agent:{agent_name}:{task_id}:state")
if pct > last_progress:
    last_progress = pct
    dynamic_timeout = min(600, dynamic_timeout + 60)  # 有进展 → 延长
elif time.time() - last_progress_ts > 180:
    break  # 停滞 > 180s → 真超时
```

---

## 五、Stream 全链路通信

**文件:** `stream_bridge.py` + 各模块集成

### 架构

```
Gateway ──→ yaxiio:stream:task_incoming ──→ Commander 主循环
Commander ──→ yaxiio:stream:L4 ──→ Neuron (CG: agents-L4)
Neuron ──→ yaxiio:stream:L4_response ──→ Commander (CG: commander-response)
```

### 设计原则
- **Stream 优先 + Pub/Sub 回退**: 所有通道双写
- **Consumer Group**: 自动负载均衡 + 故障恢复
- **ACK 机制**: 消息确认，未 ACK 的消息可被其他 consumer 接管
- **Pending 恢复**: Agent 崩溃后任务自动转移

---

## 六、L5 自进化循环

**文件:** `workflow_engine.py` (round 循环) + `experience_flywheel.py`

### 目标自检循环

```
执行 → L5 评分 → goal_met?
  ├─ 是 → DONE + 飞轮保存经验
  └─ 否 → gap 分析 → 生成新 subtask → 重试 (max 3 rounds)
```

### Flywheel 经验飞轮
- 每次任务完成后保存经验到 Chroma 向量库
- 后续任务通过 L0 检索历史经验注入 LLM prompt
- Agent credit 评分：高频超时 → 降级 thinking level

---

## 七、防呆体系 (Foolproof)

**文件:** `modules/shared/foolproof.py`

### 四层渐进披露

| 层级 | 名称 | 说明 |
|------|------|------|
| T1 | 平民 | 零配置，全部自动 |
| T2 | 探索者 | 可调关键参数 |
| T3 | 工匠 | 完整配置能力 |
| T4 | 大神 | 源码级定制 |

### 关键函数
- `safe_default(key)`: 集中管理的默认值
- `validate_in_range(value, key, min, max)`: 参数边界校验
- `friendly_error(op, detail, suggestion)`: 人类可读错误
- `assess_risk(action, context)`: 风险等级评估

---

## 八、关键 Bug 修复记录

| Bug | 根因 | 修复 |
|-----|------|------|
| BoundedThreadPool 死锁 | `with self._lock` 内重复 acquire 非重入锁 | 删除内部 acquire |
| Guardian KeyError | evaluate() 返回 key 不一致 | 统一 `overall` |
| trace_id NameError | 闭包作用域缺失 | 添加变量定义 |
| Neuron SKILL_DIR | 路径 `/app` 不存在 | → `/opt/yaxiio` |
| Neuron card 返回空 | `return {}` | → `return card` |
| Pipe buffer 阻塞 | 默认 4KB pipe | fcntl 1MB + PYTHONUNBUFFERED |
| Pub/Sub 消息丢失 | 瞬态消息 | Stream 全链路迁移 |
| LLMAdapter 兼容 | chat/completions 双接口 | 自动检测 |

---

## 九、配置文件速查

| 配置 | 值 | 位置 |
|------|-----|------|
| Redis Stream L4 | `yaxiio:stream:L4` | Consumer Group: `agents-L4` |
| Redis Stream 响应 | `yaxiio:stream:L4_response` | Consumer Group: `commander-response` |
| Redis Stream 入口 | `yaxiio:stream:task_incoming` | Consumer Group: `commander-main` |
| 侦察报告 | `yaxiio:recon:{task_id}` | TTL 86400 |
| Agent 进度 | `agent:{name}:{task_id}:state` | TTL 3600 |
| API Key | `yaxiio:config:llm_api_key` | Redis |

---

## 十、下一步演进

1. **慢思考策略锦标赛** — 多策略并行试跑，自动择最优
2. **Agent 私有笔记** — 每个 Agent 维护自己的经验上下文
3. **Commander 策略市场** — 历史最优策略可跨任务复用
4. **侦察深度增强** — 接入 AST 解析、依赖图分析
5. **预热智能触发** — 基于任务相似度自动决定是否预热
