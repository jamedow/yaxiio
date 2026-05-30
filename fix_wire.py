#!/usr/bin/env python3
"""Wire SemanticIntentRouter into _decompose_via_l2 + enable feature flags"""

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path, "r") as f:
    content = f.read()

changes = 0

# ── Enable L3 async orchestrator by default ──
old_async = (
    '        _use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "false").lower() == "true"'
)
new_async = (
    '        _use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "true").lower() == "true"'
)
if old_async in content:
    content = content.replace(old_async, new_async)
    changes += 1
    print("OK: YAXIIO_ASYNC_ORCHESTRATOR default → true")
else:
    print("FAIL: async orchestrator flag not found")

# ── Wire intent_router into _decompose_via_l2 ──
# Find the "L0: Retrieve past experiences" section and add semantic routing before it
old_section = (
    "        task_desc = str(payload.get(\"task\", payload.get(\"action\", \"\")))[:800]\n"
    "        action = payload.get(\"action\", \"unknown\")\n"
    "        action_clean = action.replace(\"site_\", \"\").replace(\"translate_\", \"\")\n"
    "        self._current_intent = action_clean\n"
    "        available = list(self._agent_skill_map().keys())\n"
    "\n"
    "        # L0: Retrieve past experiences for this intent"
)

if old_section in content:
    new_section = (
        '        task_desc = str(payload.get("task", payload.get("action", "")))[:800]\n'
        '        action = payload.get("action", "unknown")\n'
        '        action_clean = action.replace("site_", "").replace("translate_", "")\n'
        '        self._current_intent = action_clean\n'
        '        available = list(self._agent_skill_map().keys())\n'
        '\n'
        '        # ── Semantic Intent Routing (replaces INTENT_TOOL_MAP) ──\n'
        '        _primary_agent = None\n'
        '        try:\n'
        '            if hasattr(self, "intent_router") and self.intent_router:\n'
        '                _route = self.intent_router.route(task_desc)\n'
        '                if _route and _route.get("confidence", 0) > 0.4:\n'
        '                    _primary_agent = _route.get("primary_agent")\n'
        '                    print(f"[WF] {task_id} semantic route: {_primary_agent} '\n'
        '                          f"(conf={_route.get(\'confidence\',0):.2f})", flush=True)\n'
        '        except Exception as _re:\n'
        '            print(f"[WF] {task_id} semantic router failed: {_re}", flush=True)\n'
        '\n'
        "        # L0: Retrieve past experiences for this intent"
    )
    content = content.replace(old_section, new_section)
    changes += 1
    print("OK: intent_router wired into _decompose_via_l2")
else:
    print("FAIL: _decompose_via_l2 section not found")

with open(path, "w") as f:
    f.write(content)

print(f"\n{changes}/2 changes applied")
