# 雅溪 Yaxiio v1.0 — 操作手册

---

## 一、系统启动

### 1.1 启动容器
```bash
docker start yaxiio
```

容器启动后会自动拉起 Redis、MongoDB、双守护进程和 Commander。

### 1.2 检查状态
```bash
# 查看进程
docker exec yaxiio pm2 list
docker exec yaxiio ps aux | grep -E "guardian|yaxiio|neuron"

# 查看 Commander 日志
docker exec yaxiio tail -f /opt/commander/guard.log

# Dashboard
curl http://localhost:3003/api/dashboard/realtime
```

### 1.3 确认就绪
```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"ping","from":"api","to":"commander",
    "replyTo":"api","payload":{"action":"status"}}'
```
正常返回 `"version":"1.0"` 即就绪。

---

## 二、任务派发

所有任务通过 Redis Pub/Sub 派发到频道 `yaxiio:agent:commander`。

### 2.1 消息格式

```json
{
  "type": "task",
  "taskId": "唯一任务ID",
  "from": "发送者标识",
  "to": "commander",
  "replyTo": "接收回复的频道",
  "payload": {
    "action": "动作名称",
    "task": "任务描述",
    "...": "其他参数"
  }
}
```

### 2.2 接收回复

任务完成后，Commander 会向 `replyTo` 频道发布结果：
```json
{
  "type": "response",
  "taskId": "原任务ID",
  "payload": {
    "status": "success",
    "result": { ... }
  }
}
```

---

## 三、功能操作

### 3.1 多语翻译

**动作**: `translate`

将中文技术内容翻译为英文、俄文、阿拉伯文、西班牙文、法语。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"trans-001","from":"cms","to":"commander",
    "replyTo":"cms-reply",
    "payload":{"action":"translate","task":"将热镀锌螺旋地桩翻译为英文和俄文",
      "source":"zh","targets":["en","ru"]}}'
```

支持的目标语言: `en` `ru` `ar` `es` `fr`

### 3.2 内容质量审计

**动作**: `audit`

对网站代码进行设计合规性、术语一致性、旧版 UI 残留等检查。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"audit-001","from":"ci","to":"commander",
    "replyTo":"ci-reply",
    "payload":{"action":"audit","task":"审计customer-portal的Vue组件",
      "codebase":"/app/lightingmetal/customer-portal"}}'
```

审计报告保存在 `/app/.pi/blackboard/reports/audit-*.md`。

### 3.3 产品搜索

**动作**: `search`

从产品数据库中按关键词、规格检索产品。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"search-001","from":"sales","to":"commander",
    "replyTo":"sales-reply",
    "payload":{"action":"search","task":"查找M20热镀锌螺栓，材质Q235B"}}'
```

### 3.4 UI/UX 重设计

**动作**: `redesign`

触发完整的设计工作流：启发式评估→竞品分析→品牌转译→首页布局→坦克页重设计→移动端适配→设计规范输出。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"design-001","from":"ui","to":"commander",
    "replyTo":"ui-reply",
    "payload":{"action":"redesign",
      "task":"对LightingMetal首页进行UI/UX重设计",
      "context":{"brand_colors":["#D4A843","#14110F","#C87D4A"]}}}'
```

7 个子任务自动并行编排，约 2-3 分钟完成，结果写入任务状态机 `yaxiio:task:design-001`。

### 3.5 简单设计任务

**动作**: `design`

单次设计请求，走需求分析→设计方案→设计评审 3 步流程。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"design-002","from":"ui","to":"commander",
    "replyTo":"ui-reply",
    "payload":{"action":"design","task":"为B2B首页设计Hero区域配色方案"}}'
```

### 3.6 品牌策略

**动作**: `brand`

品牌调性分析、配色升级、竞品对比。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"brand-001","from":"marketing","to":"commander",
    "replyTo":"marketing-reply",
    "payload":{"action":"brand","task":"分析竞品B2B网站设计，提取品牌升级建议"}}'
```

### 3.7 前端工程

**动作**: `frontend`

技术可行性评估、响应式适配建议、架构优化。

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  yaxiio:agent:commander \
  '{"type":"task","taskId":"fe-001","from":"dev","to":"commander",
    "replyTo":"dev-reply",
    "payload":{"action":"frontend","task":"评估首页在移动端的TailwindCSS响应式方案"}}'
```

### 3.8 系统管理操作

以下操作走白名单直通，不经过五层流水线：

```bash
# Agent 配置导出
{"action":"agent_export"}

# Agent 配置导入
{"action":"agent_import","agents":{...}}

# Skill 导出
{"action":"skill_export"}

# Skill 导入
{"action":"skill_import","skills":{...}}

# 系统状态
{"action":"status"}

# 清理沙箱
{"action":"session_end"}
```

---

## 四、查询任务状态

```bash
# 查看单个任务
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning GET "yaxiio:task:任务ID"

# 列出所有活跃任务
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning SMEMBERS "yaxiio:task:active"

# 查看子任务产出
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning GET "yaxiio:output:任务ID:子任务ID"
```

任务状态包含：五层里程碑进度、子任务执行的 Agent 和耗时、完整事件时间线。

---

## 五、模型路由配置

### 5.1 查看当前配置

| Agent | 模型 | 推理深度 | 说明 |
|------|------|:--:|------|
| 翻译官 | deepseek-chat | low | 追求速度 |
| 审计官 | deepseek-chat | high | 深度推理 |
| UI/UX设计师 | deepseek-chat | medium | 平衡 |
| 品牌策略师 | deepseek-chat | high | 策略分析 |
| 前端工程师 | deepseek-chat | medium | 平衡 |
| 系统医生 | deepseek-chat | high | 故障诊断 |

### 5.2 热更新

```bash
# 运行时切换翻译官到 Flash 模型
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning SETEX \
  "yaxiio:model:config:翻译官" 86400 \
  '{"default":{"model":"deepseek-v4-flash","thinking":"off"}}'
```

下次翻译官启动时自动生效，无需重启系统。

---

## 六、故障处理

### 6.1 Agent 崩溃

Commander 自动检测心跳丢失，直接重启 Agent 进程。无需人工干预。

### 6.2 Agent 质量下降

连续 3 次 L5 评分低于 5 分时，Commander 自动派出系统医生：

1. 医生读取 Agent 记忆和 Skill 文件
2. LLM 分析根因（提示词歧义/缺失/矛盾）
3. 生成 2-3 个修复候选
4. A/B 测试选优
5. 更新 Agent 的 Prompt

报告保存在 `/app/.pi/blackboard/reports/doctor-*.md`。

### 6.3 Commander 崩溃

Guardian 自动重启。如果 Guardian 也挂了，备用的 secondary Guardian 在 30 秒内接管。

### 6.4 查看违宪记录

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning \
  LRANGE "yaxiio:constitution:violations" 0 -1
```

---

## 七、停止系统

```bash
docker stop yaxiio
```
