# 雅溪 Yaxiio — 多智能体自主调度系统

Yaxiio 是一个**通用的 Agent 操作系统内核**，不是外贸工具。

## 核心创新

| 特性 | 说明 |
|------|------|
| ⚖️ **宪法约束** | 硬编码规则，Commander 不可越权执行 (业界独有) |
| 🔄 **五层流水线** | L1感知→L2规划→L3调度→L4执行→L5评估 |
| 🧠 **模板克隆** | 每任务新 Agent 实例，上下文零泄露 |
| 📊 **A/B 自进化** | L5 低分自动触发 research + retry + 策略优化 |
| 🛡️ **五级降级** | crash→restart, low_quality→医生诊断, 高危→sandbox |
| 🏗️ **DinD 沙箱** | Docker-in-Docker 容器级隔离 |

## 快速开始

```bash
git clone git@codeup.aliyun.com:69ea42b37b6e0a01296310b9/AI/Yaxiio.git
cd Yaxiio
cat docs/README.md  # 开发者入门
```

## 文档

| 文档 | 说明 |
|------|------|
| [开发者入门](docs/README.md) | 5 分钟了解 + 目录结构 + 启动 |
| [架构设计](docs/ARCHITECTURE.md) | 五层 MCP + 宪法 + 设计模式 |
| [设计决策](docs/DESIGN.md) | 8 个核心设计决策及取舍 |
| [宪法系统](docs/CONSTITUTION.md) | ALLOWED/DELEGATED/REJECTED/DEGRADED |
| [API 文档](docs/API.md) | WebSocket + HTTP + Redis Pub/Sub |
| [部署指南](docs/DEPLOYMENT.md) | 生产 Docker 部署 |
| [环境变量](docs/ENVIRONMENT.md) | 全部配置项 |
| [结构化日志](docs/TRACE_LOGGING.md) | TraceLogger 使用 |
| [沙箱系统](docs/SANDBOX.md) | DinD 容器隔离 |
| [开发历史](docs/HISTORY.md) | v1.0 → v2.3.1 版本演进 |

## 许可证

AGPL v3 — [详见 LICENSE](LICENSE)
