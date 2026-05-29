
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
LLM Auto Scorer v3.0 — 任务自动评分
====================================
每个任务完成后自动评分 (1-10分)。低于 6 分的任务自动触发修复流程。

评分维度:
  - completeness (1-10): 任务完成度
  - quality (1-10): 输出质量
  - efficiency (1-10): 资源效率 (时间/Token)
  - relevance (1-10): 与预期目标相关性

综合分 = weighted_avg(四维度)
阈值: SCORE_THRESHOLD=6 → 低于此分触发 REPLAN/人工复核
"""

import json
import os
import time
from typing import Any, Dict, Optional

SCORE_THRESHOLD = int(os.environ.get("SCORE_THRESHOLD", "6"))
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")


class LLMScorer:
    """LLM 驱动的任务自动评分。"""

    def __init__(self, llm_client=None):
        self.llm = llm_client
        self._scores: list = []

    def score(self, task_description: str, result: dict,
              agent_id: str = "", elapsed_ms: float = 0,
              token_count: int = 0) -> dict:
        """评估任务并返回 1-10 的综合分。

        Returns:
            {"overall": 7, "dimensions": {...}, "needs_review": False, "needs_replan": False}
        """
        scores = self._estimate_scores(task_description, result, elapsed_ms, token_count)
        overall = round(
            scores.get("completeness", 5) * 0.35 +
            scores.get("quality", 5) * 0.30 +
            scores.get("efficiency", 5) * 0.20 +
            scores.get("relevance", 5) * 0.15
        )
        overall = max(1, min(10, overall))

        record = {
            "agent_id": agent_id,
            "task": task_description[:200],
            "overall": overall,
            "dimensions": scores,
            "elapsed_ms": elapsed_ms,
            "timestamp": time.time(),
        }
        self._scores.append(record)
        if len(self._scores) > 1000:
            self._scores = self._scores[-500:]

        return {
            "overall": overall,
            "dimensions": scores,
            "needs_review": overall < SCORE_THRESHOLD,
            "needs_replan": overall < SCORE_THRESHOLD - 2,
        }

    def _estimate_scores(self, task: str, result: dict,
                          elapsed_ms: float, token_count: int) -> dict:
        """估算四维评分。如果 LLM 可用则用 LLM 评估，否则用规则。"""
        if self.llm:
            try:
                return self._llm_evaluate(task, result)
            except Exception:
                pass
        return self._rule_based_evaluate(task, result, elapsed_ms, token_count)

    def _llm_evaluate(self, task: str, result: dict) -> dict:
        prompt = f"""Rate this task execution on 4 dimensions (1-10 each).
Task: {task[:300]}
Result preview: {json.dumps(result, ensure_ascii=False)[:500]}

Reply with ONLY JSON:
{{"completeness": N, "quality": N, "efficiency": N, "relevance": N}}"""
        resp = self.llm.chat(prompt, max_tokens=100)
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return {"completeness": 5, "quality": 5, "efficiency": 5, "relevance": 5}

    def _rule_based_evaluate(self, task: str, result: dict,
                              elapsed_ms: float, token_count: int) -> dict:
        """规则引擎快速评分。"""
        # completeness: 结果是否有内容
        has_content = bool(result.get("result") or result.get("stdout"))
        completeness = 8 if has_content else 3

        # quality: 是否有错误
        has_error = bool(result.get("error") or result.get("stderr"))
        quality = 3 if has_error else 7

        # efficiency: 耗时是否合理
        if elapsed_ms < 5000:
            efficiency = 9
        elif elapsed_ms < 30000:
            efficiency = 7
        else:
            efficiency = 4

        # relevance: 简单检查
        relevance = 6  # 默认中等

        return {
            "completeness": completeness,
            "quality": quality,
            "efficiency": efficiency,
            "relevance": relevance,
        }

    def get_agent_average(self, agent_id: str) -> dict:
        """获取 Agent 的平均评分。"""
        agent_scores = [s for s in self._scores if s["agent_id"] == agent_id]
        if not agent_scores:
            return {"agent_id": agent_id, "avg_score": 0, "count": 0}
        avg = sum(s["overall"] for s in agent_scores) / len(agent_scores)
        return {
            "agent_id": agent_id,
            "avg_score": round(avg, 2),
            "count": len(agent_scores),
        }

    def get_stats(self) -> dict:
        """全局评分统计。"""
        if not self._scores:
            return {"avg_score": 0, "count": 0, "below_threshold": 0}
        avg = sum(s["overall"] for s in self._scores) / len(self._scores)
        below = sum(1 for s in self._scores if s["overall"] < SCORE_THRESHOLD)
        return {
            "avg_score": round(avg, 2),
            "count": len(self._scores),
            "below_threshold": below,
            "threshold": SCORE_THRESHOLD,
        }
