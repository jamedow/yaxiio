# Yaxiio Core Engine

The heart of Yaxiio — an 8-module Agent orchestration kernel.

## Module Map

```
                    ┌─────────────┐
                    │  yaxiio.py  │  Commander entry point
                    │  (755 lines)│  Redis Pub/Sub, crash recovery
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
    ┌─────────────┐ ┌───────────┐ ┌──────────────┐
    │constitution │ │ workflow  │ │ pi_guardian  │
    │   .py       │ │ _engine   │ │   _v3.py     │
    │宪法审查框架  │ │ .py 1036L │ │ 进程守护      │
    └─────────────┘ └─────┬─────┘ └──────────────┘
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
┌────────────┐   ┌──────────────┐   ┌──────────────┐
│agent_      │   │gap_analyzer  │   │l0_memory     │
│factory.py  │   │.py           │   │.py           │
│Agent工厂   │   │差距分析器     │   │L0经验记忆层   │
│74行        │   │56行          │   │80行          │
└────────────┘   └──────────────┘   └──────────────┘
       │
       ▼
┌────────────┐
│neuron.py   │  Agent runtime
│613行       │  LLM + tools + state machine
└────────────┘
```

## Key Files

| File | Lines | Role |
|------|-------|------|
| `yaxiio.py` | 755 | Commander: Redis Pub/Sub listener, task dispatch, crash recovery |
| `workflow_engine.py` | 1036 | L1→L5 orchestration, self-check loop, verification |
| `neuron.py` | 613 | Agent runtime: LLM, tool execution, state machine |
| `constitution.py` | 192 | Constitutional review: ALLOWED/DELEGATED/REJECTED/DEGRADED |
| `agent_factory.py` | 74 | Capability card → Agent instance |
| `gap_analyzer.py` | 56 | Content-aware gap detection, corrective action planning |
| `l0_memory.py` | 80 | Experience storage, web knowledge caching |
| `mcp_bridge.py` | 27 | Unified MCPClient call interface with retry |
| `workflow_snapshot.py` | 34 | Cross-subtask data relay |
| `workflow_utils.py` | 53 | LLM helpers, skill map, thinking upgrade |
| `parallel_orchestrator.py` | 100 | Dependency-aware parallel subtask execution |
| `task_state_machine.py` | 141 | Task lifecycle: PENDING→RUNNING→DONE/FAILED |
| `pi_guardian_v3.py` | 809 | PM2-managed process guardian with health checks |
| `config.py` | 56 | Unified configuration via env vars |

## Data Flow

```
Redis Pub/Sub (yaxiio:agent:commander)
    │
    ▼
Commander receives task
    │
    ▼
constitution.review() → ALLOWED/DELEGATED
    │
    ▼
L1 Perception → L2 Planning → AgentFactory → L3 Schedule → L4 Execute → L5 Score
    │                                                    │
    ▼                                                    ▼
gap_analyzer checks if done                   l0_memory saves experience
    │
    ▼
Not done? Continue loop (max 3 rounds)
Done? Cleanup + destroy session
```

## Starting the Engine

```bash
python3 yaxiio/yaxiio.py
# → Spawns L1-L5 MCP servers (3401-3405)
# → Subscribes to Redis channel
# → Ready to receive tasks
```

Send a task:
```bash
redis-cli PUBLISH 'yaxiio:agent:commander' '{"type":"task","taskId":"demo-001","payload":{"action":"site_audit","task":"Audit content quality"}}'
```
