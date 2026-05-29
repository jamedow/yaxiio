# 雅溪 Yaxiio 系统深度诊断报告 v1.0

> 评估日期：2026-05-29 | 评估范围：代码仓库 + 运行态容器 (yaxiio:v1.04) + 部署配置
> 评估方法论：OWASP SAMM v2 + Google SRE Workbook + 12-Factor App

---

## 一、现状速写

### 1.1 运行态与代码态的版本断层

| 维度 | 代码仓库 (最新) | 运行容器 (yaxiio:v1.04) |
|------|----------------|------------------------|
| 主进程 | Supervisor → commander_v2.py | `sleep infinity` + 手动 `python3 yaxiio.py` |
| 进程管理 | Supervisor 5进程 | 无，裸进程 |
| Dashboard | Flask dashboard_v2.py (3003) | 未运行 |
| WebSocket/HTTP | CommanderV3 (3398/3399) | 未运行 |
| Agent | autostart=false（待隔离系统接管） | neuron.py 手动启动 |
| Redis | 外部服务 | 内置 `redis-server` (PID 82) |
| 五层 MCP | 独立容器中的 MCP Server | 同容器内 5 个 Python 进程 |

**结论**：仓库已经演进到 v2.3 架构（Supervisor + CommanderV2/V3），但线上容器仍跑着 v1.04 的原始版本。两者之间存在一个未完成的迁移。

### 1.2 角色关系澄清

```
雅溪 Yaxiio (品牌/项目整体)
│
├── Commander (指挥官) — 编排调度角色
│   ├── commander_v2.py  — 六大优化引擎集成版 (推荐)
│   ├── commander_v3.py  — 会话分离版 (WebSocket)
│   └── yaxiio.py        — 宪法流水线版 (最早实现)
│
├── Agent 集群 (执行角色)
│   ├── 翻译官 (translator)
│   ├── 商务经理 (business)
│   └── 售前经理 (presales)
│
├── 五层 MCP Server (管道角色)
│   L1 感知 / L2 规划 / L3 调度 / L4 执行 / L5 进化
│
└── 基础设施
    Redis (消息总线) + MongoDB (持久化) + PM2/Supervisor (进程管理)
```

三个 Commander 实现不是"竞争关系"，而是 Yaxiio 项目在不同阶段的架构尝试：`yaxiio.py` 最先落地（宪法 + 五层流水线），`commander_v2.py` 在此基础上引入六大优化引擎，`commander_v3.py` 进一步加入会话分离。问题在于三者未能收敛为统一入口。

---

## 二、问题清单 (按严重性排序)

### 🔴 P0 — 阻塞级

#### P0-1: 容器使用 `sleep infinity` 作为 PID 1，健康检查永久失败

**现象**：`docker ps` 显示 `yaxiio` 容器状态为 `unhealthy` 已 48 分钟。

**根因**：当前镜像 `yaxiio:v1.04` 的 ENTRYPOINT 是 `sleep infinity`（典型的调试/开发占位），PID 1 不响应任何信号，Supervisor 未安装或未启动。healthcheck.sh 检查 supervisorctl、Dashboard HTTP、Redis——三者全失败。

**影响**：容器无法被 Docker 自动重启恢复，无法执行 `docker exec yaxiio supervisorctl` 等运维命令。

**修复**：重新构建镜像，使用 `deploy/commander/Dockerfile` 的生产配置（ENTRYPOINT → entrypoint.sh → supervisord）。

#### P0-2: Arsenal 工具注册表被重复覆盖（Bug）

**位置**：`/app/.pi/skills/commander/yaxiio.py` 第 123-166 行

