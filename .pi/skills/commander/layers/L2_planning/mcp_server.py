
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_L2 = 0x3adc1b50

"""
L2 Planning Server — 规划层 MCP Server
=======================================
工具:
  - decompose_task(intent, context) → {plan_id, subtasks[]}
  - select_strategy(task_type) → {strategy, group}
  - list_skills() → [{name, path, size}]
"""

import sys, os, json, uuid, time
sys.path.insert(0, '/opt/commander')

from mcp.protocol import MCPServer, run_mcp_server
from config import L2_PLANNING_PORT, SKILL_DIR


class PlanningServer(MCPServer):
    """规划层: 任务拆解 + DAG生成。"""

    def __init__(self):
        super().__init__("L2_planning", "Planning Layer — Task Decomposition & DAG Generation")

        self.register_tool("decompose_task", self.decompose_task)
        self.register_tool("select_strategy", self.select_strategy)
        self.register_tool("list_skills", self.list_skills)
        self._skills_cache = {}

    def decompose_task(self, intent: str = "", context: dict = None) -> dict:
        """拆解任务为子任务DAG。"""
        context = context or {}
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        task = context.get("task", intent)

        # 基于意图的拆分策略
        strategies = {
            "translate": [{"id":"s1","action":"translate","agent_type":"翻译官","depends_on":[],"priority":1}],
            "quote": [
                {"id":"s1","action":"analyze_requirements","agent_type":"商务经理","depends_on":[],"priority":1},
                {"id":"s2","action":"search_products","agent_type":"售前经理","depends_on":["s1"],"priority":1},
                {"id":"s3","action":"generate_quote","agent_type":"售前经理","depends_on":["s2"],"priority":2},
            ],
            "deploy": [
                {"id":"s1","action":"build","agent_type":"通用Agent","depends_on":[],"priority":1},
                {"id":"s2","action":"package","agent_type":"通用Agent","depends_on":["s1"],"priority":1},
                {"id":"s3","action":"upload","agent_type":"通用Agent","depends_on":["s2"],"priority":2},
                {"id":"s4","action":"restart","agent_type":"通用Agent","depends_on":["s3"],"priority":2},
            ],
            "audit": [
                {"id":"s1","action":"scan","agent_type":"通用Agent","depends_on":[],"priority":1},
                {"id":"s2","action":"report","agent_type":"通用Agent","depends_on":["s1"],"priority":2},
            ],
        }

        subtasks = strategies.get(intent)
        if subtasks is None:
            # LLM fallback: 未知意图用大模型拆解
            subtasks = self._llm_decompose(intent, task)
            if not subtasks:
                subtasks = [
                    {"id":"s1","action":"execute","agent_type":"通用Agent","depends_on":[],"priority":1}
                ]

        return {
            "plan_id": plan_id,
            "intent": intent,
            "task": task,
            "subtasks": subtasks,
            "total_subtasks": len(subtasks),
            "parallel_groups": self._find_parallel_groups(subtasks),
            "timestamp": time.time(),
        }

    def _llm_decompose(self, intent: str, task: str) -> list:
        """LLM 驱动的任务拆解 — 未知意图的 fallback"""
        import json as _json
        try:
            from openai import OpenAI
            import redis as _r
            r = _r.Redis(host="127.0.0.1", port=6379,
                        password=os.environ.get("REDIS_PASSWORD",""),
                        decode_responses=True, socket_connect_timeout=3)
            key = r.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY","")
            if not key:
                return []
            llm = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
            prompt = f"""Decompose this task into 2-5 subtasks. Output JSON array only.
Available agents: 审计官(audit), 品牌策略师(brand), 翻译官(translate), UI/UX设计师(design), 前端工程师(frontend), LM内容工程师(content)

Task: {task[:400]}"""
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role":"user","content":prompt}],
                temperature=0.3, max_tokens=500,
            )
            text = resp.choices[0].message.content
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"): text = text[4:]
            data = _json.loads(text.strip())
            items = data if isinstance(data, list) else data.get("subtasks", [])
            result = []
            for i, item in enumerate(items):
                if isinstance(item, dict):
                    result.append({
                        "id": item.get("id", f"s{i+1}"),
                        "action": item.get("action", "")[:60],
                        "agent_type": item.get("agent", item.get("agent_type", "审计官")),
                        "depends_on": item.get("depends_on", item.get("depends", [])),
                        "priority": 1,
                    })
            return result if result else []
        except Exception:
            return []

    def _find_parallel_groups(self, subtasks: list) -> list:
        """找出可并行执行的子任务组。"""
        deps = {s["id"]: s.get("depends_on", []) for s in subtasks}
        groups = []
        remaining = set(deps.keys())
        while remaining:
            ready = {tid for tid in remaining if all(
                d not in remaining for d in deps.get(tid, [])
            )}
            if ready:
                groups.append(sorted(ready))
                remaining -= ready
            else:
                break
        return groups

    def select_strategy(self, task_type: str = "general") -> dict:
        """选择执行策略。"""
        strategies = {
            "translate": {"strategy": "direct", "parallel": False, "reason": "single agent task"},
            "quote": {"strategy": "sequential", "parallel": False, "reason": "multi-step pipeline"},
            "deploy": {"strategy": "diamond", "parallel": True, "reason": "build-pack-upload chain"},
            "audit": {"strategy": "map_reduce", "parallel": True, "reason": "scan then aggregate"},
            "general": {"strategy": "adaptive", "parallel": False, "reason": "unknown task type"},
        }
        return strategies.get(task_type, strategies["general"])

    def list_skills(self) -> list:
        """列出已生成的 Skill。"""
        skills = []
        if os.path.isdir(SKILL_DIR):
            for f in os.listdir(SKILL_DIR):
                if f.endswith(".md"):
                    path = os.path.join(SKILL_DIR, f)
                    skills.append({"name": f, "size": os.path.getsize(path)})
        return skills


if __name__ == "__main__":
    run_mcp_server("L2_planning", PlanningServer(), L2_PLANNING_PORT)
