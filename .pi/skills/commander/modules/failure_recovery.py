
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Failure Recovery v3.0 — 五种失败恢复策略
=========================================
Commander 在 Agent 任务失败时自动选择最优恢复策略。

策略:
  RETRY       — 原 Agent 重试 (瞬时失败)
  REASSIGN    — 换一个 Agent 执行 (Agent 故障)
  DECOMPOSE   — 拆分为更小粒度 (任务过大)
  REPLAN      — LLM 重新规划 (语义错误)
  CREATE_WORKER — 创建新的专业 Agent (无可用Agent)

选择逻辑 (优先级):
  1. 失败次数 < 3 → RETRY
  2. Agent 连续失败 → REASSIGN
  3. 任务超时/资源不足 → DECOMPOSE
  4. 语义/逻辑错误 → REPLAN (需 LLM)
  5. 无可用 Agent → CREATE_WORKER

配置:
  MAX_RETRIES=3           最大重试次数
  DECOMPOSE_THRESHOLD=60  超时阈值 (秒)
  REPLAN_MIN_SCORE=3      LLM评分低于此值触发 REPLAN
"""

import os
import time
from enum import Enum

MAX_RETRIES = int(os.environ.get("FAILOVER_MAX_RETRIES", "3"))
DECOMPOSE_THRESHOLD = int(os.environ.get("FAILOVER_DECOMPOSE_THRESHOLD", "60"))
REPLAN_MIN_SCORE = int(os.environ.get("FAILOVER_REPLAN_MIN_SCORE", "3"))


class RecoveryStrategy(Enum):
    RETRY = "retry"
    REASSIGN = "reassign"
    DECOMPOSE = "decompose"
    REPLAN = "replan"
    CREATE_WORKER = "create_worker"


class FailureRecovery:
    """自动选择恢复策略。"""

    def __init__(self, session_manager=None, llm_client=None, agent_factory=None):
        self.session_mgr = session_manager
        self.llm_client = llm_client
        self.agent_factory = agent_factory
        self._failure_counts: dict = {}  # agent_id → count
        self._history: list = []  # 最近失败记录

    def decide(self, task_id: str, agent_id: str, error: str,
               elapsed_ms: float = 0, llm_score: int = None,
               available_agents: list = None) -> dict:
        """分析失败并返回恢复决策。

        Args:
            task_id: 失败任务 ID
            agent_id: 执行失败的 Agent
            error: 错误信息
            elapsed_ms: 任务耗时
            llm_score: LLM 评分 (1-10)
            available_agents: 可用 Agent 列表

        Returns:
            {"strategy": RecoveryStrategy, "reason": str, "params": dict}
        """
        count = self._failure_counts.get(agent_id, 0) + 1
        self._failure_counts[agent_id] = count
        self._history.append({
            "task_id": task_id, "agent_id": agent_id,
            "error": error, "timestamp": time.time(),
        })
        if len(self._history) > 100:
            self._history = self._history[-100:]

        err_lower = error.lower()

        # 策略3: DECOMPOSE — 资源不足/超时 (最高优先级)
        if elapsed_ms / 1000 > DECOMPOSE_THRESHOLD or any(
            kw in err_lower for kw in ["memory", "resource", "too large"]):
            return {
                "strategy": RecoveryStrategy.DECOMPOSE.value,
                "reason": f"任务超时/资源不足 (耗时{elapsed_ms}ms > {DECOMPOSE_THRESHOLD}s)",
                "params": {"elapsed_ms": elapsed_ms, "split_count": 2},
            }

        # 策略4: REPLAN — LLM语义错误 (次高优先级)
        if llm_score is not None and llm_score < REPLAN_MIN_SCORE and self.llm_client:
            return {
                "strategy": RecoveryStrategy.REPLAN.value,
                "reason": f"LLM 评分 {llm_score} < {REPLAN_MIN_SCORE}",
                "params": {"llm_score": llm_score, "original_task_id": task_id},
            }

        # 策略2: REASSIGN / CREATE_WORKER — Agent 故障
        if count >= MAX_RETRIES or any(
            kw in err_lower for kw in ["crash", "timeout", "connection", "dead"]):
            alt_agents = [a for a in (available_agents or []) if a != agent_id]
            if alt_agents:
                return {
                    "strategy": RecoveryStrategy.REASSIGN.value,
                    "reason": f"Agent {agent_id} 连续失败 {count} 次，切换到 {alt_agents[0]}",
                    "params": {"new_agent": alt_agents[0], "failed_agent": agent_id},
                }
            return {
                "strategy": RecoveryStrategy.CREATE_WORKER.value,
                "reason": "无可用替代 Agent",
                "params": {"failed_agent": agent_id, "task_id": task_id},
            }

        # 策略1: RETRY — 瞬时失败 (默认)
        return {
            "strategy": RecoveryStrategy.RETRY.value,
            "reason": f"Agent {agent_id} 瞬时失败 (第{count}次)",
            "params": {"retry_count": count, "delay_s": min(2 ** count, 30)},
        }

    def reset_agent(self, agent_id: str):
        """Agent 成功后重置失败计数。"""
        self._failure_counts.pop(agent_id, None)

    def get_stats(self) -> dict:
        """获取失败统计。"""
        return {
            "failure_counts": dict(self._failure_counts),
            "recent_failures": len(self._history),
            "most_failing_agent": max(self._failure_counts, key=self._failure_counts.get) if self._failure_counts else None,
        }
