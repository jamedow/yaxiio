# 雅溪 Yaxiio — 开发历史

## 版本演进

### v1.0 (2026-05-27) — 首次发布
- 五层 MCP 流水线架构落地 (L1-L5)
- 宪法审查机制 (ALLOWED/DELEGATED/REJECTED/DEGRADED)
- Neuron Agent 运行时 (Redis Pub/Sub + LLM + bash tools)
- 双 Guardian 守护 (PM2 + leader 选举)
- 7 个 Agent 类型: 审计官/翻译官/售前经理/品牌策略师/UI设计师/前端工程师/商务经理
- Redis 消息总线 + MongoDB 持久化
- 软件著作权申请 (雅溪五金外贸多智能体调度系统)

### v1.04 — 性能优化
- LLMAdapter 异步调用
- 六大优化引擎 (TaskAnalyzer/AutoScaler/ReliableComm/ABTester/AgentFailover/LLMRouter)
- A2A 协议适配层

### v1.06 — 宪法强化
- Constitution 从建议变为硬编码强制
- Arsenal 工具注册表 (16 工具)
- Commander 禁止直接执行业务操作
- force_sandbox 降级模式
- 白名单: 6 个系统管理操作

### v1.07 — 模块化重构
- WorkflowEngine 从 1384 行拆分为 8 个模块
- L3/L4 MCP 协调层完全落地
- 四道代码审查防火墙 (代码/架构/安全/测试)

### v1.10 — 五层验证
- solar-farm 五语内容修复全链路验证
- 发现 351 处语言混杂, 24 处截断
- 任务路由类操作 (brand/audit/translate/redesign) 全链路通过
- 内容修复类操作走直接脚本, 需增加 content_fix 模板

### v2.0 — 双守护架构
- PM2 (最外层) → Commander Guard (AI修复层) → Commander
- Guard 职责: 健康检查 → 故障诊断 → 自动修复 → 重启 Commander
- 启动顺序: Redis → MongoDB → PM2(Guard) → Guard自启Commander → Dashboard

### v2.3 — Commander V3 + 容器化
- 三个 Commander 收敛: yaxiio.py(宪法)/commander.py(六大引擎)/gateway.py(WS/HTTP)
- SessionManager: 会话与连接分离, 离线消息队列, 多端互通
- WebSocket:3398 + HTTP:3399
- Commander 容器镜像

### v2.3.1 (2026-05-29) — 生产就绪
- MongoDB → SQLite 迁移 (sqlite_store.py + FakeMongoDB)
- commander_v2/v3 降级为 deprecation wrapper
- DinD 沙箱系统 (docker run 替代 目录+PM2)
- 硬编码密码清理 (0 残留)
- Redis RESP2 全局兼容 (30+ 文件 protocol=2)
- 结构化日志系统 (TraceLogger)
- GitHub Actions CI
- 仓库拆分: Yaxiio(开源) / LightingMetal(网站)

## 早期探索 (2026-05-22~27)

### 5月22日 — 审计引擎验证
- 第一次全站审计: 4,105 页面 × 5 语言
- 发现 fr 语言完全缺失, 中文残留问题
- 审计引擎 v3.1 落地

### 5月23-24日 — Agent 系统设计
- 能力卡片系统 (YAML → AgentFactory)
- 四象限分类 (Core/Strategic/Utility/Ephemeral)
- 三层适配器 (Schema/字段映射/快照)
- 人类评分系统 + HybridScorer

### 5月25-26日 — 工作流 + A/B 测试
- 复杂任务 LLM 拆解为子任务 DAG
- 无依赖子任务并行发射 (ThreadPoolExecutor)
- A/B 测试零 token 路由
- 自进化: L5 分析失败模式 → 生成工具脚本

### 5月27日 — Yaxiio Desktop 构想
- SQLite-only 模式 (无 Redis/MongoDB)
- Tauri 桌面壳 + 系统托盘
- 多 Yaxiio 联邦 (MCP 协议互联)

### 5月28日 — 全站内容修复
- 5 行业 × 4 子任务 = 20 个 Agent 输出
- 17 处 MongoDB UI 标签修复
- MongoDB → Redis 同步 (~40 万字段)
- L3/L2 页面渲染修复 (solar-farm)

## 项目里程碑

| 日期 | 事件 |
|------|------|
| 5月22日 | 首次全站审计 |
| 5月24日 | Agent 能力卡片系统设计 |
| 5月27日 | 软著申请 + v1.0 发布 |
| 5月28日 | 全站内容修复自主完成 (7,048 entries) |
| 5月29日 | 生产就绪发布 (DinD 沙箱 + 结构化日志 + CI) |

## 源码统计

| 指标 | 数值 |
|------|:--:|
| Python 文件 | 50+ |
| 总代码行数 | ~20,000 |
| 最大单文件 | agent_lifecycle_v2.py (1,694行) |
| 核心引擎 | yaxiio.py (854行) |
| 测试覆盖 | 2 个文件, 宪法 + 日志 |

## 参考

- [架构设计](ARCHITECTURE.md)
- [设计决策](DESIGN.md)
- [五层架构验证报告 (v1.10)](../.pi/blackboard/reports/yaxiio_5layer_report.md)
- [全站审计修复报告 (2026-05-28)](../.pi/blackboard/reports/full-audit-20260528.md)
- [软件著作权信息](COPYRIGHT.md)
