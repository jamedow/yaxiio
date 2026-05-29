# Commander API 参考 v2.3

## CommanderV2

### 构造

```python
from src.commander_v2 import CommanderV2

commander = CommanderV2(
    agent_id="commander",
    redis_host="redis",
    redis_port=6379,
    redis_password="commander-secret",
    mongo_client=None,                # 可选：MongoDB 持久化
    use_sentinel=False,               # 可选：Redis Sentinel HA
    sentinel_hosts=None,
    llm_api_key=None,                 # 可选：LLM 智能路由
    llm_base_url="https://api.deepseek.com/v1",
    llm_model="deepseek-chat",
    enable_lifecycle=True,
    enable_designer=True,
    enable_evolver=True,
    enable_extensions=True,           # v2.3: 动态扩展系统
)
```

### handle_task(task_description: str) → dict

处理新任务的完整流程（六大优化引擎 + 扩展系统）。

```python
result = commander.handle_task("审计俄语页面中文残留并翻译")
# → {
#     "status": "dispatched",
#     "task_id": "task-1717000000000",
#     "subtasks": 3,
#     "results": [
#         {"target": "翻译官", "result": "ack_received", ...}
#     ]
# }
```

### 频道

```python
# Commander 监听
CHANNEL = "lightingmetal:agent:commander"

# Agent 频道格式
CHANNEL = f"lightingmetal:agent:{agent_name}"
```

---

## TaskAnalyzer

```python
from src.task_analyzer import TaskAnalyzer

ta = TaskAnalyzer(redis_host, redis_port, redis_password)

# 查重
result = ta.check_duplicate("翻译俄语产品页")
# → {"is_duplicate": True/False, "match_type": "exact|fuzzy", "original_task_id": "..."}

# 拆分建议
subtasks = ta.suggest_split("审计并翻译俄语页面")
# → [{"type": "audit", "agent_type": "审计官", ...}, ...]

# 缓存指纹
ta.cache_task(task_id, description, summary)
```

---

## AutoScaler

```python
from src.auto_scaler import AutoScaler

scaler = AutoScaler(redis_host, redis_port, redis_password)

# 检查并伸缩
result = scaler.check_and_scale()
# → {"action": "scale_out|scale_in|no_change", "count": 2, "reason": "..."}
```

---

## ReliableComm

```python
from src.reliable_comm import ReliableComm

comm = ReliableComm("commander", redis_host, redis_port, redis_password)

# 发送关键指令（List通道 + ACK确认）
result = comm.send_critical_command("翻译官", {"type": "task", "payload": {...}})
# → {"status": "ack_received|timeout|failed", "attempts": 1}

# 广播消息（Pub/Sub）
comm.broadcast({"type": "heartbeat_check"})

# 注册指令处理器
comm.register_handler(lambda cmd: handle_command(cmd))
```

---

## ABTester

```python
from src.ab_tester import ABTester

ab = ABTester(redis_host, redis_port, redis_password)

# 创建A/B测试
ab.create_test(
    test_name="split_granularity",
    description="测试更细粒度的任务拆分",
    strategy_config={"granularity": "fine", "parallel_limit": 2},
    duration_hours=24,
    min_samples=20
)

# 任务分流
group = ab.route_task()  # → "group_a" | "group_b"

# 记录结果
ab.record_result(group="group_b", success=True, task_id="...", metadata={})

# 获取活跃测试
active = ab.get_active_test()

# 获取测试报告
report = ab.get_report("split_granularity")
```

---

## Failover & Degradation

```python
from src.failover import AgentFailover, TaskDegradation

# 故障转移
failover = AgentFailover(redis_client, mongo_client)
failover.start_monitoring()

# 降级检测
degradation = TaskDegradation(redis_client, mongo_client)
level = degradation.get_degradation_level("quote")  # → "L0"~"L4"

# 执行降级
result = degradation.execute_degraded({"taskId": "..."}, "L4")
```

---

## LLMRouter

```python
from src.llm_router import LLMRouter

router = LLMRouter(
    redis_client,
    llm_api_key="sk-xxx",
    llm_base_url="https://api.deepseek.com/v1",
    llm_model="deepseek-chat"
)

# 路由决策
decision = router.route_task(
    task={"description": "翻译光伏支架规格书为俄语", "type": "translate"},
    agent_capabilities=[
        {"agentId": "翻译官", "role": "翻译官", "capabilities": ["翻译"], "status": "running"},
        {"agentId": "商务经理", "role": "商务经理", "capabilities": ["沟通"], "status": "idle"},
    ]
)
# → {"selected_agent": "翻译官", "confidence": 0.95, "reasoning": "...", "routing_method": "llm"}
```

