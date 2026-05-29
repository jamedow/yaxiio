# 环境变量

## 必需

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | LLM API Key | 无 (必需) |
| `REDIS_PASSWORD` | Redis 密码 | `Yaxiio2026` |

## 基础设施

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `REDIS_HOST` | Redis 地址 | `127.0.0.1` |
| `REDIS_PORT` | Redis 端口 | `6379` |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 默认模型 | `deepseek-chat` |

## 服务端口

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `WS_PORT` | WebSocket 端口 | `3398` |
| `WS_HOST` | WebSocket 绑定 | `0.0.0.0` |
| `HTTP_PORT` | HTTP 端口 | `3399` |
| `HEALTH_PORT` | 健康检查端口 | `3399` |

## 存储

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `YAXIO_DB` | SQLite 数据库路径 | `/opt/commander/data/yaxiio.db` |
| `YAXIO_HOME` | Commander 代码目录 | `/opt/yaxiio/.pi/skills/commander` |
| `COMMANDER_SCRIPT` | Commander 入口脚本 | `$YAXIO_HOME/yaxiio.py` |
| `GUARD_LOG_DIR` | Guard 日志目录 | `/opt/commander` |

## 日志

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_TO_REDIS` | 是否写入 Redis | `1` |
| `LOG_TO_STDOUT` | 是否输出到 stdout | `1` |
