
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Optimization Algorithms MCP v3.0 — 吸收自 EvoAgentX
=====================================================
集成三种优化算法作为 MCP Server 接入 Commander:
  - TextGrad: 优化提示词 (Prompt Engineering)
  - AFlow: 优化工作流拓扑 (Topology Optimization)
  - MIPRO: 优化工具配置 (Tool Configuration)

通过 MCP 协议 (JSON-RPC 2.0) 调用，Commander 自主选择最优算法。

环境变量:
  TEXTGRAD_ENABLED=true
  AFLOW_ENABLED=true
  MIPRO_ENABLED=true
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

TEXTGRAD_ENABLED = os.environ.get("TEXTGRAD_ENABLED", "true").lower() == "true"
AFLOW_ENABLED = os.environ.get("AFLOW_ENABLED", "true").lower() == "true"
MIPRO_ENABLED = os.environ.get("MIPRO_ENABLED", "true").lower() == "true"


class TextGradOptimizer:
    """TextGrad — 自动优化 Agent 提示词。

    算法: 基于梯度的方法迭代改进提示词。
    输入: 当前提示词 + 执行反馈
    输出: 优化后的提示词
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client
        self._history: list = []

    def optimize(self, current_prompt: str, feedback: str,
                  max_iterations: int = 3) -> dict:
        """优化提示词。

        Args:
            current_prompt: 当前提示词
            feedback: 执行反馈（为什么失败了/效果不好）
            max_iterations: 最大迭代次数

        Returns:
            {"prompt": "...", "improvement_score": 0.85, "iterations": 2}
        """
        if not self.llm or not TEXTGRAD_ENABLED:
            return self._rule_optimize(current_prompt, feedback)

        best_prompt = current_prompt
        best_score = 0

        for i in range(max_iterations):
            grad_prompt = f"""Analyze this prompt and feedback. Suggest one precise improvement.
Current prompt: {current_prompt[:500]}
Feedback: {feedback[:300]}
Return ONLY the improved prompt, no explanation."""

            try:
                improved = self.llm.chat(grad_prompt, max_tokens=500)
                score = self._evaluate_prompt(improved)
                if score > best_score:
                    best_prompt = improved
                    best_score = score
            except Exception:
                break

        self._history.append({
            "original": current_prompt[:200],
            "optimized": best_prompt[:200],
            "score": best_score,
            "timestamp": time.time(),
        })

        return {
            "prompt": best_prompt,
            "improvement_score": round(best_score, 2),
            "iterations": max_iterations,
            "method": "textgrad",
        }

    def _rule_optimize(self, prompt: str, feedback: str) -> dict:
        """规则优化（fallback）。"""
        improved = prompt
        if "error" in feedback.lower():
            improved += "\n\nIMPORTANT: Handle errors gracefully. On failure, retry once."
        if "slow" in feedback.lower():
            improved += "\n\nBe concise. Return only essential information."
        return {
            "prompt": improved,
            "improvement_score": 0.5,
            "iterations": 1,
            "method": "textgrad-rule",
        }

    def _evaluate_prompt(self, prompt: str) -> float:
        """评估提示词质量。"""
        score = 0.5
        if len(prompt) > 50:
            score += 0.1
        if "example" in prompt.lower() or "示例" in prompt:
            score += 0.15
        if "step" in prompt.lower() or "步骤" in prompt:
            score += 0.1
        if "return" in prompt.lower() or "输出" in prompt:
            score += 0.1
        return min(score, 1.0)


class AFlowOptimizer:
    """AFlow — 自动优化工作流拓扑。

    输入: 任务类型 + 执行历史
    输出: 优化后的 Agent 协作拓扑
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def optimize(self, task_type: str, current_topology: dict,
                  execution_history: List[dict]) -> dict:
        """优化工作流拓扑。

        Returns:
            {"topology": {...}, "changes": ["added parallel step", "removed bottleneck"]}
        """
        if not AFLOW_ENABLED:
            return {"topology": current_topology, "changes": [], "method": "aflow-disabled"}

        changes = self._analyze_bottlenecks(execution_history)

        if self.llm:
            try:
                prompt = f"""Optimize this agent workflow topology.
Task: {task_type}
Current topology: {json.dumps(current_topology)[:300]}
Execution history: {json.dumps(execution_history[-5:])[:300]}
Bottlenecks: {changes}

Suggest topology changes. Return JSON: {{"agents": [...], "edges": [...], "parallel": true/false}}"""
                new_topo = json.loads(self.llm.chat(prompt, max_tokens=300))
                return {
                    "topology": new_topo,
                    "changes": changes,
                    "method": "aflow",
                }
            except Exception:
                pass

        # Fallback: 简单的并行化
        if len(current_topology.get("agents", [])) > 2:
            current_topology["parallel"] = True
            changes.append("enabled_parallel_execution")

        return {
            "topology": current_topology,
            "changes": changes,
            "method": "aflow-rule",
        }

    def _analyze_bottlenecks(self, history: List[dict]) -> list:
        """分析瓶颈。"""
        changes = []
        if not history:
            return changes

        avg_time = sum(h.get("elapsed_ms", 0) for h in history) / len(history)
        if avg_time > 30000:
            changes.append("task_too_slow_suggest_parallel")
        if sum(1 for h in history if h.get("success")) / len(history) < 0.7:
            changes.append("success_rate_low_suggest_retry")
        if len(history) > 10 and avg_time > 10000:
            changes.append("high_volume_suggest_precompute")

        return changes


