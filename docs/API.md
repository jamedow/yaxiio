# Yaxiio API 文档

## WebSocket API (port 3398)

JSON 消息协议，每条消息包含 `action` 字段：

### register — 注册新会话

```json
{"action":"register", "client_type":"browser", "fingerprint":"..."}
→ {"type":"registered", "session_token":"hmac_sha256..."}
```

### connect — 重连已有会话

```json
{"action":"connect", "session_token":"...", "client_id":"...", "last_seq":0}
→ {"type":"connected", "offline_messages":[], "history":[...]}
```

### heartbeat — 心跳 (30s)

```json
{"action":"heartbeat"}
→ {"type":"heartbeat_ack"}
```

### dispatch — 提交任务

```json
{
  "action":"dispatch",
  "task_type":"audit",
  "payload":{"action":"site_audit","target":"power"}
}
```

### destroy — 销毁会话

```json
{"action":"destroy"}
→ {"type":"destroyed","session_token":"..."}
```

## HTTP API (port 3399)

### 健康检查

```
GET /health
→ {"status":"ok","redis":true,"uptime_seconds":1234}
```

### 系统指标

```
GET /metrics
→ {"commander":true,"guardian":true,"active_tasks":3,"uptime_seconds":1234}
```

### 链路追踪

```
GET /trace/{trace_id}
→ {"trace_id":"a1b2c3d4","count":15,"logs":[...]}
```

### 订阅频道 (Redis Pub/Sub)

Commander 同时监听两个频道：
- `yaxiio:agent:commander` — 主要任务频道
- `lightingmetal:agent:commander` — LightingMetal 集成频道

消息格式：

```json
{
  "type": "task",
  "taskId": "task-123",
  "from": "user",
  "to": "commander",
  "replyTo": "lightingmetal:agent:zelda",
  "payload": {"action": "site_audit", "target": "power"}
}
```
