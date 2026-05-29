# 雅溪 Yaxiio — 开发者快速入门

## 5 分钟了解 Yaxiio

Yaxiio 是一个**多智能体自主调度系统**，核心思想：**Commander 只做编排，Agent 负责执行**。

```
用户请求 → 宪法审查 → L1感知 → L2规划 → L3调度 → L4执行 → L5评估 → 回复
              ⚖️        🧠       📋       🚀       ⚙️       📊
```

## 项目结构

```
yaxiio/
├── .pi/skills/commander/    ← 核心引擎
│   ├── yaxiio.py            ← Commander 主程序 (宪法+流水线)
│   ├── gateway.py           ← WebSocket/HTTP 统一入口
│   ├── workflow_engine.py   ← L1→L5 编排引擎
│   ├── constitution.py      ← 宪法审查
│   ├── neuron.py            ← Agent 运行时
│   ├── sandbox_manager.py   ← DinD 沙箱管理
│   ├── trace_logger.py      ← 结构化日志
│   ├── task_state_machine.py← 任务状态机
│   ├── session_manager.py   ← 会话管理
│   ├── commander.py         ← CommanderV2 (六大引擎)
│   ├── pi_guardian_v3.py    ← 守护进程
│   ├── modules/             ← L1-L5 功能模块
│   ├── tools/               ← Agent 工具集
│   └── layers/              ← MCP Server 实现
├── .pi/docker/production/   ← 生产 Docker 镜像
├── .pi/docker/sandbox/      ← 沙箱 Docker 镜像
├── .pi/agents/              ← Agent 定义
├── .pi/extensions/          ← Pi 编辑器扩展
└── docs/                    ← 文档
```

## 一分钟跑起来

```bash
# 构建生产镜像
cd .pi/docker/production && docker build -t yaxiio:prod .

# 启动
docker run -d --name yaxiio \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v $(pwd):/opt/yaxiio \
  -p 3398:3398 -p 3399:3399 \
  yaxiio:prod

# 验证
curl http://localhost:3399/health
curl http://localhost:3399/metrics
```

## 核心概念

| 概念 | 文件 | 说明 |
|------|------|------|
| **宪法** | `constitution.py` | 硬编码规则约束，Commander 不能越权执行 |
| **五层流水线** | `workflow_engine.py` | L1感知→L2规划→L3调度→L4执行→L5评估 |
| **Arsenal** | `yaxiio.py:Arsenal` | Commander 的工具注册表，16个工具 |
| **Neuron** | `neuron.py` | Agent 运行时，LLM+bash 工具执行 |
| **Guardian** | `pi_guardian_v3.py` | PM2 守护，30s 健康检查，自动修复 |
| **Sandbox** | `sandbox_manager.py` | Docker-in-Docker 容器隔离 |
| **TraceLogger** | `trace_logger.py` | 结构化日志 `[时间] 级别 [trace] [模块] [方法] 操作 \| params` |
| **ResourcePool** | `resource_pool.py` | LLM API Key + 模型分配，三源降级 |

## 启动链路

```
entrypoint.sh
  → Redis (127.0.0.1:6379)
  → PM2 → pi_guardian_v3.py (守护)
           → Commander.run() (yaxiio.py)
                ├── 单实例锁 SETNX
                ├── BoundedThreadPool (5 workers)
                ├── WorkflowEngine
                ├── L1-L5 MCP Server 子进程
                └── Redis Pub/Sub 主循环
  → Gateway (WebSocket:3398 + HTTP:3399)
```

## 文档索引

- [架构设计](ARCHITECTURE.md) — 五层 MCP + 宪法 + BoundedThreadPool
- [设计决策](DESIGN.md) — 为什么这样设计
- [宪法系统](CONSTITUTION.md) — ALLOWED/DELEGATED/REJECTED/DEGRADED
- [API 文档](API.md) — WebSocket + HTTP 协议
- [部署指南](DEPLOYMENT.md) — 生产部署
- [环境变量](ENVIRONMENT.md) — 全部配置项
- [结构化日志](TRACE_LOGGING.md) — TraceLogger 使用
- [沙箱系统](SANDBOX.md) — DinD 容器隔离
- [Agent 系统](AGENT_SYSTEM.md) — 能力卡片 + 神经元
- [开发历史](HISTORY.md) — 版本演进

## 许可证

AGPL v3 — 详见 LICENSE
