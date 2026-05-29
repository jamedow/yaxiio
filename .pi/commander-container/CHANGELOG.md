# Changelog

All notable changes to the Commander project.

## [2.3.0] — 2026-05-24

### Added — 动态扩展系统

- **SkillManager** (`skill_manager.py`): Skill 生命周期管理器
  - npm / github / local 三源安装/卸载
  - Redis 注册表 + Agent 粒度启用/禁用
  - `LocalSkillAdapter` 引导已有本地 Skill 到 Redis
  - 关键词搜索匹配

- **MCPManager** (`mcp_manager.py`): MCP Server 生命周期管理器
  - mcp.json 配置文件 CRUD
  - Redis 注册表 + Pub/Sub 通知 Pi MCP Adapter 热重载
  - 健康检查（多策略探测）
  - `MCPBootstrap` 引导已有配置

- **ExtensionRouter** (`extension_router.py`): 扩展路由决策器
  - 消费 AgentDesigner 输出 → 检测 Skill/MCP/Agent 缺口
  - 决策策略：install_skill / register_mcp / create_agent / create_skill_blueprint
  - Risk 分级：auto（自动执行）/ manual（生成蓝图待审核）
  - capability → provider 倒排索引
  - `build_extension_router()` 便捷工厂函数

- **CommanderV2 集成**:
  - `enable_extensions` 参数控制
  - `_bootstrap_extension_registry()` 启动时同步已有资源
  - `handle_task()` 中异步扩展分析
  - `_run_async()` 同步/异步兼容工具

### Changed

- CommanderV2: 新增 `_run_async()` 静态方法
- CommanderV2: `__init__` 新增 `enable_extensions` 参数
- 经验模式：新增 CMD-014~015（LLM路由相关）

---

## [2.3.0] — 2026-05-23

### Added — LLM 智能路由 + A2A 协议层

- **LLMRouter** (`llm_router.py`): OpenAI 兼容 LLM 语义路由
  - 支持 DeepSeek / OpenAI / 通义千问
  - LLM 不可用时 fallback 到规则路由
  - `RouteABTester` 规则 vs LLM 24h A/B 自动决策

- **A2A 协议适配层** (`a2a_protocol.py`):
  - `A2AAdapter`: Redis ↔ A2A 双向转换
  - `AgentCard`: 角色 → JSON Schema（6种预置）
  - `AgentDiscovery`: 能力注册 → 倒排索引 → 模糊匹配

---

## [2.2.0] — 2026-05-23

### Added — 故障转移 + 五级降级 + Sentinel

- **AgentFailover** (`failover.py`):
  - 心跳监测 30s → 备选链式切换 → 降级模板
  - 连续3次失败自动触发
  - `RedisHAWrapper`: Sentinel 读写分离 + 自动故障切换

- **TaskDegradation** (`failover.py`):
  - L0~L4 五级降级策略
  - 按任务类型 + 可用Agent动态判定
  - `Dashboard v2` 增强可观测性 + 五条告警规则

---

## [2.1.0] — 2026-05-23

### Added — 四大优化引擎 + 生命周期管理

- **TaskAnalyzer** (`task_analyzer.py`): 任务指纹去重 + 启发式拆分
- **AutoScaler** (`auto_scaler.py`): 按队列深度弹性扩缩容
- **ReliableComm** (`reliable_comm.py`): 双通道通信 + ACK确认
- **ABTester** (`ab_tester.py`): A/B测试自进化策略
- **AgentLifecycleManagerV2** (`agent_lifecycle_v2.py`):
  - 四象限分级（Core/Strategic/Utility/Ephemeral）
  - `AgentDesigner`: LLM 驱动能力规格设计
  - `SelfEvolvingCommander`: 历史模式分析 + 自我优化
  - `SafetyBoundary`: 安全边界

---

## [2.0.0] — 2026-05-23

### Added — P2P 扁平通信

- Agent 间直连协作（`replyTo` 字段）
- `forward` / `request_help` 函数
- 4种消息类型扩展为7种
- 不经过 Commander 直接 P2P 通信

---

## [1.0.0] — 2026-05-23

### Initial Release

- Redis Pub/Sub 消息总线
- PM2 进程管理
- Dashboard v1（http.server, port 3002）
- 3个核心 Agent：翻译官 / 商务经理 / 售前经理
- 并行任务分派
- 故障恢复
- 自我进化日志

---

格式基于 [Keep a Changelog](https://keepachangelog.com/)。
版本号遵循 [Semantic Versioning](https://semver.org/)。
