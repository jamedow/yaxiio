# Yaxiio 环境变量参考手册 v0.2.6

> 所有环境变量按「必填 / 推荐 / 可选」三级分类。
> 标记 `⚠️ 必填` 的变量不设置会导致启动失败。

---

## 一、必填变量

不设这些 Yaxiio 无法启动。

| 变量 | 用途 | 示例值 |
|------|------|--------|
| `REDIS_PASSWORD` | Redis 认证密码 | `your-redis-password` |
| `LLM_API_KEY` | LLM API 密钥（DeepSeek 或 OpenAI 兼容） | `sk-xxxxxxxxxxxxxxxx` |

> **说明**：`REDIS_PASSWORD` 是 Agent 间通信总线的钥匙，所有组件（Commander、Agent、Gateway、Dashboard）共用同一个 Redis 实例。
> `LLM_API_KEY` 驱动任务拆分、智能路由、自我进化等全部 LLM 功能。

---

## 二、推荐配置

生产环境建议显式设置，有内置默认值但可能不适合你的环境。

### 2.1 基础设施

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `REDIS_PORT` | `6379` | Redis 端口 |
| `MONGO_URI` | `mongodb://127.0.0.1:27017/` | MongoDB 连接串（含认证） |
| `MONGO_DATABASE` | `lightingmetal` | MongoDB 数据库名 |

### 2.2 LLM

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM API 地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `DEEPSEEK_API_KEY` | (空) | DeepSeek API Key（与 `LLM_API_KEY` 二选一） |

### 2.3 Gateway（WebSocket / HTTP）

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `WS_PORT` | `3398` | WebSocket 端口 |
| `WS_HOST` | `0.0.0.0` | WebSocket 绑定地址 |
| `HTTP_PORT` | `3399` | HTTP API 端口 |
| `WS_PING_INTERVAL` | `15` | WebSocket 心跳间隔（秒） |
| `WS_PING_TIMEOUT` | `10` | WebSocket 心跳超时（秒） |

### 2.4 Dashboard

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `DASHBOARD_V2_PORT` | `3003` | Dashboard Web UI 端口 |
| `COMMANDER_TUI_PORT` | `7681` | 终端 TUI 端口 |

---

## 三、可选变量

不设就用默认值，不影响核心功能。

### 3.1 会话管理（Gateway V3）

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `SESSION_TOKEN_SECRET` | 随机生成 | HMAC 签名密钥（多实例部署必须统一） |
| `SESSION_MAX_OFFLINE_QUEUE` | `1000` | 单会话离线消息上限 |
| `SESSION_MAX_HISTORY_REDIS` | `500` | Redis 中保留的最近消息数 |
| `SESSION_OFFLINE_ARCHIVE_HOURS` | `24` | 离线超时自动归档（小时） |

### 3.2 五层 MCP Pipeline

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `L1_PERCEPTION_PORT` | `3401` | L1 感知层端口 |
| `L2_PLANNING_PORT` | `3402` | L2 规划层端口 |
| `L3_COORDINATION_PORT` | `3403` | L3 调度层端口 |
| `L4_EXECUTION_PORT` | `3404` | L4 执行层端口 |
| `L5_EVOLUTION_PORT` | `3405` | L5 进化层端口 |
| `ORCHESTRATOR_HOST` | `127.0.0.1` | 跨层编排器地址 |
| `ORCHESTRATOR_PORT` | `3300` | 跨层编排器端口 |

### 3.3 监督树（故障恢复）

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `SUPERVISION_STRATEGY` | `one_for_one` | 监督策略 |
| `MAX_RESTARTS_PER_PERIOD` | `5` | 周期内最大重启次数 |
| `RESTART_PERIOD_SECONDS` | `60` | 重启计数周期（秒） |

### 3.4 评分与路由

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `SCORE_THRESHOLD` | `6` | 任务评分通过阈值 |
| `LLM_THRESHOLD_TOKEN` | `500` | 低于此 token 数使用规则路由 |

### 3.5 自进化

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `TEXTGRAD_ENABLED` | `true` | 启用 TextGrad 优化 |
| `AFLOW_ENABLED` | `true` | 启用 AFlow 优化 |
| `MIPRO_ENABLED` | `true` | 启用 MIPROv2 优化 |

### 3.6 Skill 管理

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `SKILL_DIR` | `/app/data/skills` | Skill 定义目录 |
| `SKILL_MAX_CHARS` | `2200` | 单个 Skill 最大字符数 |
| `CONTEXT_MAX_CHARS` | `1375` | 上下文最大字符数 |

### 3.7 审计

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `AUDIT_ENABLED` | `true` | 启用审计日志 |
| `AUDIT_LOG_LEVEL` | `INFO` | 审计日志级别 |
| `AUDIT_BATCH_SIZE` | `50` | 审计批处理大小 |

### 3.8 RAG 知识库（需 Redis Stack）

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `RAG_REDIS_HOST` | `redis-stack` | RAG Redis Stack 地址 |
| `RAG_REDIS_PORT` | `6379` | RAG Redis 端口 |
| `RAG_REDIS_PASSWORD` | (空) | RAG Redis 密码 |
| `ENABLE_RAG` | `false` | 启用 RAG |

---

## 四、快速开始

### 4.1 最小配置（开发环境）

```bash
# 仅需两个变量即可启动
export REDIS_PASSWORD="dev-pass"
export LLM_API_KEY="sk-your-key"
```

此时 Yaxiio 假定 Redis 在 `127.0.0.1:6379`，LLM 走 DeepSeek。

### 4.2 Docker Compose 配置

复制模板文件并填入实际值：

```bash
cp deploy/commander/.env.example .env
# 编辑 .env，修改 LLM_API_KEY 和 REDIS_PASSWORD
docker compose up -d
```

### 4.3 验证

```bash
# 检查 Dashboard 是否正常
curl http://localhost:3003/dashboard

# 检查 Gateway WebSocket
wscat -c ws://localhost:3398
```

---

## 五、安全提醒

- **绝对不要**把真实密码写入 `.env.example` 或提交到 Git
- `.env` 文件已在 `.gitignore` 中排除
- 生产环境建议使用 Docker Secrets 或 HashiCorp Vault 管理密钥
- `REDIS_PASSWORD` 和 `LLM_API_KEY` 是最核心的两个凭证，泄露等于系统完全暴露