```python
class Arsenal:
    def __init__(self, commander):
        self.commander = commander
        self._register_defaults()  # ← 第一次注册（15个工具，通过 _run_tool 代理）
    def _register_defaults(self):  # ← 同一个方法名，第二次定义覆盖了第一次
        ...
        self._tools = {
            "audit_codebase": lambda ... c._run_audit(tid, p),  # _run_audit 方法不存在！
            "fix_codebase":   lambda ... c._run_fix(tid, p),    # _run_fix 不存在！
            "drill_improve":  lambda ... c._run_drill(tid, p),  # _run_drill 不存在！
            "evolve_code":    lambda ... c._run_evolve(tid, p), # _run_evolve 不存在！
            "build_deploy":   lambda ... c._build_deploy(...),  # _build_deploy 不存在！
            ...
        }
```

**影响**：
- 第一次 `__init__` 调用注册了 15 个工具（通过 `_run_tool` 代理到 `tools/*.py`，这些方法是存在的）
- 紧接着 `_register_defaults()` 再次执行，**完全覆盖** `self._tools`，替换为另一组工具列表
- 新列表中的 `_run_audit`、`_run_fix`、`_run_drill`、`_run_evolve`、`_build_deploy`、`_run_diagnose`、`_run_translate_script` 在 Commander 类上**全部不存在**
- 调用这些工具时会抛出 `AttributeError`，但被 Arsenal.call() 外层 try/except 吞掉，静默失败

**修复**：删除其中一个 `_register_defaults()`，合并为唯一版本，确保所有 lambda 引用的 Commander 方法真实存在。

### 🟡 P1 — 高危级

#### P1-1: 硬编码密码散落在 18 个文件中

**涉及文件**：
- `agent-commander.py`：`password='Lt@114514!'`
- `agent.sh`：`-a 'Lt@114514!'`（3处）
- `agent-factory.sh`：`-a 'Lt@114514!'`
- `commander_v3.py`：`"Commander2024!Redis"`（硬编码默认值）
- `session_manager.py`：`"Commander2024!Redis"`（硬编码默认值）
- `pi_guardian.py`：`"Commander2024!Redis"`（硬编码默认值）
- 另有 11 个文件使用 `"$REDIS_PASSWORD"` 字符串字面量作为默认参数

**风险**：密码出现在 git 历史中，即使后续移除仍可通过 `git log -p` 检索。Docker 镜像层也会保留。

**修复方案**：
1. 所有硬编码密码替换为 `os.environ.get("REDIS_PASSWORD")`，不提供默认值
2. 如果必须提供默认值，使用 `os.environ.get("REDIS_PASSWORD", "").strip() or None` 并在为空时抛出明确异常
3. 对 git 历史中的密码执行 `git filter-branch` 或 `bfg-repo-cleaner` 清理
4. 轮换生产环境 Redis 密码

#### P1-2: Shell 脚本对特定 Docker 环境的硬编码依赖

**位置**：`agent.sh`、`agent-factory.sh`

```bash
docker exec redis-centos7 redis-cli -a 'Lt@114514!' PUBLISH ...
docker exec redis-centos7 redis-cli -a 'Lt@114514!' SUBSCRIBE ...
```

**问题**：
- 假设 Redis 容器名为 `redis-centos7`（这是 LightingMetal 主项目的容器名，不是 Commander 专用的）
- 假设 Docker 可用（在 Kubernetes 或裸机部署中不成立）
- 假设密码为 `Lt@114514!`

**修复方案**：
```bash
# 统一使用环境变量 + redis-cli 直连
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:?REDIS_PASSWORD must be set}"

redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" --no-auth-warning PUBLISH ...
```

#### P1-3: 同步/异步混用架构的并发风险

**涉及模块**：
- `commander_v2.py::_run_async()` — 在同步主循环中尝试运行异步 lifecycle 方法
- `yaxiio.py::SharedAsyncLoop` — 单例模式 + 线程不安全的 `_started` 标志
- `yaxiio.py::BoundedThreadPool` — 自制的有界线程池

**具体风险**：
1. `_run_async()` 的策略链 `get_event_loop() → run_coroutine_threadsafe() → asyncio.run()` 在 Supervisor 管理的同步 `main()` 循环中，第一个分支会检测到已有 running loop（来自 SharedAsyncLoop 后台线程），然后用 `run_coroutine_threadsafe` 提交。如果 SharedAsyncLoop 恰好在处理高负载，30 秒超时很容易触发。
2. `BoundedThreadPool` 的自旋锁 `pending` 计数器在多线程环境下没有原子性保证（`+=` 不是线程安全的）。

