# Commander Constitution — 宪法 v2.3

## 概述

宪法是 Commander 多Agent系统的最高约束规范。所有模块、Agent、扩展系统必须无条件遵守。

## 规则

### R1: Redis 只读不删

**禁止**对以下前缀的 Key 执行 `DEL` / `FLUSH` / `FLUSHALL` 操作：
- `page:*`
- `agent:*` （生命周期管理除外）
- `lightingmetal:*`

**允许**的前缀：
- `commander:*` — Commander 内部状态
- `skills:*` — Skill 注册表
- `mcp:*` — MCP Server 注册表
- `extensions:*` — 扩展系统
- `lifecycle:*` — 生命周期管理
- `agent:metadata:*` — Agent 元数据

### R2: Agent 上限 10 个

- 并行运行的子Agent ≤ 10
- 由 `SafetyBoundary` 强制执行
- 扩容请求超过上限时拒绝并告警

### R3: 报价先存草稿

- 客户报价必须在**发送前**存储到 MongoDB 草稿
- 草稿状态：`draft` → `sent` → `accepted` / `rejected`
- 由 CommanderV2 层强制执行

### R4: 消息格式标准化

所有 Agent 间通信**强制使用**标准 JSON 协议：

```json
{
  "from": "发送者",
  "to": "接收者",
  "type": "task|request|response|error|heartbeat|heartbeat_check|shutdown",
  "taskId": "唯一任务ID",
  "timestamp": "ISO 8601",
  "replyTo": "回复目标(可选)",
  "payload": {
    "action": "操作名",
    "data": {}
  }
}
```

### R5: 故障自动降级

| 场景 | 处理 |
|------|------|
| Agent 无心跳 | 30s 超时重试，最多3次 |
| Agent 连续失败3次 | `pm2 delete` 销毁 → `pm2 start` 重建 |
| Redis 断连 | 暂停新任务，已运行任务继续 |
| MongoDB 写入失败 | 本地缓存 → 恢复后补写 |

五级降级：L0(全量) → L1(无售前) → L2(无商务) → L3(无翻译) → L4(仅Commander)

### R6: P2P 优先

- Agent 间协作**优先**走 P2P 直连通道
- 通过 `replyTo` 字段指定回复目标
- 仅生命周期管理（创建/销毁/监控）必须经过 Commander

### R7: 消息不可篡改

- Agent 收到消息后**只可追加** `forwardedBy` 字段
- **不可修改**原始 `payload` 内容
- 违规消息将被 `SafetyBoundary` 拦截

## 修订历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 2.3 | 2026-05-24 | + 扩展系统 Skills/MCP 前缀 `skills:*` `mcp:*` `extensions:*` |
| 2.0 | 2026-05-23 | + R6 P2P优先 + R7 消息不可篡改 |
| 1.0 | 2026-05-23 | 初始版本 R1-R5 |
