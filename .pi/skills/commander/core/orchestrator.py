
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
雅溪 Yaxiio Orchestrator — 五层编排核心
============================================
通过 MCP 协议编排五层:
  L1 → L2 → L3 → L4 → L5

每层作为独立 MCP Server，松耦合，可独立部署/升级。

启动:
  python3 core/orchestrator.py
"""

import sys, os, time, json, uuid

sys.path.insert(0, '/opt/commander')

from config import (
    LAYER_URLS, DASHBOARD_PORT, COMMANDER_VERSION,
    REDIS_HOST, REDIS_PORT, REDIS_PASSWORD,
)
from mcp.protocol import MCPClient, MCPHub


class Orchestrator:
    """Commander 主调度器 — 通过 MCP 编排五层。"""

    def __init__(self):
        # 连接五层
        self.hub = MCPHub()
        for name, url in LAYER_URLS.items():
            self.hub.register_layer(name, url)
            print(f"[Orchestrator] 🔌 {name}: {url}")

        self.task_count = 0
        self.start_time = time.time()

    def process(self, task: str) -> dict:
        """完整的五层处理流程。"""
        self.task_count += 1
        result = {"task_id": f"task-{uuid.uuid4().hex[:8]}", "task": task, "layers": {}}
        t0 = time.time()

        # ── L1: Perception ──────────────────────────
        l1 = self.hub.get_client("perception")
        if l1:
            perception = l1.call_tool("analyze_intent", {"text": task})
            result["layers"]["perception"] = perception
        else:
            perception = {"primary_intent": "general", "intents": ["general"]}

        # ── L2: Planning ────────────────────────────
        l2 = self.hub.get_client("planning")
        if l2:
            plan = l2.call_tool("decompose_task", {
                "intent": perception.get("primary_intent", "general"),
                "context": {"task": task},
            })
            result["layers"]["planning"] = {
                "plan_id": plan.get("plan_id"),
                "subtasks": plan.get("total_subtasks", 0),
            }
        else:
            plan = {"subtasks": [], "plan_id": None}

        # ── L3: Coordination ────────────────────────
        l3 = self.hub.get_client("coordination")
        if l3 and plan.get("subtasks"):
            schedule = l3.call_tool("schedule_agents", {
                "plan": plan,
                "available_agents": ["翻译官", "商务经理", "售前经理"],
            })
            result["layers"]["coordination"] = {
                "assigned": schedule.get("total_assigned", 0),
                "unassigned": schedule.get("total_unassigned", 0),
            }
        else:
            result["layers"]["coordination"] = {"assigned": 0}

        # ── L4: Execution ───────────────────────────
        l4 = self.hub.get_client("execution")
        if l4 and plan.get("subtasks"):
            # 执行第一个子任务作为演示
            first = plan["subtasks"][0]
            exec_result = l4.call_tool("execute_task", {
                "agent_id": first.get("agent_type", "general"),
                "command": f"echo '{first.get('action', task[:80])}'",
            })
            result["layers"]["execution"] = {
                "status": exec_result.get("status", "unknown"),
                "elapsed_ms": exec_result.get("elapsed_ms", 0),
            }
        else:
            result["layers"]["execution"] = {"status": "skipped"}

        # ── L5: Evolution ───────────────────────────
        l5 = self.hub.get_client("evolution")
        if l5:
            score = l5.call_tool("score_task", {
                "task": task,
                "result": result["layers"].get("execution", {}),
                "agent_id": "orchestrator",
                "elapsed_ms": int((time.time() - t0) * 1000),
            })
            result["layers"]["evolution"] = {
                "score": score.get("overall"),
                "needs_review": score.get("needs_review"),
            }
        else:
            result["layers"]["evolution"] = {"score": None}

        result["total_elapsed_ms"] = int((time.time() - t0) * 1000)
        return result

    def health_check(self) -> dict:
        """全层健康检查。"""
        return {
            "orchestrator": "running",
            "version": COMMANDER_VERSION,
            "uptime_s": int(time.time() - self.start_time),
            "tasks_processed": self.task_count,
            "layers": self.hub.health_check_all(),
            "tools": self.hub.list_all_tools(),
        }


# ═══════════════════════════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════════════════════════

def main():
    print(f"⚡ 雅溪 Yaxiio Orchestrator v{COMMANDER_VERSION}")
    print(f"   五层架构: L1(3401)→L2(3402)→L3(3403)→L4(3404)→L5(3405)")

    orch = Orchestrator()

    # 健康检查
    health = orch.health_check()
    print(f"\n   Layer Status: {json.dumps(health['layers'], indent=2)}")

    # 测试任务
    test_task = "翻译 Hello 到俄语"
    print(f"\n   📥 测试任务: {test_task}")
    result = orch.process(test_task)
    print(f"   📤 结果: {json.dumps(result['layers'], indent=2, ensure_ascii=False)}")
    print(f"   ⏱️  总耗时: {result['total_elapsed_ms']}ms")

    print(f"\n✅ Orchestrator 就绪 (version={COMMANDER_VERSION})")


if __name__ == "__main__":
    main()