---

## A2A Protocol

```python
from src.a2a_protocol import A2AAdapter, AgentCard, AgentDiscovery

# 适配器
adapter = A2AAdapter(redis_client)
a2a_task = adapter.to_a2a(redis_message)     # Redis → A2A
redis_msg = adapter.from_a2a(a2a_task)       # A2A → Redis

# 能力发现
discovery = AgentDiscovery(redis_client)
discovery.register(card)                     # 注册能力卡片
agents = discovery.discover("翻译")          # 能力发现
best = discovery.find_best_match("翻译")     # 最优匹配
```

---

## SkillManager (v2.3)

```python
from src.skill_manager import SkillManager

sm = SkillManager(redis_client, mongo_client)

# 安装 Skill
result = await sm.install_skill("translate-engine", source="npm")
# → {"skill_name": "translate-engine", "status": "success", ...}

# 卸载 Skill
result = await sm.uninstall_skill("translate-engine")

# 为 Agent 启用
await sm.enable_skill_for_agent("translate-engine", "翻译官")

# 查询
skills = sm.get_global_skills()
agent_skills = sm.get_agent_skills("翻译官")

# 搜索
matches = await sm.search_skill("翻译")
```

---

## MCPManager (v2.3)

```python
from src.mcp_manager import MCPManager

mm = MCPManager(redis_client, mongo_client)

# 注册 MCP Server
result = await mm.register_mcp_server(
    "firecrawl",
    command="npx",
    args=["-y", "@anthropic/mcp-server-firecrawl"]
)

# 管理
await mm.enable_mcp_server("firecrawl")
await mm.disable_mcp_server("firecrawl")
await mm.unregister_mcp_server("firecrawl")

# 健康检查
health = await mm.test_connection("firecrawl")
all_health = await mm.run_health_checks()

# 查询
servers = mm.get_registered_servers()
tools = mm.get_server_tools("firecrawl")

# 搜索
matches = await mm.search_mcp_server("爬取")
```

---

## ExtensionRouter (v2.3)

```python
from src.extension_router import ExtensionRouter, build_extension_router

router = build_extension_router(
    redis_client=redis_client,
    mongo_client=mongo_client,
    lifecycle_manager=lifecycle_manager,
    agent_designer=designer,
    task_analyzer=task_analyzer
)

# 分析并扩展
result = await router.analyze_and_extend({
    "taskId": "task-001",
    "type": "translate",
    "description": "翻译光伏支架规格书为阿拉伯语"
})
# → {
#     "strategies_executed": 2,
#     "decisions": [
#         {"gap": {"capability": "阿拉伯语翻译"}, "strategy": {"action": "install_skill"}, ...},
#         {"gap": {"capability": "firecrawl"}, "strategy": {"action": "register_mcp"}, ...}
#     ]
# }

# 查询
decisions = router.get_recent_decisions(limit=20)
providers = router.get_capability_providers("翻译")
```

---

## AgentFactory (Shell)

```bash
# 分析任务 → 建议 Agent
bash agent-factory.sh analyze "审计俄语页面中文残留并翻译"
# → 建议: 翻译官, 审计官

# 创建 Agent
bash agent-factory.sh create ru-auditor 俄语审计官 "审计俄语中文残留"

# 启动
bash agent-factory.sh spawn ru-auditor 俄语审计官

# 销毁
bash agent-factory.sh destroy ru-auditor

# 全自动
bash agent-factory.sh request 俄语审计官 "审计俄语中文残留"
```

---

## Dashboard v2

```bash
# 启动（仅 Redis）
python3 dashboard_v2.py

# 启动（Redis + MongoDB）
python3 dashboard_v2.py "mongodb://user:pass@host:27017/"

# 访问
open http://localhost:3003/dashboard
```

### 健康检查端点

```
GET /health → {"status": "ok", "redis": true, "agents": 3}
```

---

## 消息协议

### 标准消息格式

```json
{
  "from": "commander",
  "to": "翻译官",
  "type": "task",
  "taskId": "task-1717000000000",
  "timestamp": "2026-05-24T12:00:00",
  "replyTo": "",
  "payload": {
    "action": "translate",
    "data": {
      "text": "热镀锌螺旋地桩",
      "target": "ru"
    }
  }
}
```

### P2P 请求示例

```bash
# 商务经理 → 售前经理 (P2P)
redis-cli PUBLISH lightingmetal:agent:售前经理 \
  '{"from":"商务经理","to":"售前经理","type":"request","taskId":"p2p-001","replyTo":"商务经理","payload":{"action":"generate_quote","data":{"product":"solar-ground-screw","qty":5000}}}'
```