class MIPROOptimizer:
    """MIPRO — 自动优化工具配置。

    输入: Agent 配置 + 工具列表 + 性能数据
    输出: 优化后的工具选择与参数
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def optimize(self, agent_id: str, current_tools: List[str],
                  performance: List[dict]) -> dict:
        """优化工具配置。"""
        if not MIPRO_ENABLED:
            return {"tools": current_tools, "changes": [], "method": "mipro-disabled"}

        # 分析哪些工具效果好
        tool_scores = {}
        for p in performance:
            tool = p.get("tool", "unknown")
            tool_scores[tool] = tool_scores.get(tool, 0) + (1 if p.get("success") else -1)

        # 保留高分工具
        keep = [t for t in current_tools if tool_scores.get(t, 0) >= 0]
        removed = [t for t in current_tools if t not in keep]

        return {
            "tools": keep,
            "removed": removed,
            "changes": [f"removed_low_performing:{r}" for r in removed],
            "method": "mipro",
        }


class OptimizationMCPHub:
    """优化算法 MCP Hub — 统一入口，按需调用三种优化器。"""

    def __init__(self, llm_client=None):
        self.textgrad = TextGradOptimizer(llm_client)
        self.aflow = AFlowOptimizer(llm_client)
        self.mipro = MIPROOptimizer(llm_client)
        self._llm = llm_client

    def optimize_prompt(self, prompt: str, feedback: str) -> dict:
        """优化提示词 (TextGrad)。"""
        return self.textgrad.optimize(prompt, feedback)

    def optimize_topology(self, task_type: str, topology: dict,
                           history: list) -> dict:
        """优化工作流拓扑 (AFlow)。"""
        return self.aflow.optimize(task_type, topology, history)

    def optimize_tools(self, agent_id: str, tools: list,
                        performance: list) -> dict:
        """优化工具配置 (MIPRO)。"""
        return self.mipro.optimize(agent_id, tools, performance)

    def auto_optimize(self, task_type: str, context: dict) -> dict:
        """自动选择优化策略。"""
        results = {}

        if context.get("prompt"):
            results["textgrad"] = self.optimize_prompt(
                context["prompt"],
                context.get("feedback", "")
            )

        if context.get("topology"):
            results["aflow"] = self.optimize_topology(
                task_type,
                context["topology"],
                context.get("history", [])
            )

        if context.get("tools"):
            results["mipro"] = self.optimize_tools(
                context.get("agent_id", "unknown"),
                context["tools"],
                context.get("performance", [])
            )

        return results
