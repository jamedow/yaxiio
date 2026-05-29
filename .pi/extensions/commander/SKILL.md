# Commander Global Event Router — Pi 扩展

**版本**: v2.3.1  
**类型**: Pi Extension (TypeScript)  
**许可**: Apache 2.0  
**依赖**: Redis ≥ 6.0, Python Commander ≥ v2.3  

Commander 全局事件路由器是一个 Pi 扩展，将 Commander 多Agent调度引擎注入到 **pi 编码代理框架**的每个会话生命周期事件中。它监听所有 LLM 调用和工具执行，智能决定是自行处理、分派给专业 Agent，还是交由当前 Agent 执行。

---

## 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      pi coding agent                        │
│  ┌──────────────┐  事件 Hook    ┌─────────────────────────┐ │
│  │ Agent 会话    │──────────────▶│  Commander Extension     │ │
│  │  (LLM/Tools) │◀──────────────│  (index.ts)             │ │
│  └──────────────┘  注入结果     └───────────┬─────────────┘ │
└─────────────────────────────────────────────┼───────────────┘
                                              │ Redis Pub/Sub
┌─────────────────────────────────────────────┼───────────────┐
│                     Redis                    │               │
│  ┌─────────────────┐  ┌───────────────────┐ │               │
│  │ Agent Pool       │  │ Task Queue        │◀┘               │
│  │ (Registry Hash)  │  │ (List)            │                 │
│  └────────┬────────┘  └────────┬──────────┘                 │
│           │                    │                             │
│  ┌────────▼────────────────────▼──────────┐                 │
│  │        Python Commander Backend          │                 │
│  │  - agent_lifecycle_v2                   │                 │
│  │  - extension_router                     │                 │
│  │  - llm_router / task_analyzer           │                 │
│  └──────────────┬──────────────────────────┘                 │
└─────────────────┼───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                   Professional Agents                        │
│  翻译官 │ 商务经理 │ 售前经理 │ 审计官 │ dev-agent │ ...     │
└─────────────────────────────────────────────────────────────┘
```

## 事件处理流程

| 事件 | Hook | 处理逻辑 |
|------|------|---------|
| **session_start** | 初始化连接 | 连接 Redis，同步 Agent 池，显示就绪状态 |
| **input** | 智能路由 | `recognizeIntent()` → `self` / `dispatch` / `handle` |
| **tool_call** | 安全治理 | `governor.audit()` → allow / deny / warn / throttle |
| **context** | (可选) 注入 | 将 Commander 调度结果注入 LLM 上下文 |
| **session_shutdown** | 清理 | 关闭 Redis 连接，停止心跳定时器 |

## 三大路由模式

### 1. Self — 不干预 (Pass-through)

简单对话如问候、闲聊或极短输入 → Commander 不干预，由当前 Agent 自行处理。

```typescript
// 示例
"你好" → self (pass-through)
"什么是热镀锌？" → self (简短问答)
```

### 2. Dispatch — 分派给专业 Agent

识别到明确的任务意图 → 通过 Redis Pub/Sub 分派给目标 Agent。

支持 **12 个意图类别** + **快捷命令**：

| 意图 | 目标 Agent | 触发词示例 |
|------|-----------|-----------|
| translation | 翻译官 | 翻译, translate, 多语言 |
| business | 商务经理 | 报价, quotation, 订单, 沙特, 光伏 |
| presales | 售前经理 | 规格, 方案, 对比, ISO |
| development | dev-agent | build, 开发, 代码, API, 部署 |
| content | CMS工程师 | 文章, 白皮书, 发布 |
| audit | 审计官 | 审计, review, 抽检 |
| productSearch | 产品搜索Agent | 产品, 搜索, 规格 |
| design | UI设计师 | 设计, UI, 配色 |
| infrastructure | 架构运维工程师 | 部署, CI/CD, 安全 |

**快捷命令** (直接路由，跳过意图识别)：

```
/translate  → 翻译官
/报价       → 商务经理
/tech       → 售前经理
/audit      → 审计官
/build      → dev-agent
/deploy     → dev-agent
/design     → UI设计师
/product    → 产品搜索Agent
/seo        → SEO工程师
/infra      → 架构运维工程师
/cms        → CMS工程师
```

### 3. Handle — Commander 自行处理

Commander 自带 LLM 能力，可直接处理一些不涉及专业知识的请求。

## 安全治理

Commander 的 Governor 层在每次 `tool_call` 事件中审计：

| 规则类型 | 示例 | 操作 |
|---------|------|------|
| **allow** | `read`, `edit`, `write`, `bash`, `mcp`, `subagent` | ✅ 通过 |
| **deny** | `rm -rf /`, `mkfs`, `sudo`, `passwd` | ❌ 阻止 |
| **warn** | 修改 `.env`, `.ssh/`  | ⚠️ 警告 |
| **throttle** | `bash` ≤ 30次/分钟, `mcp` ≤ 20次/分钟 | 🐌 限流 |

## 命令

| 命令 | 说明 |
|------|------|
| `/commander` | 查看 Commander 状态 (总拦截数、调度数、阻止数、Agent 数) |
| `/commander agents` | 列出所有活跃 Agent |
| `/commander dispatch <agent> <task>` | 手动分派任务 |

## 自定义工具 (LLM 可调用)

| 工具名 | 说明 |
|--------|------|
| `commander_dispatch` | LLM 可主动将任务分派给指定 Agent |

## 快速部署

### 1. 安装依赖

```bash
cd .pi/extensions/commander
npm install
```

### 2. 启动守护进程 (PM2)

```bash
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup
```

### 3. 以扩展模式运行 pi

```bash
pi --extension .pi/extensions/commander
```

### 4. 作为默认扩展 (全局)

在 `~/.pi/agent/settings.json` 中配置：

```json
{
  "extensions": [
    "/absolute/path/to/.pi/extensions/commander"
  ]
}
```

### Docker 部署

```bash
# 使用 Commander Docker 部署方案
docker compose -f deploy/commander/docker-compose.yml up -d
docker compose -f deploy/commander/docker-compose.yml --profile rag up -d
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `REDIS_URL` | `redis://127.0.0.1:6379` | Redis 连接 URL |
| `REDIS_PASSWORD` | — | Redis 密码 |
| `LLM_API_KEY` | — | LLM API 密钥 |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | LLM 基础 URL |
| `LLM_MODEL` | `deepseek-chat` | LLM 模型 |

## 文件清单

```
.pi/extensions/commander/
├── index.ts              # 主扩展入口 (438行) — 事件监听 + 命令注册
├── router.ts             # 智能路由核心 (306行) — 意图识别 + 路由策略
├── agent-pool.ts         # Agent 池管理 (334行) — Redis Pub/Sub 通信
├── governance.ts         # 安全治理层 (257行) — 审计 + 限流 + 阻止
├── ecosystem.config.cjs  # PM2 守护进程配置
├── package.json          # 依赖声明
└── node_modules/         # 运行时依赖
```

## 集成验证

```bash
# 检查扩展是否加载
pi --extension .pi/extensions/commander <<< '/commander status'

# 预期输出:
# Commander 全局路由器状态
# | 指标 | 值 |
# | 运行时间 | 0s |
# | 已拦截指令 | 0 |
# | 活跃 Agent | 3 |
# | Redis 连接 | ✅ |
```

## 扩展路线图

- [ ] LLM 语义路由 (替换关键词匹配)
- [ ] Commander 自行处理复杂请求 (LLM 直接回复)
- [ ] A/B 测试路由策略对比
- [ ] Agent 懒加载 (按需启动)
- [ ] 跨会话任务持久化
- [ ] WebSocket 实时 Dashboard
