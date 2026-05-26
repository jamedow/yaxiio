# 雅溪 Yaxiio

> AI 智能调度系统 · 五层模块化架构 · AGPLv3

Yaxiio 是一个通用的 AI 智能调度系统。通过 Redis Pub/Sub + MCP 协议实现多 Agent 协作，支持任务拆解、自主审计、自进化、沙箱演习。

## 快速开始

```bash
docker pull yaxiio:latest
docker run -d --name yaxiio \
  -p 3003:3003 -p 3398:3398 \
  -e DEEPSEEK_API_KEY=sk-xxx \
  yaxiio:latest
```

## 架构

```
yaxiio.py                    # 主入口
├── L1 基础组件: Redis · SQLite · MCP · Skill热加载 · 向量DB
├── L2 智能体:   Agent工厂 · 生命周期 · RAG · 分层记忆 · 多Provider
├── L3 工作流:   任务状态机 · 并行调度 · 断点续传 · 条件分支
├── L4 评估:     LLM-as-Judge · 全链路追踪 · Prometheus
└── L5 进化:     GEPA优化 · A/B测试 · DSPy互补
```

## 许可证

GNU Affero General Public License v3.0
