#!/usr/bin/env python3
"""Inject SemanticIntentRouter, AsyncOrchestrator, RedisDataBus into WorkflowEngine.__init__"""

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path, "r") as f:
    content = f.read()

changes = 0

# ── Inject new routers and orchestrator into __init__ ──
old_init_end = '        from gap_analyzer import GapAnalyzer\n        self.gap = GapAnalyzer()'

if old_init_end in content:
    new_init_snippet = (
        '        from gap_analyzer import GapAnalyzer\n'
        '        self.gap = GapAnalyzer()\n'
        '\n'
        '        # ── L2: Semantic Intent Router (replaces INTENT_TOOL_MAP) ──\n'
        '        from modules.layer2.intent_router import SemanticIntentRouter\n'
        '        try:\n'
        '            from modules.layer1.vector_store_chroma import ChromaVectorStore\n'
        '            _vs = ChromaVectorStore()\n'
        '        except Exception:\n'
        '            from modules.layer1.vector_store import MemVectorStore\n'
        '            _vs = MemVectorStore()\n'
        '        self.intent_router = SemanticIntentRouter(\n'
        '            vector_store=_vs,\n'
        '            redis_client=self.commander.redis if self.commander else None\n'
        '        )\n'
        '\n'
        '        # ── L2: Intelligent Model Router ──\n'
        '        from modules.layer2.model_router_v2 import IntelligentModelRouter\n'
        '        self.model_router_v2 = IntelligentModelRouter(\n'
        '            redis_client=self.commander.redis if self.commander else None\n'
        '        )\n'
        '\n'
        '        # ── L3: Async Orchestrator + Redis Data Bus ──\n'
        '        from modules.layer3.async_orchestrator import AsyncOrchestrator\n'
        '        from modules.layer3.redis_data_bus import RedisDataBus\n'
        '        self.async_orch = AsyncOrchestrator(\n'
        '            commander=self.commander,\n'
        '            max_concurrent=int(os.environ.get("YAXIIO_MAX_CONCURRENT", "10")),\n'
        '            total_timeout=float(os.environ.get("YAXIIO_TASK_TIMEOUT", "600")),\n'
        '            subtask_timeout=float(os.environ.get("YAXIIO_SUBTASK_TIMEOUT", "120")),\n'
        '        )\n'
        '        self.data_bus = RedisDataBus(\n'
        '            redis_client=self.commander.redis if self.commander else None\n'
        '        )\n'
    )
    content = content.replace(old_init_end, new_init_snippet)
    changes += 1
    print("OK: AsyncOrchestrator + SemanticIntentRouter injected into __init__")
else:
    print("FAIL: __init__ snippet not found")

# ── Wire _orchestrate_subtasks to use AsyncOrchestrator ──
# Find the current method and add feature-flagged async path at the top
old_orch_start = (
    "    def _orchestrate_subtasks(self, task_id: str, subtasks: list, payload: dict) -> dict:\n"
    "        if MCP_LAYERS_ENABLED.get(\"L3\"):\n"
    "            return {\"mcp_routed\": True, \"layer\": \"L3\", \"phase\": \"not_implemented\"}"
)

if old_orch_start in content:
    new_orch_start = (
        '    def _orchestrate_subtasks(self, task_id: str, subtasks: list, payload: dict) -> dict:\n'
        '        if MCP_LAYERS_ENABLED.get("L3"):\n'
        '            return {"mcp_routed": True, "layer": "L3", "phase": "not_implemented"}\n'
        '\n'
        '        # ── Async path (feature-flagged) ──\n'
        '        _use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "false").lower() == "true"\n'
        '        if _use_async and hasattr(self, "async_orch") and self.async_orch:\n'
        '            try:\n'
        '                import asyncio\n'
        '                loop = asyncio.new_event_loop()\n'
        '                asyncio.set_event_loop(loop)\n'
        '                results = loop.run_until_complete(\n'
        '                    self.async_orch.execute(task_id, subtasks, payload)\n'
        '                )\n'
        '                loop.close()\n'
        '                # Write results to data_bus\n'
        '                if hasattr(self, "data_bus") and self.data_bus:\n'
        '                    for sid, r in results.items():\n'
        '                        self.data_bus.put(task_id, sid, r)\n'
        '                print(f"[WF] {task_id} AsyncOrchestrator: {len(results)} results", flush=True)\n'
        '                return results\n'
        '            except Exception as e:\n'
        '                print(f"[WF] {task_id} AsyncOrchestrator failed ({e}), fallback to thread pool", flush=True)\n'
        '\n'
        '        # ── Thread pool fallback (existing logic) ──'
    )
    content = content.replace(old_orch_start, new_orch_start)
    changes += 1
    print("OK: _orchestrate_subtasks -> AsyncOrchestrator (feature-flagged)")
else:
    print("FAIL: _orchestrate_subtasks pattern not found")

with open(path, "w") as f:
    f.write(content)

print(f"\n{changes}/2 changes applied")
