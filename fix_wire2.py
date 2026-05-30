#!/usr/bin/env python3
"""Wire semantic router + enable async orchestrator"""
import os

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path) as f:
    content = f.read()

changes = 0

# 1. Wire intent_router into _decompose_via_l2
old = '        # L0: Retrieve past experiences for this intent'
if old in content:
    new = '''        # Semantic Intent Routing (replaces INTENT_TOOL_MAP)
        _primary_agent = None
        try:
            if hasattr(self, "intent_router") and self.intent_router:
                _route = self.intent_router.route(task_desc)
                if _route and _route.get("confidence", 0) > 0.4:
                    _primary_agent = _route.get("primary_agent")
                    print("[WF] {} semantic route: {} (conf={:.2f})".format(
                        task_id, _primary_agent, _route.get("confidence", 0)), flush=True)
        except Exception:
            pass  # semantic router unavailable, fallback to INTENT_TOOL_MAP

        # L0: Retrieve past experiences for this intent'''
    content = content.replace(old, new)
    changes += 1
    print("OK: intent_router wired into _decompose_via_l2")
else:
    print("FAIL: L0 marker not found")

# 2. Enable L3 async orchestrator default
old_flag = '_use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "false").lower() == "true"'
new_flag = '_use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "true").lower() == "true"'
if old_flag in content:
    content = content.replace(old_flag, new_flag)
    changes += 1
    print("OK: async orchestrator default enabled")
else:
    print("FAIL: async flag not found")

with open(path, "w") as f:
    f.write(content)

print(f"{changes}/2 changes applied")