**修复方案**：见第四章。

### 🟠 P2 — 警告级

#### P2-1: `supervisord.conf` 中 Agent 全部 autostart=false

```ini
[program:agent-business]
autostart=false
autorestart=false

[program:agent-presales]
autostart=false
autorestart=false

[program:agent-translator]
autostart=false
autorestart=false
```

注释说"将由 Commander 隔离系统接管"，但代码中 `IsolatedAgentManager` 尚未完成集成。

**修复**：短期改为 `autostart=true`，等隔离系统就绪后再切回。

#### P2-2: 容器内 Supervisor 未安装

当前 `yaxiio:v1.04` 容器内 `supervisorctl: not found`。生产 Dockerfile 的 `RUN apt-get install -y supervisor` 在 v1.04 镜像构建时未执行。

#### P2-3: 日志缺乏统一 Trace ID

日志输出点：
- Python `print()` / `log()` 函数 → stdout
- `logging` 模块 → 部分写入 `LOG_DIR`
- Supervisor → `/app/logs/*.log`
- PM2 → pm2 logs

没有统一的 `trace_id` 或 `correlation_id` 贯穿一次用户请求的全链路。当 Commander 分派任务给 Agent、Agent 调用 MCP Server、MCP Server 写 Redis，链路完全断裂。

**参考方案**：OpenTelemetry 的 `W3C TraceContext` 标准——在每个 Redis Pub/Sub 消息的 payload 中携带 `traceparent` 头。

#### P2-4: A/B 测试缺少自动回滚机制

`ABTester._promote_strategy()` 将优胜策略写入 `commander:agent:scheduling_policy`，但：
- 没有保留上一个策略的完整快照
- 没有设置"观察期"——新策略推广后如果 7 天内指标恶化，无法自动回滚
- 推广历史只有追加 (RPUSH)，没有关联性能指标

### 🟢 P3 — 改善建议

#### P3-1: 错误处理过于宽泛

大量 `except Exception as e: print(...)` 吞掉了真实错误。建议：
- 关键路径（任务分发、ACK 确认）使用结构化错误 + 告警
- 非关键路径（日志写入）允许静默失败
- 引入 `sentry-sdk` 或等价方案做异常聚合

#### P3-2: Redis 键空间缺乏命名规范文档

实际前缀使用情况：
- `commander:*` — Commander 内部状态（合规 R1）
- `lightingmetal:*` — Agent 通信频道（合规 R1，只读）
- `lifecycle:*` — 生命周期管理
- `extensions:*` — 扩展系统
- `agent:*` — Agent 元数据
- `mcp:*` — MCP Server
- `skills:*` — Skill 注册表
- `yaxiio:*` — 雅溪宪法 + 任务状态

建议：在 CONFIGURATION.md 中建立正式的 Key Space Schema 文档。

#### P3-3: 测试覆盖率近乎为零

代码仓库中只有一个 `test_integration.py`，各模块末尾的 `if __name__ == "__main__"` 块充当了手工测试。没有单元测试、没有集成测试、没有 CI。

---

## 四、重构方案

### 4.1 目标架构

```
                        ┌────────────────────────┐
                        │   雅溪 Yaxiio Gateway   │  ← 唯一入口
                        │   (commander_v3.py)     │
                        │   WebSocket + HTTP API  │
                        └───────────┬────────────┘
                                    │
               ┌────────────────────┼────────────────────┐
               │                    │                    │
        ┌──────▼──────┐    ┌───────▼───────┐    ┌───────▼──────┐
        │  Commander  │    │   Dashboard   │    │  Agent 集群  │
        │  (v2 引擎)  │    │   (v2 Flask)  │    │  (PM2管理)   │
        └──────┬──────┘    └───────────────┘    └───────┬──────┘
               │                                        │
        ┌──────▼──────┐                         ┌───────▼──────┐
        │  五层 MCP   │                         │   P2P 直连   │
        │  Pipeline   │                         │  Redis Pub/Sub│
        └─────────────┘                         └──────────────┘
```

