# 雅溪 Yaxiio v1.0 — 外部调用手册

> LightingMetal 多智能体调度系统  
> 最后更新: 2026-05-27

---

## 一、入口地址

| 入口 | 地址 | 用途 |
|------|------|------|
| 任务派发 | Redis Pub/Sub `yaxiio:agent:commander` | 所有任务入口 |
| Dashboard | `http://yaxiio.lightingmetal.com:3003/dashboard` | 系统监控 |
| 黑板报告 | `http://yaxiio.lightingmetal.com:3003/blackboard` | 审计/修复报告 |
| WebSocket | `ws://yaxiio.lightingmetal.com:3398` | 实时推送 |
| L1-L5 MCP | `http://yaxiio.lightingmetal.com:3401-3405` | 内部层间通信（不对外） |

> Redis 仅容器内可访问。外部系统通过 HTTP API 或容器内调用。

---

## 二、任务派发

### 2.1 消息格式

```json
{
  "type": "task",
  "taskId": "唯一ID（建议: 动作-时间戳-随机6位）",
  "from": "调用方标识",
  "to": "commander",
  "replyTo": "接收回复的Redis频道",
  "payload": {
    "action": "动作（见第三章）",
    "task": "任务描述文本",
    "context": { "可选上下文": "..." },
    "_model": "可选: 覆盖模型",
    "_thinking": "可选: off/low/medium/high/max"
  }
}
```

### 2.2 回复格式

```json
{
  "type": "response",
  "taskId": "原任务ID",
  "from": "yaxiio",
  "to": "调用方",
  "payload": {
    "status": "success|error|rejected",
    "result": { "具体产出": "..." },
    "completed_at": 1779876543.123
  }
}
```

### 2.3 调用示例（Python）

```python
import redis, json, time

r = redis.Redis(host="容器IP", port=6379, password="Yaxiio2026", decode_responses=True)

# 派发任务
r.publish("yaxiio:agent:commander", json.dumps({
    "type": "task",
    "taskId": f"my-task-{int(time.time())}",
    "from": "my-app",
    "to": "commander",
    "replyTo": "my-app-reply",
    "payload": {"action": "design", "task": "首页Hero区域配色方案"}
}, ensure_ascii=False))

# 监听回复
ps = r.pubsub()
ps.subscribe("my-app-reply")
for msg in ps.listen():
    if msg["type"] == "message":
        print(json.loads(msg["data"]))
        break
```

---

## 三、支持的动作

### 3.1 多语翻译 `translate`

将中文翻译为目标语言。Agent: 翻译官，推理深度: low。

```json
{"action":"translate","task":"翻译内容","targets":["en","ru","ar","es","fr"]}
```

### 3.2 内容审计 `audit`

扫描代码库，检查设计合规性、术语一致性、旧版 UI 残留。

```json
{"action":"audit","task":"审计描述","codebase":"/app/customer-portal"}
```

报告输出到 `/app/.pi/blackboard/reports/audit-*.md`。

### 3.3 产品搜索 `search`

从产品数据库检索产品。

```json
{"action":"search","task":"M20热镀锌螺栓 Q235B"}
```

### 3.4 完整重设计 `redesign`

触发 7 步设计工作流：评估→竞品分析→品牌转译→首页布局→坦克页→移动端→规范输出。

```json
{
  "action":"redesign",
  "task":"LightingMetal首页UI/UX重设计",
  "context":{"brand_colors":["#D4A843","#14110F","#C87D4A"]}
}
```

7 个子任务自动并行编排，约 2-3 分钟，结果写入 `yaxiio:task:{id}`。

### 3.5 简单设计 `design`

需求分析→设计方案→设计评审，3 步流程。

```json
{"action":"design","task":"B2B首页Hero区域配色方案"}
```

### 3.6 品牌策略 `brand`

品牌调性分析、配色升级建议、竞品对比。

