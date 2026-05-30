"""
UniversalGapAnalyzer — 通用差距分析器
=======================================
零行业硬编码。完全基于能力卡片的 output_schema 和 L5 评分维度。

替代 gap_analyzer.py 中硬编码的 "mixed_lang", "empty_fields", "missing_pages"。

差距类型:
  1. Schema 缺失 — output 缺少能力卡片要求的必需字段
  2. 低分维度 — 某一评分维度未达标 (< 7)
  3. Agent 不匹配 — 卡片校验低但 LLM 评分高
  4. 知识缺口 — 缺少领域知识/数据
  5. Prompt 优化 — 输出格式/结构问题
"""
from typing import Dict, List


class UniversalGapAnalyzer:
    """通用差距分析器 — 零行业硬编码"""

    # 默认维度→Agent 映射（可被能力卡片 override）
    DEFAULT_DIM_AGENTS: Dict[str, str] = {
        "accuracy": "审计官",
        "completeness": "LM内容工程师",
        "professionalism": "品牌策略师",
        "actionability": "售前经理",
        "consistency": "翻译官",
        "code_quality": "前端工程师",
        "design": "UI/UX设计师",
        "schema_completeness": "审计官",
        "quality": "审计官",
        "structure": "LM内容工程师",
    }

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    def analyze(self,
                task: dict,
                results: dict,
                l5_scores: dict,
                agent_card: dict = None) -> dict:
        """
        分析任务差距并生成改进行动

        Args:
            task: {"action", "description", ...}
            results: 子任务执行结果 dict
            l5_scores: UnifiedScorer.score() 的输出（含 evolution_signals）
            agent_card: 主要 Agent 的能力卡片

        Returns:
            {
                "has_gap": True/False,
                "gap_summary": "一句话总结",
                "next_actions": [
                    {"action": "...", "agent": "...", "description": "...",
                     "priority": "high", "intent": "..."}
                ],
                "priority": "high"|"medium"|"low",
                "dimensions": {...},
                "evolution_signals": {...}
            }
        """
        score = l5_scores.get("overall", 5)
        verdict = l5_scores.get("verdict", "pass")
        dimensions = l5_scores.get("dimensions", {})
        evolution_signals = l5_scores.get("evolution_signals", {})

        has_gap = score < 7 or verdict in ("retry", "reject")

        if not has_gap:
            return {
                "has_gap": False,
                "gap_summary": f"目标已达成 (score={score}/10)",
                "next_actions": [],
                "priority": "low",
                "dimensions": dimensions,
                "evolution_signals": evolution_signals,
            }

        actions = []

        # ── Gap 1: Schema 缺失 ──
        missing_fields = l5_scores.get("missing_fields", [])
        if missing_fields:
            card_agent = (agent_card.get("name", "通用Agent")
                          if agent_card else "通用Agent")
            actions.append({
                "action": "补全输出字段",
                "agent": card_agent,
                "description": f"输出缺少必需字段: {', '.join(missing_fields)}",
                "priority": "high",
                "intent": "complete_output",
            })

        # ── Gap 2: 低分维度定向改进 ──
        dim_agents = self._get_dim_agents(agent_card)
        default_agent = (agent_card.get("name", "审计官")
                         if agent_card else "审计官")
        for dim, val in sorted(dimensions.items(), key=lambda x: x[1]):
            if val < 7:
                agent = dim_agents.get(dim, default_agent)
                actions.append({
                    "action": f"提升{dim}维度",
                    "agent": agent,
                    "description": f"当前{dim}评分 {val}/10，需要提升至 ≥7",
                    "priority": "high" if val < 5 else "medium",
                    "intent": "improve_dimension",
                })

        # ── Gap 3: Agent 不匹配 → 建议换 Agent ──
        if evolution_signals.get("agent_mismatch"):
            alt = evolution_signals.get("suggested_alternative_agent")
            if alt:
                actions.append({
                    "action": "更换执行Agent",
                    "agent": alt,
                    "description": f"当前 Agent 可能不匹配此任务，建议更换为 {alt}",
                    "priority": "high",
                    "intent": "reassign_agent",
                })

        # ── Gap 4: 知识缺口 → 建议搜索 ──
        if evolution_signals.get("knowledge_gap"):
            queries = evolution_signals.get("suggested_search_queries", [])
            actions.append({
                "action": "补充领域知识",
                "agent": "_tool_",
                "tool": "web_search",
                "description": f"搜索相关知识: {'; '.join(queries[:2])}",
                "priority": "medium",
                "intent": "research",
            })

        # ── Gap 5: Prompt 优化 ──
        if evolution_signals.get("prompt_needs_optimization"):
            card_agent = (agent_card.get("name", "通用Agent")
                          if agent_card else "通用Agent")
            actions.append({
                "action": "优化Agent提示词",
                "agent": card_agent,
                "description": "输出格式/结构存在问题，建议优化 agent prompt",
                "priority": "medium",
                "intent": "optimize_prompt",
            })

        # 限制最多 3 个行动
        actions = actions[:3]

        return {
            "has_gap": True,
            "gap_summary": f"Score={score}/10, {len(actions)} 个改进项",
            "next_actions": actions,
            "priority": "high" if score < 5 else "medium",
            "dimensions": dimensions,
            "evolution_signals": evolution_signals,
        }

    # ═══════════════════════════════════════════════
    # 子任务生成
    # ═══════════════════════════════════════════════

    def to_subtasks(self, task_id: str, gap: dict, payload: dict,
                    round_num: int) -> list:
        """将差距分析结果转换为可执行的子任务"""
        actions = gap.get("next_actions", [])
        if not actions:
            return []

        subtasks = []
        for i, act in enumerate(actions):
            sid = f"s{round_num}_{i+1}"
            subtask = {
                "id": sid,
                "action": act.get("action", "")[:60],
                "agent": act.get("agent", "审计官"),
                "depends": [],
                "prompt": act.get("description", "")[:500],
                "tool": act.get("tool", ""),
                "priority": act.get("priority", "medium").upper(),
            }
            subtasks.append(subtask)

        return subtasks

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _get_dim_agents(self, agent_card: dict) -> Dict[str, str]:
        """从能力卡片推导谁最适合改进某个维度"""
        if not agent_card:
            return self.DEFAULT_DIM_AGENTS

        # 能力卡片可以定义自己的改进 Agent 映射
        custom = agent_card.get("improvement_agents", {})
        if custom:
            return custom

        return self.DEFAULT_DIM_AGENTS
