# Five-Layer MCP Architecture

Each layer is an independent MCP (Model Context Protocol) server, communicating via JSON-RPC 2.0 over HTTP. This enables independent development, testing, scaling, and crash isolation.

## Layer Map

```
Port 3401            3402            3403            3404            3405
  │                  │               │               │               │
  ▼                  ▼               ▼               ▼               ▼
┌─────────┐    ┌──────────┐   ┌───────────┐   ┌──────────┐   ┌──────────┐
│   L1    │ →  │    L2    │→ │    L3     │→  │    L4    │→ │    L5    │
│Perception│   │ Planning │   │Coordination│  │Execution │   │Evolution │
└─────────┘    └──────────┘   └───────────┘   └──────────┘   └──────────┘
  "What?"        "How?"         "Who?"          "Do it!"       "How good?"
```

## L1 — Perception (port 3401)

| Tool | Description |
|------|-------------|
| `analyze_intent` | Dual-path: keyword matching + LLM classification |
| `load_skills` | Load agent skills from Redis/SQLite |
| `ping` | Health check |

Intent mapping:
```json
{"text": "审计全站内容质量"} → {"primary_intent": "audit", "confidence": 0.99}
```

## L2 — Planning (port 3402)

| Tool | Description |
|------|-------------|
| `decompose_task` | LLM-driven task decomposition into DAG |
| `select_strategy` | Choose execution strategy (parallel/sequential) |
| `list_skills` | List available agent skills |

Data-driven batch detection:
```
Detect "3973 entries" → Auto-decompose: 9 batches × 496 items
```

## L3 — Coordination (port 3403)

| Tool | Description |
|------|-------------|
| `schedule_agents` | Least-loaded-first agent assignment |
| `get_agent_load` | Query agent workload |
| `report_crash` | Crash → restart strategy (ONE_FOR_ONE) |
| `scale_check` | Auto-scale trigger (load > 8 tasks) |
| `release_agent` | Release agent after task completion |

## L4 — Execution (port 3404)

| Tool | Description |
|------|-------------|
| `dispatch_and_await` | Spawn agent → dispatch task → poll result |
| `execute_task` | Direct task execution |
| `launch_agent` | Launch new agent instance |
| `sandbox_exec` | Sandboxed code execution |
| `dispatch_task` | Fire-and-forget task dispatch |

Polling: exponential backoff 2s → 30s, max 600s timeout.

## L5 — Evolution (port 3405)

| Tool | Description |
|------|-------------|
| `deep_score` | LLM quality scoring (5 dimensions) |
| `meta_reflect` | Post-task reflection + pattern detection |
| `generate_tool` | **Auto-generate Python tools from failure patterns** |
| `generate_skill` | Create new agent skills |
| `generate_agent` | Design new agent types |
| `web_research` | Internet knowledge retrieval |
| `research_and_retry` | Research → retry with enhanced context |

Score dimensions:
```json
{"accuracy": 8, "completeness": 6, "professionalism": 7, "actionability": 7, "consistency": 8}
```

## Communication Protocol

All layers use MCP (JSON-RPC 2.0):

```json
// Request
{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "deep_score", "arguments": {...}}, "id": 1}

// Response
{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "..."}]}, "id": 1}
```

Layer communication goes through `mcp_bridge.py`:

```python
from mcp_bridge import call_layer
result = call_layer(5, "deep_score", task_id="t1", output="...")
```