### 4.2 合并路线图

#### Phase 1：止血（1-2天）

| 任务 | 文件 | 动作 |
|------|------|------|
| 修复 Arsenal Bug | `yaxiio.py:121-166` | 删除重复的 `_register_defaults()`，合并为唯一版本；补充缺失方法或降级为 `_run_tool` 代理 |
| 移除硬编码密码 | 18个文件 | 全部替换为 `os.environ.get()` |
| Shell 脚本 Docker 解耦 | `agent.sh`、`agent-factory.sh` | 替换 `docker exec redis-centos7` 为 `redis-cli` 直连 |
| Agent autostart | `supervisord.conf` | 改为 `autostart=true` |
| 重新构建镜像 | `Dockerfile` | 基于修复后的代码构建 `yaxiio:v2.3.1-fix` |

#### Phase 2：合并 Commander 三兄弟（3-5天）

目标：将三个 Commander 实现收敛为一个，保留全部功能：

```
commander_v3.py (保留作为唯一入口)
  ├── 继承 CommanderV2 的全部能力 (六大引擎)
  ├── 接入 yaxiio.py 的 Constitution 宪法审查
  ├── 接入 yaxiio.py 的 WorkflowEngine (五层流水线)
  └── 保留 V3 的 SessionManager + WebSocket/HTTP API
```

具体步骤：
1. 将 `constitution.py` + `constitution.py` 从 `yaxiio.py` 中抽出为独立 mixin
2. 将 `WorkflowEngine` 注册为 CommanderV2 的 `_on_critical_command` 处理器
3. 删除 `agent-commander.py`（功能已被 CommanderV2 完全覆盖）
4. `yaxiio.py` 保留为向后兼容的薄 wrapper，内部实例化 CommanderV3

#### Phase 3：解决同步/异步混用（3天）

参考方案（借鉴 LangChain 的 `asyncio.to_thread` + FastAPI 的 `run_in_executor` 模式）：

```python
# 新的统一执行模型
class YaxiioExecutor:
    """所有异步操作统一走这个单例，消除 _run_async 的 hack。"""

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro, timeout=30):
        """提交协程并同步等待结果。线程安全。"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    def submit_async(self, coro):
        """提交协程不等待结果（fire-and-forget）。"""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)
```

所有同步→异步桥接统一走 `YaxiioExecutor`，替代当前的 `_run_async()` + `SharedAsyncLoop` 双轨制。

#### Phase 4：可观测性补齐（2天）

1. **统一 Trace ID**：在 `handle_task()` 入口生成 `trace_id`，注入到所有 Pub/Sub 消息的 `metadata.traceparent` 字段（W3C 格式）
2. **结构化日志**：引入 `structlog`，所有 `print()` 替换为 `structlog.get_logger().info("task_dispatched", task_id=..., trace_id=...)`
3. **健康检查修复**：healthcheck.sh 改为检查核心能力——Redis ping + Commander Pub/Sub 订阅数 > 0 + Dashboard HTTP 200

#### Phase 5：ABTester 回滚能力（1天）

在 `_promote_strategy()` 中增加：
```python
def _promote_strategy(self, test_config):
    # 1. 保存当前策略快照（用于回滚）
    old_policy = self.get_current_policy()
    if old_policy:
        self.redis.set("commander:agent:scheduling_policy:rollback",
                       json.dumps(old_policy))
    # 2. 写入新策略
    self.redis.set("commander:agent:scheduling_policy",
                   json.dumps(new_policy))
    # 3. 设置 7 天观察期，到期自动评估是否回滚
    self.redis.setex("commander:ab_test:observation:" + test_id,
                     86400 * 7,
                     json.dumps({"promoted_at": now, "baseline_success_rate": old_rate}))
```

