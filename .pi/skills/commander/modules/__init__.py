
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
雅溪 Yaxiio v1 Modules — 统一入口 (v3.1)
=======================================
通过 SessionManager 统一对外暴露所有模块接口。

目录结构:
  modules/
    __init__.py            — 统一入口 CommanderModules
    session/
      __init__.py
      session_manager.py   — Part A: 会话分离/seq/Lamport/令牌
      ws_bridge_v3.py      — Part A: WebSocket双心跳
    optimization/
      __init__.py
      failure_recovery.py  — Part B: 五种失败恢复策略
      llm_scorer.py        — Part B: LLM自动评分
      audit_logger.py      — Part B: 审计日志
      planner_coordinator.py — Part B: Planner/Coordinator/Worker
      skill_generator.py   — Part B: Skill自动生成 + 五层架构
      workflow_optimizer.py — Part B8: 工作流拓扑优化
      optimization_algorithms.py — Part B9: TextGrad/AFlow/MIPRO
      zero_token_router.py — Part B10-11: 零Token路由 + 工作流快照
      supervision_tree.py  — Part B12: 监督树
    test_modules.py        — 测试套件
"""

import os
import sys
import time

# 包内导入
from .session.session_manager import SessionManager
from .optimization.failure_recovery import FailureRecovery
from .optimization.llm_scorer import LLMScorer
from .optimization.audit_logger import AuditLogger
from .optimization.planner_coordinator import Planner, Coordinator
from .optimization.skill_generator import SkillGenerator, FiveLayerArchitecture
from .optimization.workflow_optimizer import WorkflowTopologyOptimizer
from .optimization.optimization_algorithms import OptimizationMCPHub
from .optimization.zero_token_router import ZeroTokenRouter, WorkflowSnapshotManager
from .optimization.supervision_tree import SupervisionTree


class CommanderModules:
    """雅溪 Yaxiio v1.1 模块集合。"""

    def __init__(self, llm_client=None, agent_factory=None):
        # Session 层
        self.session = SessionManager()

        # Optimization 层
        self.failure = FailureRecovery(
            session_manager=self.session,
            llm_client=llm_client,
            agent_factory=agent_factory,
        )
        self.scorer = LLMScorer(llm_client=llm_client)
        self.audit = AuditLogger(mongo_db=self.session._mongo)

        # 解耦架构
        self.planner = Planner(llm_client=llm_client)
        self.coordinator = Coordinator()
        self.skill_gen = SkillGenerator(llm_client=llm_client)

        # 五层架构
        self.five_layer = FiveLayerArchitecture(
            session_manager=self.session,
            llm_client=llm_client,
        )

        # B8-B12 新模块
        self.workflow = WorkflowTopologyOptimizer(
            redis_client=self.session._redis,
            mongo_db=self.session._mongo,
        )
        self.optimization = OptimizationMCPHub(llm_client=llm_client)
        self.router = ZeroTokenRouter(redis_client=self.session._redis)
        self.snapshots = WorkflowSnapshotManager(mongo_db=self.session._mongo)
        self.supervision = SupervisionTree()

        self._llm = llm_client
        self._start_time = time.time()

    # ── 委托方法 ──────────────────────────────────────

    def create_session(self, fingerprint="", metadata=None):
        return self.session.create_session(fingerprint, metadata)

    def connect(self, token, fingerprint):
        return self.session.connect(token, fingerprint)

    def enqueue_message(self, token, message, depends_on=None):
        return self.session.enqueue_message(token, message, depends_on)

    def score_task(self, task_description, result, agent_id="", elapsed_ms=0, token_count=0):
        return self.scorer.score(task_description, result, agent_id, elapsed_ms, token_count)

    def route_task(self, task: str) -> dict:
        """零Token路由判断。"""
        return self.router.route(task)

    def snapshot_plan(self, task: str, plan: dict, task_type: str = "general") -> str:
        """创建工作流快照。"""
        return self.snapshots.snapshot(task, plan, task_type)

    def register_child(self, parent_id: str, child_spec) -> None:
        """注册子Agent到监督树。"""
        self.supervision.register(parent_id, child_spec)

    def complete_task(self, token: str, task_id: str, agent_id: str,
                      task_description: str, result: dict,
                      elapsed_ms: float = 0) -> dict:
        """任务完成全流程处理。"""
        score = self.score_task(task_description, result, agent_id, elapsed_ms)

        success = not score["needs_replan"]
        self.audit.log(
            level="INFO" if success else "WARN",
            event_type="state_change",
            session_token=token, agent_id=agent_id, task_id=task_id,
            detail={"score": score["overall"], "dimensions": score["dimensions"]},
        )

        msg_seq = self.enqueue_message(token, {
            "type": "task_result", "task_id": task_id,
            "agent_id": agent_id, "result": result, "score": score,
        })

        recovery = None
        if score["needs_replan"]:
            recovery = self.failure.decide(
                task_id, agent_id,
                error=f"Score too low ({score['overall']})",
                elapsed_ms=elapsed_ms, llm_score=score["overall"],
            )

        return {
            "msg_seq": msg_seq, "score": score,
            "needs_recovery": recovery is not None, "recovery": recovery,
        }

    def health_check(self) -> dict:
        return {
            "session": {
                "active_sessions": len(self.session.list_active_sessions()),
                "redis": self.session._redis is not None,
                "mongo": self.session._mongo is not None,
            },
            "scorer": self.scorer.get_stats(),
            "failure": self.failure.get_stats(),
            "audit": self.audit.get_stats(),
            "coordination": self.coordinator.get_load(),
            "skills": len(self.skill_gen.list_skills()),
            "architecture": self.five_layer.get_architecture_state(),
            "router": self.router.get_stats(),
            "snapshots": self.snapshots.get_stats(),
            "supervision": self.supervision.get_stats(),
            "uptime_s": int(time.time() - self._start_time),
        }
