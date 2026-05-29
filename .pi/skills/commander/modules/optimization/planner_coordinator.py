
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Planner-Coordinator-Worker 解耦架构 v3.0
=========================================
吸收自 Camel-ai 的设计理念：规划器、协调器、执行器通过 Redis 解耦，可独立扩展。

架构:
  Planner   → 任务分析 + 拆解 + 生成执行计划
  Coordinator → Agent 调度 + 资源分配 + 负载均衡  
  Worker    → 具体任务执行 (agent-core.py)

通信: Redis Pub/Sub
  planner:plan     → Planner 产出执行计划
  coordinator:assign → Coordinator 分配任务给 Worker
  worker:result    → Worker 返回执行结果

配置:
  PLANNER_LLM_MODEL=deepseek-chat
  COORDINATOR_MAX_CONCURRENT=10
  WORKER_MAX_RETRIES=3
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

PLANNER_LLM_MODEL = os.environ.get("PLANNER_LLM_MODEL", "deepseek-chat")
COORDINATOR_MAX_CONCURRENT = int(os.environ.get("COORDINATOR_MAX_CONCURRENT", "10"))


class Planner:
    """任务规划器 — 拆解复杂任务为可执行的子任务 DAG。"""

    def __init__(self, llm_client=None, redis_client=None):
        self.llm = llm_client
        self.redis = redis_client or self._init_redis()

    def _init_redis(self):
        if not HAS_REDIS:
            return None
        return redis_lib.Redis(protocol=2, host=os.environ.get("REDIS_HOST", "127.0.0.1"),
                                port=int(os.environ.get("REDIS_PORT", "6379")),
                                password=os.environ.get("REDIS_PASSWORD", ""),
                                decode_responses=True)

    def plan(self, task: str, context: dict = None) -> dict:
        """拆解任务为子任务 DAG。

        Returns:
            {
              "plan_id": "plan-xxx",
              "subtasks": [
                {"id": "s1", "action": "...", "agent_type": "...", "depends_on": [], "priority": 1},
                ...
              ]
            }
        """
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"

        if self.llm:
            subtasks = self._llm_plan(task, context)
        else:
            subtasks = self._rule_plan(task)

        plan = {
            "plan_id": plan_id,
            "task": task,
            "subtasks": subtasks,
            "timestamp": time.time(),
            "context": context or {},
        }

        # 发布到 Redis
        if self.redis:
            self.redis.publish("planner:plan", json.dumps(plan, ensure_ascii=False))
            self.redis.setex(f"planner:plan:{plan_id}", 3600,
                             json.dumps(plan, ensure_ascii=False))

        return plan

    def _llm_plan(self, task: str, context: dict) -> list:
        prompt = f"""Break this task into independent subtasks as JSON array.
Task: {task}
Context: {json.dumps(context or {}, ensure_ascii=False)[:200]}

Each subtask: {{"id":"sN","action":"...","agent_type":"翻译官|商务经理|售前经理|通用Agent","depends_on":[],"priority":1}}

Return ONLY JSON array, no markdown."""
        try:
            resp = self.llm.chat(prompt, max_tokens=500)
            return json.loads(resp)
        except Exception:
            return self._rule_plan(task)

    def _rule_plan(self, task: str) -> list:
        """基于关键词的规则拆分（fallback）。"""
        keywords = {
            "翻译": "翻译官", "translate": "翻译官",
            "报价": "售前经理", "quote": "售前经理",
            "客户": "商务经理", "customer": "商务经理",
            "部署": "通用Agent", "deploy": "通用Agent",
            "审计": "通用Agent", "audit": "通用Agent",
        }
        for kw, agent in keywords.items():
            if kw in task.lower():
                return [{"id": "s1", "action": task, "agent_type": agent, "depends_on": [], "priority": 1}]
        return [{"id": "s1", "action": task, "agent_type": "通用Agent", "depends_on": [], "priority": 1}]


class Coordinator:
    """任务协调器 — Agent 调度 + 负载均衡。"""

    def __init__(self, redis_client=None):
        self.redis = redis_client or Planner()._init_redis()
        self._agent_load: Dict[str, int] = {}  # agent_id → active tasks

    def assign(self, subtask: dict, available_agents: List[str]) -> dict:
        """为子任务选择最优 Agent。

        Returns:
            {"agent_id": "...", "task_id": "...", "reason": "..."}
        """
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        agent_type = subtask.get("agent_type", "通用Agent")
        matching = [a for a in available_agents if agent_type in a or a in agent_type]

        if not matching:
            return {"agent_id": None, "task_id": task_id, "reason": f"无匹配 {agent_type}"}

        # 最少负载优先
        best = min(matching, key=lambda a: self._agent_load.get(a, 0))
        self._agent_load[best] = self._agent_load.get(best, 0) + 1

        # 发布分配通知
        if self.redis:
            self.redis.publish("coordinator:assign", json.dumps({
                "agent_id": best, "task_id": task_id, "subtask": subtask,
            }, ensure_ascii=False))

        return {"agent_id": best, "task_id": task_id, "reason": "least_loaded"}

    def release(self, agent_id: str):
        """Agent 完成任务后释放负载计数。"""
        self._agent_load[agent_id] = max(0, self._agent_load.get(agent_id, 1) - 1)

    def get_load(self) -> dict:
        """当前负载分布。"""
        return {
            "total_agents": len(self._agent_load),
            "total_tasks": sum(self._agent_load.values()),
            "max_concurrent": COORDINATOR_MAX_CONCURRENT,
            "loads": dict(self._agent_load),
        }

    def can_accept(self) -> bool:
        """检查是否还能接受新任务。"""
        return sum(self._agent_load.values()) < COORDINATOR_MAX_CONCURRENT


class Worker:
    """任务执行器 — 封装 agent-core.py 的标准化接口。"""

    def __init__(self, agent_id: str, redis_client=None):
        self.agent_id = agent_id
        self.redis = redis_client or Planner()._init_redis()
        self.task_count = 0
        self.fail_count = 0
        self.start_time = time.time()

    def execute(self, task: dict) -> dict:
        """执行任务并返回结果。

        task 必须包含 'command' 字段（shell命令）或 'action' 字段。
        """
        self.task_count += 1
        task_id = task.get("task_id", "unknown")

        try:
            if "command" in task:
                import subprocess
                proc = subprocess.run(
                    task["command"], shell=True,
                    capture_output=True, text=True, timeout=120
                )
                result = {
                    "status": "success" if proc.returncode == 0 else "failed",
                    "stdout": proc.stdout[:2000],
                    "stderr": proc.stderr[:500],
                    "exit_code": proc.returncode,
                }
            else:
                result = {"status": "done", "note": f"{self.agent_id} executed"}

            # 发布结果
            if self.redis:
                self.redis.publish("worker:result", json.dumps({
                    "agent_id": self.agent_id, "task_id": task_id,
                    "result": result, "timestamp": time.time(),
                }, ensure_ascii=False))

            return result

        except Exception as e:
            self.fail_count += 1
            error_result = {"status": "failed", "error": str(e)}
            if self.redis:
                self.redis.publish("worker:result", json.dumps({
                    "agent_id": self.agent_id, "task_id": task_id,
                    "result": error_result, "error": str(e),
                }, ensure_ascii=False))
            return error_result

    def health(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "tasks": self.task_count,
            "fails": self.fail_count,
            "uptime_s": int(time.time() - self.start_time),
        }
