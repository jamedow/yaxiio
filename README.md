# 雅溪 Yaxiio

> AI 智能调度系统 · AGPLv3

Yaxiio 是一个通用的 AI 智能调度系统。基于五层模块化架构，通过 Redis Pub/Sub + MCP 协议实现多 Agent 协作，支持任务拆解、自主审计、自进化。适用于任何需要多 Agent 协同的 AI 应用场景。

## 架构

```
yaxiio.py                    # 主入口 (97行)
├── modules/
│   ├── layer1/              # 基础组件: Redis/MongoDB/MCP/Skill
│   ├── layer2/              # 智能体: Agent工厂/生命周期/模型路由
│   ├── layer3/              # 工作流: 任务拆解/依赖分析/调度
│   ├── layer4/              # 评估: 自动评分/审计日志/失败检测
│   └── layer5/              # 进化: 提示词优化/A/B测试/技能生成
├── core/                    # Commander 核心 (22模块)
├── guard/                   # 两层守护 (PM2 → Guard → Commander)
└── agents/                  # Agent 运行时
```

## 快速开始

```bash
docker run -d --name yaxiio \
  -p 3003:3003 \
  yaxiio:latest
```

## 许可证

GNU Affero General Public License v3.0 — 自由使用、修改、分发，网络服务也需开源。
