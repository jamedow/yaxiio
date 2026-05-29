
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_L3 = 0xc71f9607

"""
L3 Coordination Server — 协调层 MCP Server
===========================================
工具:
  - schedule_agents(plan) → {assignments, load_distribution}
  - get_agent_load() → {agent_loads}
  - report_crash(agent_id, error) → {action, agents_to_restart}
  - scale_check() → {action, reason}
"""

import sys, os, time
sys.path.insert(0, '/opt/commander')

from mcp.protocol import MCPServer, run_mcp_server
from config import L3_COORDINATION_PORT, MAX_RESTARTS, RESTART_PERIOD


class CoordinationServer(MCPServer):
    """协调层: Agent调度 + 负载均衡 + 监督。"""

    def __init__(self):
        super().__init__("L3_coordination", "Coordination Layer — Agent Scheduling & Supervision")

        self._agent_loads = {}  # agent_id → active_tasks
        self._crash_counts = {}  # agent_id → count
        self._crash_times = {}  # agent_id → last_crash_time

        self.register_tool("schedule_agents", self.schedule_agents)
        self.register_tool("get_agent_load", self.get_agent_load)
        self.register_tool("report_crash", self.report_crash)
        self.register_tool("scale_check", self.scale_check)
        self.register_tool("release_agent", self.release_agent)

    def schedule_agents(self, plan: dict = None, available_agents: list = None) -> dict:
        """为子任务分配Agent（最少负载优先）。"""
        plan = plan or {}
        subtasks = plan.get("subtasks", [])
        available = available_agents or ["翻译官", "商务经理", "售前经理"]
        assignments = []

        for subtask in subtasks:
            agent_type = subtask.get("agent_type", "通用Agent")
            matching = [a for a in available if agent_type in a or a in agent_type]
            if matching:
                best = min(matching, key=lambda a: self._agent_loads.get(a, 0))
                self._agent_loads[best] = self._agent_loads.get(best, 0) + 1
                assignments.append({
                    "subtask_id": subtask["id"],
                    "agent_id": best,
                    "reason": "least_loaded",
                })
            else:
                assignments.append({
                    "subtask_id": subtask["id"],
                    "agent_id": None,
                    "reason": "no_matching_agent",
                })

        return {
            "assignments": assignments,
            "total_assigned": sum(1 for a in assignments if a["agent_id"]),
            "total_unassigned": sum(1 for a in assignments if not a["agent_id"]),
            "load_distribution": dict(self._agent_loads),
        }

    def get_agent_load(self) -> dict:
        """获取Agent负载。"""
        return {
            "loads": dict(self._agent_loads),
            "total_tasks": sum(self._agent_loads.values()),
            "active_agents": len([a for a, c in self._agent_loads.items() if c > 0]),
        }

    def report_crash(self, agent_id: str = "", error: str = "") -> dict:
        """Agent崩溃后触发重启策略 (ONE_FOR_ONE)。"""
        now = time.time()
        if now - self._crash_times.get(agent_id, 0) < RESTART_PERIOD:
            self._crash_counts[agent_id] = self._crash_counts.get(agent_id, 0) + 1
        else:
            self._crash_counts[agent_id] = 1
        self._crash_times[agent_id] = now

        if self._crash_counts[agent_id] > MAX_RESTARTS:
            return {"action": "escalate", "reason": f"超过最大重启次数({MAX_RESTARTS})", "agent_id": agent_id}

        return {"action": "restart", "agents_to_restart": [agent_id], "strategy": "one_for_one"}

    def scale_check(self) -> dict:
        """弹性伸缩检查。"""
        total_load = sum(self._agent_loads.values())
        if total_load >= 8:
            return {"action": "scale_up", "reason": f"高负载({total_load} tasks)", "suggest_count": 2}
        elif total_load <= 1 and len(self._agent_loads) > 3:
            return {"action": "scale_down", "reason": "低负载", "suggest_count": 1}
        return {"action": "no_change", "reason": "load_normal"}

    def release_agent(self, agent_id: str = "") -> dict:
        """释放Agent负载计数。"""
        if agent_id in self._agent_loads:
            self._agent_loads[agent_id] = max(0, self._agent_loads[agent_id] - 1)
        return {"agent_id": agent_id, "current_load": self._agent_loads.get(agent_id, 0)}


if __name__ == "__main__":
    run_mcp_server("L3_coordination", CoordinationServer(), L3_COORDINATION_PORT)