---

## 五、参考资料与业界对标

### 5.1 架构参考

| 参考 | 对标点 | 可借鉴之处 |
|------|--------|-----------|
| **Google A2A Protocol** (github.com/google/A2A) | Yaxiio 的 A2A 适配层 | Agent Card 格式、Task 生命周期状态机 |
| **Anthropic MCP** (modelcontextprotocol.io) | Yaxiio 的五层 MCP Pipeline | stdio/HTTP 双传输、tools/list 能力发现 |
| **LangGraph** (langchain-ai/langgraph) | Yaxiio 的 WorkflowEngine | 条件边 + 循环的 DAG 执行模型、checkpointer 持久化 |
| **CrewAI** (crewAIInc/crewAI) | Yaxiio 的多 Agent 协作 | 角色定义、任务委派、顺序/层级流程 |
| **OpenTelemetry** (opentelemetry.io) | Yaxiio 的可观测性缺口 | W3C TraceContext、Span 嵌套、自动插桩 |

### 5.2 Agent 框架对比

| 能力 | Yaxiio 现状 | OpenAI Swarm | LangGraph | AutoGen |
|------|------------|-------------|-----------|---------|
| 任务编排 | ✅ 五层流水线 + DAG | 简单 handoff | ✅ 图状态机 | ✅ 对话驱动 |
| 故障降级 | ✅ L0-L4 五级 | ❌ 无 | ⚠️ 需手动 | ⚠️ 需手动 |
| 自进化 A/B | ✅ 双轨 A/B | ❌ 无 | ❌ 无 | ❌ 无 |
| 宪法约束 | ✅ Constitution | ❌ 无 | ❌ 无 | ❌ 无 |
| 会话分离 | ✅ V3 SessionManager | ❌ 无 | ✅ Checkpointer | ❌ 无 |
| P2P 直连 | ✅ replyTo + forward | ❌ 中心化 | ❌ 中心化 | ❌ 中心化 |
| 可观测性 | ❌ 无 Trace | ❌ 无 | ⚠️ LangSmith | ❌ 无 |
| 测试框架 | ❌ 无 | ❌ 无 | ⚠️ 有限 | ⚠️ 有限 |

**Yaxiio 的独特优势**：Constitution 宪法约束 + 五级降级 + A/B 自进化 是业界独有组合。这是真正的竞争壁垒。

### 5.3 密码管理参考

- **12-Factor App 第三原则**：配置存储在环境变量中（https://12factor.net/config）
- **HashiCorp Vault**：生产环境密钥管理的事实标准
- **git-secrets** (awslabs/git-secrets)：扫描 git 历史中的敏感信息

### 5.4 异步 Python 参考

- **PEP 3156** — Python asyncio 规范
- **asgiref** (django/asgiref) — `sync_to_async` / `async_to_sync` 实现参考
- **anyio** (agronholm/anyio) — 结构化并发，可替代裸 asyncio

---

## 六、总结

**健康度评分**：

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐⭐ | 五层流水线 + 宪法 + 六大引擎 + A2A，业界领先 |
| 代码质量 | ⭐⭐⭐ | 核心逻辑清晰但有重复代码和 Bug |
| 可运维性 | ⭐⭐ | 健康检查失败、日志碎片化、密码硬编码 |
| 可测试性 | ⭐ | 近乎为零的测试覆盖 |
| 安全性 | ⭐⭐ | 密码泄露 + 硬编码依赖 |
| 可扩展性 | ⭐⭐⭐⭐ | A2A 协议 + ExtensionRouter + AgentFactory 设计到位 |

**一句话**：Yaxiio 的架构设计（宪法约束、五级降级、A/B 自进化）是真正的竞争壁垒，但当前处于"重构中途"——v1 和 v2 两套实现尚未收敛，部署和生产可观测性有显著缺口。按 Phase 1→5 路线图推进，预计 2 周可达到生产级标准。
