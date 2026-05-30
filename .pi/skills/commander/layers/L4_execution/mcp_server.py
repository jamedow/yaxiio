"""
L4 Execution Server — 执行层 MCP Server
=======================================
工具:
  - execute_task(agent_id, command) → {result, elapsed_ms}
  - launch_agent(agent_name) → {pid, status}
  - get_agent_status() → [{agent_id, status, tasks}]
  - sandbox_exec(code) → {stdout, stderr}
"""

import sys, os, time, subprocess
sys.path.insert(0, "/opt/commander")

from mcp.protocol import MCPServer, run_mcp_server
from config import L4_EXECUTION_PORT


class ExecutionServer(MCPServer):
    """执行层: Agent执行 + 沙箱 + 结果收集。"""

    def __init__(self):
        super().__init__("L4_execution", "Execution Layer — Agent Execution & Sandbox")

        self._task_count = 0
        self._results = []

        self.register_tool("execute_task", self.execute_task)
        self.register_tool("launch_agent", self.launch_agent)
        self.register_tool("get_agent_status", self.get_agent_status)
        self.register_tool("sandbox_exec", self.sandbox_exec)
        self.register_tool("dispatch_task", self.dispatch_task)
        self.register_tool("dispatch_and_await", self.dispatch_and_await)

    def execute_task(self, agent_id: str = "", command: str = "") -> dict:
        """执行任务（调用 agent-core 或直接 subprocess）。"""
        self._task_count += 1
        t0 = time.time()

        try:
            proc = subprocess.run(
                command.split(), capture_output=True, text=True, timeout=120, shell=False
            )
            elapsed = int((time.time() - t0) * 1000)
            result = {
                "agent_id": agent_id,
                "status": "success" if proc.returncode == 0 else "failed",
                "stdout": proc.stdout[:2000],
                "stderr": proc.stderr[:500],
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed,
            }
        except subprocess.TimeoutExpired:
            result = {
                "agent_id": agent_id,
                "status": "timeout",
                "error": "Execution timeout (>120s)",
                "elapsed_ms": 120000,
            }
        except Exception as e:
            result = {
                "agent_id": agent_id,
                "status": "error",
                "error": str(e),
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        self._results.append(result)
        if len(self._results) > 100:
            self._results = self._results[-50:]

        return result

    def launch_agent(self, agent_name: str = "") -> dict:
        """启动新Agent进程。"""
        try:
            proc = subprocess.Popen(
                ["python3", "/app/.pi/agents/runtime/agent-core.py"],
                env={"AGENT_NAME": agent_name, "AGENT_ROLE": agent_name, **os.environ},
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return {"agent_name": agent_name, "pid": proc.pid, "status": "launched"}
        except Exception as e:
            return {"agent_name": agent_name, "status": "failed", "error": str(e)}

    def get_agent_status(self) -> dict:
        """获取Agent状态。"""
        try:
            result = subprocess.run(
                "pm2 jlist 2>/dev/null",
                shell=False, capture_output=True, text=True, timeout=5
            )
            import json
            agents = json.loads(result.stdout) if result.stdout else []
            return {
                "agents": [
                    {"name": a.get("name"), "status": a.get("pm2_env", {}).get("status")}
                    for a in agents[:20]
                ],
                "total": len(agents),
            }
        except Exception:
            return {"agents": [], "total": 0}


    def dispatch_task(self, action: str = "", codebase: str = "", issue: str = "", **kwargs) -> dict:
        """Dispatch task to Commander via Stream + PubSub. Returns taskId."""
        import redis as _r, json as _j, uuid, time
        try:
            rr = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
            tid = f"mcp-{uuid.uuid4().hex[:8]}"
            msg = {"type":"task","taskId":tid,"from":"mcp-l4","to":"commander","payload":{"action":action,"codebase":codebase,"issue":issue,**kwargs}}
            # Stream 持久化
            try:
                rr.xadd("yaxiio:stream:task_incoming", {
                    "task_id": tid, "payload": _j.dumps(msg, ensure_ascii=False),
                    "timestamp": str(time.time()),
                }, maxlen=10000)
            except Exception:
                pass
            # Pub/Sub 快速通道
            subs = rr.publish("yaxiio:agent:commander", _j.dumps(msg, ensure_ascii=False))
            rr.close()
            return {"task_id": tid, "action": action, "subscribers": subs, "status": "dispatched"}
        except Exception as e: return {"error": str(e)}

    def dispatch_and_await(self, agent_name: str = "", task_id: str = "",
                          sid: str = "", action: str = "", prompt: str = "",
                          parent_task: str = "", agent_skill: str = "", timeout: int = 600) -> dict:
        """Dispatch to neuron and await result (orchestration primitive)"""
        import redis as _r, json as _j, time, subprocess, os
        
        r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
        
        # 1. Ensure neuron is running
        try:
            existing = subprocess.run(["pgrep", "-f", f"AGENT_NAME={agent_name}.*TASK_ID={parent_task}"],
                                     capture_output=True, text=True, timeout=3)
            if not existing.stdout.strip():
                env = {**os.environ, "AGENT_NAME": agent_name, "AGENT_SKILL": agent_skill,
                       "TASK_ID": parent_task, "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379",
                       "REDIS_PASSWORD": "$REDIS_PASSWORD"}
                subprocess.Popen(["python3", "/opt/commander/neuron.py"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(1.5)
        except:
            pass
        
        # 2. Dispatch task to agent channel
        sub_task_id = f"{parent_task}-{sid}"
        msg = {"type":"task","taskId":sub_task_id,"from":"L4","to":agent_name,
               "replyTo":"lightingmetal:agent:commander",
               "payload":{"action":action,"task":prompt,"parent_task":parent_task}}
        r.publish(f"lightingmetal:agent:{agent_name}", _j.dumps(msg, ensure_ascii=False))
        
        # 3. Streams 通道 (Phase 3): 发布到 Stream, Consumer Group 自动分配
        try:
            from stream_bridge import StreamBridge
            bridge = StreamBridge()
            bridge.publish_task("L4", {
                "taskId": sub_task_id,
                "agent_name": agent_name,
                "action": action,
                "prompt": prompt,
                "parent_task": parent_task,
            }, sub_task_id)
        except Exception:
            pass  # Streams 不可用时走 Pub/Sub

        # 4. Poll for result (Pub/Sub fallback)
        t0 = time.time()
        interval = 2
        while time.time() - t0 < timeout:
            raw = r.get(f"agent:{agent_name}:{parent_task}:memory")
            if raw:
                try:
                    memory = _j.loads(raw)
                    for entry in reversed(memory[-10:]):
                        if entry.get("task_id") == sub_task_id:
                            summary = entry.get("summary", "")
                            if summary and len(summary) > 10:
                                return {"ok": True, "output": summary[:1000], "agent": agent_name,
                                        "elapsed_ms": int((time.time()-t0)*1000)}
                except:
                    pass
            time.sleep(interval)
            interval = min(interval * 1.5, 30)
        
        return {"ok": False, "error": "timeout after " + str(timeout) + "s", "agent": agent_name,
                "elapsed_ms": int((time.time()-t0)*1000)}

    def sandbox_exec(self, code: str = "") -> dict:
        """沙箱执行代码。"""
        try:
            proc = subprocess.run(
                ["python3", "-c", code],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "SANDBOX": "1"}
            )
            return {
                "stdout": proc.stdout[:1000],
                "stderr": proc.stderr[:500],
                "exit_code": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"error": "Sandbox timeout (>10s)", "exit_code": -1}
        except Exception as e:
            return {"error": str(e), "exit_code": -1}


if __name__ == "__main__":
    run_mcp_server("L4_execution", ExecutionServer(), L4_EXECUTION_PORT)
