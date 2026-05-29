# 雅溪 Yaxiio — Agent 操作系统内核

> 不是又一个 Agent 框架。是让一群 Agent 自我管理、自我进化的元系统。

Yaxiio 回答的问题是：**当你有 10 个 AI Agent 一起干活时，谁来管它们？** 答案不是"你"——是宪法约束、五层流水线、和自动进化闭环。

## 为什么是 Yaxiio？

| 你的痛点 | Yaxiio 的方案 |
|---------|-------------|
| Agent 记忆越用越乱 | **模板克隆**：每任务新实例，上下文零泄露 |
| Commander 越权直接改数据 | **宪法约束**：硬编码规则，Commander 不可越权（业界独有） |
| 任务质量不稳定 | **五层流水线 + 自检循环**：L5 自动评分 → 差距分析 → 重试优化 |
| Agent 崩溃无人知 | **双层守护**：PM2 → Guard-Primary + Guard-Secondary，故障诊断+自动修复 |
| 代码执行不安全 | **DinD 沙箱**：Docker-in-Docker 容器级隔离 |

## 快速开始

```bash
git clone git@github.com:jamedow/yaxiio.git
cd yaxiio

# 5 分钟快速上手
cat docs/QUICK_START.md
```

## 不是聊天工具

Yaxiio 不是在聊天框里一问一答。她在后台默默干活：

```
09:00  自动扫描竞品价格 → 更新数据库 → 推送报告
14:00  检测到 17 处翻译过期 → 自动修复 → 同步部署
周末   收到客户邮件 → 提取需求 → 生成报价 → 发送
```

你不需要找她。她在帮你赚钱。

## 文档体系

### 🧭 先读这两篇

| 文档 | 回答的问题 |
|------|-----------|
| [设计哲学](docs/DESIGN_PHILOSOPHY.md) | 为什么长这样？五层 vs 三层、隔离沙箱、双守护 |
| [快速上手](docs/QUICK_START.md) | 5 分钟第一个 Agent → 10 分钟定制 → 15 分钟自进化 |

### 🏗️ 深入理解

| 文档 | 内容 |
|------|------|
| [架构设计](docs/ARCHITECTURE.md) | 五层 MCP + 宪法框架 + Agent 系统 v2 |
| [设计决策](docs/DESIGN.md) | 8 个核心决策及取舍 |
| [宪法系统](docs/CONSTITUTION.md) | ALLOWED / DELEGATED / REJECTED / DEGRADED |
| [状态机](docs/STATE_MACHINE.md) | 11 状态任务机 + Agent 生命周期 + 故障恢复 |
| [记忆系统](docs/MEMORY_SYSTEM.md) | 三层架构 + 隔离机制 + 进化流程 |

### 🔌 扩展与集成

| 文档 | 内容 |
|------|------|
| [能力卡片规范](docs/CAPABILITY_CARD_SPEC.md) | 六要素 + 四象限 + 完整示例 |
| [API 文档](docs/API.md) | WebSocket + HTTP + Redis Pub/Sub |
| [沙箱系统](docs/SANDBOX.md) | DinD 容器隔离 |
| [结构化日志](docs/TRACE_LOGGING.md) | TraceLogger 全链路追踪 |

### 📋 运维

| 文档 | 内容 |
|------|------|
| [部署指南](docs/DEPLOYMENT.md) | 生产 Docker 部署 |
| [环境变量](docs/ENVIRONMENT.md) | 全部配置项 |
| [版本历史](docs/HISTORY.md) | v1.0 → v3.1 |

## 许可证

AGPL v3 — [详见 LICENSE](LICENSE)

---

> **Yaxiio 不是一个外贸工具，是一个通用的 Agent 操作系统内核。**
> 换一套能力卡片，她从外贸系统变成法律、医疗、教育系统。壳不变，大脑换。
