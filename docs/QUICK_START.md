# 雅溪 Yaxiio 快速上手指南

> 选择你的使用深度。每节标注了适合的用户等级。
> 🟢 平民级 | 🔵 维护级 | 🟣 技术级 | ⚪ 大神级

---

## 🟢 平民级：5 分钟启动 + 提交第一个任务

> 不需要知道 Agent 是什么。只需要描述任务目标。

### 启动

```bash
docker run -d --name yaxiio \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -p 3398:3398 -p 3399:3399 \
  -e DEEPSEEK_API_KEY=你的Key \
  --restart unless-stopped \
  yaxiio:prod
curl http://localhost:3399/health
```

### 提交任务

打开 Dashboard `http://localhost:3399`，打字：

```
把 power 行业 100 条产品描述翻译成阿拉伯语
```

系统会自己拆解、调度、执行、评分。你等着看结果就行。

---

## 🔵 维护级：10 分钟定制 Agent

> 会看说明书。知道 quality 预设就够了。

### quality 三档预设

```json
{ "name": "我的翻译官", "quality": "standard", "skills": ["translate-engine"] }
```

| quality | 自动设置 | 适合 |
|---------|---------|------|
| `fast` | flash 模型，不深度思考 | 简单翻译、分类 |
| `standard` | chat 模型，适度思考 | 大多数场景 |
| `premium` | max 模型，深度思考 | 审计、复杂分析 |

想微调个别参数？直接加到卡片里就行——`"max_retries": 5`。

---

## 🟣 技术级：15 分钟自我进化 + 深度调优

> 了解设计思想。能精确控制每个参数。

<details>
<summary>完整能力卡片参数列表</summary>

```json
{
  "name": "高级翻译官",
  "quadrant": "strategic",
  "quality": "premium",
  "model": "deepseek-max",
  "thinking": "high",
  "temperature": 0.1,
  "few_shot_examples": [
    { "input": "EPDM rubber gasket", "output": "EPDM橡胶密封垫片" }
  ],
  "lifecycle": { "task_timeout": 600, "max_retries": 5 }
}
```
</details>

### 触发进化

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 PUBLISH yaxiio:agent:commander \
  '{"type":"task","taskId":"evolve-001","payload":{"action":"translate","task":"翻译500条到阿拉伯语","_thinking":"high"}}'
```

### 全链路追踪

```bash
curl http://localhost:3399/trace/task-001
# L1→L5 每一步决策理由 + 耗时
```

---

## ⚪ 大神级：底层调优

> 深入理解实现。能改宪法、调协议、做联邦。

```bash
# 修改宪法白名单
redis-cli -a Yaxiio2026 SET "yaxiio:config:forbidden_actions" '["site_audit","custom_action"]'

# 自定义 quality 预设
redis-cli -a Yaxiio2026 SET "yaxiio:config:quality_presets" \
  '{"turbo":{"model":"flash","thinking":"off","max_retries":1}}'

# 联邦部署
curl -X POST http://yaxiio-a:3404/jsonrpc \
  -d '{"method":"federate","params":{"peer":"http://yaxiio-b:3404"}}'
```

---

| 下一步 | 适合 |
|--------|------|
| [设计哲学](DESIGN_PHILOSOPHY.md) | 🟣 |
| [完整架构](ARCHITECTURE.md) | 🟣 |
| [能力卡片规范](CAPABILITY_CARD_SPEC.md) | 🔵 |
| [API 文档](API.md) | 🔵 |
| [质量宪章](QUALITY_CONSTITUTION.md) | ⚪ |
| [防呆设计体系](YAXIIO_DESIGN.md) | 🟣 |
