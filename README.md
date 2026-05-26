# 雅溪 Yaxiio

> 五金外贸B2B智能调度系统 · AGPLv3

雅溪（Yaxiio）是 LightingMetal 独立站的 AI 智能调度系统。基于五层模块化架构（基础组件 → 智能体 → 工作流 → 评估 → 进化），通过 Redis Pub/Sub + MCP 协议实现多 Agent 协作，支持自主审计、代码修复、任务拆解和自进化。

## 架构

```
yaxiio.py                    # 主入口
├── modules/
│   ├── layer1/              # 基础组件层: Redis/MongoDB/MCP/Skill
│   ├── layer2/              # 智能体层: Agent工厂/生命周期/模型路由
│   ├── layer3/              # 工作流层: 任务拆解/依赖分析/调度路由
│   ├── layer4/              # 评估层: 自动评分/审计日志/失败检测
│   └── layer5/              # 进化层: 提示词优化/A/B测试/技能生成
├── core/                    # Commander 核心模块
├── guard/                   # 守护进程 (PM2 → Guard → Commander)
├── agents/                  # Agent 运行时
└── deploy/                  # 部署脚本
```

## 快速开始

```bash
docker run -d --name yaxiio \
  -p 3003:3003 -p 3398:3398 \
  yaxiio:latest
```

## 许可证

GNU Affero General Public License v3.0