```json
{"action":"brand","task":"分析竞品B2B网站设计模式"}
```

### 3.7 前端工程 `frontend`

技术可行性评估、响应式适配方案、架构建议。

```json
{"action":"frontend","task":"评估移动端响应式方案"}
```

### 3.8 系统管理（白名单直通）

```json
{"action":"status"}           // 系统状态
{"action":"agent_export"}     // 导出Agent配置
{"action":"agent_import","agents":{...}}  // 导入Agent配置
{"action":"skill_export"}     // 导出Skill
{"action":"skill_import","skills":{...}}  // 导入Skill
{"action":"session_end"}      // 清理沙箱
```

---

## 四、结果查询

### 4.1 任务状态

```bash
redis-cli -a Yaxiio2026 GET "yaxiio:task:任务ID"
```

返回结构：
```json
{
  "status": "DONE",
  "current_layer": "L5_evaluation",
  "progress_pct": 100,
  "milestones": {
    "L1_perception": {"status":"done"},
    "L2_planning":   {"status":"done"},
    "L3_dispatch":  {"status":"done"},
    "L4_execution":  {"status":"done"},
    "L5_evaluation": {"status":"done"}
  },
  "subtasks": {
    "s1": {"agent":"UI/UX设计师","status":"DONE","duration_ms":14004}
  },
  "timeline": [
    {"event":"TASK_CREATED","ts":...,"detail":"..."},
    {"event":"SUBTASK_DONE","ts":...,"detail":"s1 14004ms"}
  ]
}
```

### 4.2 子任务产出

```bash
redis-cli -a Yaxiio2026 GET "yaxiio:output:任务ID:子任务ID"
```

### 4.3 Dashboard

```bash
# 实时指标
curl http://yaxiio.lightingmetal.com:3003/api/dashboard/realtime

# 活跃任务列表
curl http://yaxiio.lightingmetal.com:3003/api/blackboard/reports
```

---

## 五、模型路由

调用方可覆盖模型和推理深度：

```json
{
  "action": "translate",
  "task": "...",
  "_model": "deepseek-v4-flash",
  "_thinking": "off"
}
```

系统默认配置：

| Agent | 模型 | 推理深度 |
|------|------|:--:|
| 翻译官 | deepseek-chat | low |
| 审计官 | deepseek-chat | high |
| UI/UX设计师 | deepseek-chat | medium |
| 品牌策略师 | deepseek-chat | high |
| 系统医生 | deepseek-chat | high |

热更新：
```bash
redis-cli -a Yaxiio2026 SETEX "yaxiio:model:config:翻译官" 86400 \
  '{"default":{"model":"deepseek-v4-flash","thinking":"off"}}'
```

---

## 六、故障处理

| 场景 | 系统行为 |
|------|---------|
| Agent 崩溃 | Commander 自动重启 |
| Agent 连续低分 | 系统医生自动诊断提示词→A/B测试→修复 |
| Commander 崩溃 | Guardian 自动重启（30秒内） |
| Guardian 崩溃 | 备用 Guardian 接管 |

查看违宪记录：`redis-cli LRANGE "yaxiio:constitution:violations" 0 -1`

---

## 七、快速检查

```bash
# 系统存活
curl http://yaxiio.lightingmetal.com:3003/api/dashboard/realtime

# Commander 订阅者数
redis-cli -a Yaxiio2026 PUBSUB NUMSUB yaxiio:agent:commander

# 活跃 Agent
redis-cli -a Yaxiio2026 SMEMBERS commander:agents:active

# 双守护状态
redis-cli -a Yaxiio2026 GET yaxiio:guardian:leader

# 任务总数
redis-cli -a Yaxiio2026 SCARD yaxiio:task:active
```

---

> 内部操作详见 [操作手册](./yaxiio-v1.0-操作手册.md)  
> 架构细节详见 [架构文档](./yaxiio-architecture-v2.md)
