#!/usr/bin/env python3
"""Fix workflow_engine.py L4 dispatch: wait for neuron response instead of echo stub."""
import json, time, sys

TARGET = "/opt/commander/workflow_engine.py"

with open(TARGET, "r") as f:
    code = f.read()

old = '''        tool_name = plan.get("tool")
        if tool_name and self.commander and self.commander.arsenal.has(tool_name):
            try:
                l4 = {"status": "success", "arsenal_tool": tool_name,
                      "result": self.commander.arsenal.call(tool_name, task_id, payload)}
            except Exception as e:
                l4 = {"status": "error", "error": str(e)[:500]}
        else:
            l4 = call_layer(4, "execute_task", agent_id=agent_name or task_id,
                           command=f"echo 'task:{task_id}'")
        return l4'''

new = '''        tool_name = plan.get("tool")
        if tool_name and self.commander and self.commander.arsenal.has(tool_name):
            try:
                l4 = {"status": "success", "arsenal_tool": tool_name,
                      "result": self.commander.arsenal.call(tool_name, task_id, payload)}
            except Exception as e:
                l4 = {"status": "error", "error": str(e)[:500]}
        elif l3.get("dispatched"):
            l4 = self._wait_for_response(task_id, agent_name)
        else:
            l4 = {"status": "error", "error": "no agent dispatched"}
        return l4

    def _wait_for_response(self, task_id: str, agent_name: str, timeout: int = 120) -> dict:
        if not self.commander or not self.commander.redis:
            return {"status": "error", "error": "no redis"}
        print(f"[WF] waiting for {agent_name} (task={task_id})...", flush=True)
        start = time.time()
        try:
            pubsub = self.commander.redis.client.pubsub()
            pubsub.subscribe("lightingmetal:agent:commander")
            while time.time() - start < timeout:
                msg = pubsub.get_message(timeout=2.0)
                if not msg or msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except:
                    continue
                if data.get("taskId") == task_id and data.get("type") == "response":
                    pl = data.get("payload", {})
                    elapsed = time.time() - start
                    pubsub.close()
                    print(f"[WF] {agent_name} responded ({elapsed:.1f}s)", flush=True)
                    return {
                        "agent_id": agent_name,
                        "status": pl.get("status", "unknown"),
                        "stdout": str(pl.get("thought", pl.get("result", "")))[:5000],
                        "stderr": "",
                        "exit_code": 0,
                        "elapsed_ms": int(elapsed * 1000),
                    }
            pubsub.close()
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}
        return {"status": "timeout", "error": f"{agent_name} no resp {timeout}s"}'''

if old in code:
    code = code.replace(old, new)
    with open(TARGET, "w") as f:
        f.write(code)
    print("OK: workflow_engine.py patched")
else:
    print("FAIL: pattern not found")
    sys.exit(1)
