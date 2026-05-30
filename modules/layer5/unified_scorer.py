"""
UnifiedScorer — 统一评分总线
==============================
融合所有评分源: AutoScorer(rule) + CardScorer(schema) + LLMJudge(llm) + HybridScorer(human)

策略:
  - "fast":     rule + card，不调 LLM（< 100ms）
  - "standard": rule + card + llm（LLM 失败降级，~5s）
  - "deep":     rule + card + llm + human（全部源，~10s）

输出:
  - overall 评分 + 多维度详情
  - 进化信号: prompt_needs_optimization, knowledge_gap, agent_mismatch, template_promotable
  - verdict: pass | retry | reject
"""
import json
import time
import os
from typing import Dict, List, Optional


class UnifiedScorer:
    """统一评分总线 — 单入口，多源融合"""

    STRATEGIES = {
        "fast": {
            "sources": ["rule", "card"],
            "llm_fallback": False,
            "timeout_ms": 100,
            "description": "快速评分：规则+Schema校验，适合高频简单任务"
        },
        "standard": {
            "sources": ["rule", "card", "llm"],
            "llm_fallback": True,
            "timeout_ms": 5000,
            "description": "标准评分：规则+Schema+LLM，LLM失败时降级"
        },
        "deep": {
            "sources": ["rule", "card", "llm", "human"],
            "llm_fallback": False,
            "timeout_ms": 10000,
            "description": "深度评分：全部评分源，不允许降级"
        },
    }

    # 评分源权重（用于融合）
    SOURCE_WEIGHTS = {"llm": 0.40, "rule": 0.30, "card": 0.20, "human": 0.10}

    # 默认维度权重（用于降级到规则评分）
    DEFAULT_DIMENSIONS = {
        "accuracy": 0.25,
        "completeness": 0.25,
        "professionalism": 0.20,
        "actionability": 0.15,
        "consistency": 0.15,
    }

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._llm_judge = None
        self._hybrid_scorer = None

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    def score(self,
              task: dict,
              result: dict,
              strategy: str = "standard",
              agent_card: dict = None,
              human_review: dict = None) -> dict:
        """
        统一评分入口

        Args:
            task: {"task_id","action","description","type"}
            result: {"output","subtasks",...} — L4 执行结果
            strategy: "fast"|"standard"|"deep"
            agent_card: Agent 的能力卡片（用于 Schema 校验）
            human_review: {"overall": 7.5, "scores": {...}, "reviewer_id": "..."}

        Returns:
            {
                "overall": 7.5, "passed": true, "verdict": "pass",
                "sources_used": ["rule","card","llm"],
                "dimensions": {"accuracy":8.0,...},
                "key_issues": [...], "suggestions": [...],
                "evolution_signals": {
                    "prompt_needs_optimization": false,
                    "knowledge_gap": false,
                    "agent_mismatch": false,
                    "template_promotable": false,
                    "suggested_search_queries": []
                }
            }
        """
        cfg = self.STRATEGIES.get(strategy, self.STRATEGIES["standard"])
        scores_from_sources = {}
        all_dimensions = {}
        all_issues = []
        all_suggestions = []
        sources_used = []

        # ── Source 1: RuleScorer (零成本，总是执行) ──
        if "rule" in cfg["sources"]:
            try:
                rule_score = self._rule_score(task, result)
                scores_from_sources["rule"] = rule_score
                sources_used.append("rule")
                if rule_score.get("dimensions"):
                    all_dimensions.update(rule_score["dimensions"])
                if rule_score.get("issues"):
                    all_issues.extend(rule_score["issues"])
            except Exception as e:
                print(f"[UnifiedScorer] rule scoring failed: {e}", flush=True)

        # ── Source 2: CardScorer (能力卡片 Schema 校验) ──
        if "card" in cfg["sources"] and agent_card:
            try:
                card_score = self._card_score(result, agent_card)
                scores_from_sources["card"] = card_score
                sources_used.append("card")
                if card_score.get("dimensions"):
                    all_dimensions.update(card_score["dimensions"])
                if card_score.get("missing_fields"):
                    all_issues.append(
                        f"缺失输出字段: {', '.join(card_score['missing_fields'])}"
                    )
            except Exception as e:
                print(f"[UnifiedScorer] card scoring failed: {e}", flush=True)

        # ── Source 3: LLMJudge (深度评分) ──
        if "llm" in cfg["sources"]:
            try:
                llm_score = self._llm_score(task, result)
                if llm_score.get("method") != "fallback":
                    scores_from_sources["llm"] = llm_score
                    sources_used.append("llm")
                    if llm_score.get("dimensions"):
                        all_dimensions.update(llm_score["dimensions"])
                    if llm_score.get("issues"):
                        all_issues.extend(llm_score["issues"])
                    if llm_score.get("suggestions"):
                        all_suggestions.extend(llm_score["suggestions"])
            except Exception as e:
                print(f"[UnifiedScorer] LLM scoring failed: {e}", flush=True)
                if not cfg["llm_fallback"]:
                    raise

        # ── Source 4: Human (人类校准) ──
        if "human" in cfg["sources"] and human_review:
            try:
                human_score = self._human_score(
                    task.get("task_id", ""),
                    self._rough_overall(all_dimensions),
                    human_review
                )
                if human_score.get("source") == "hybrid":
                    scores_from_sources["human"] = human_score
                    sources_used.append("human")
            except Exception as e:
                print(f"[UnifiedScorer] human scoring failed: {e}", flush=True)

        # ── 融合所有评分源 ──
        return self._fuse(task, result, scores_from_sources, all_dimensions,
                          all_issues, all_suggestions, sources_used, agent_card)

    # ═══════════════════════════════════════════════
    # 评分源实现
    # ═══════════════════════════════════════════════

    def _rule_score(self, task: dict, result: dict) -> dict:
        """规则评分 — 优先内置增强规则, AutoScorer 作为补充"""
        # 先用内置增强规则（5维标准）
        builtin = self._builtin_rule_score(task, result)
        # 尝试 AutoScorer 补充
        try:
            from modules.layer4.auto_scorer import AutoScorer
            auto = AutoScorer().score(task, result)
            # 取两者中较高的分数
            if auto.get("overall", 0) > builtin.get("overall", 0):
                return auto
        except ImportError:
            pass
        return builtin

    def _builtin_rule_score(self, task: dict, result: dict) -> dict:
        """内置规则评分 — 5维标准, 从输出中提取真实信号"""
        status = result.get("status", "")
        output = str(result.get("output", result.get("stdout", "")))
        subtasks = result.get("subtasks", [])
        output_len = len(output)

        # ── 信号提取 ──
        has_code = "```" in output or "`" in output
        has_struct = any(m in output for m in ("##", "###", "**", "| ", "1.", "2.", "-"))
        has_findings = any(kw in output.lower() for kw in
                          ("发现", "问题", "建议", "结论", "诊断", "分析",
                           "finding", "issue", "recommend", "diagnos", "analys"))
        has_report = output.startswith("##") or output.startswith("# ")
        has_numbers = any(c.isdigit() for c in output[:200])
        is_long = output_len > 800

        # ── 5维评分 (accuracy/completeness/professionalism/actionability/consistency) ──

        # accuracy: 输出是否准确、有数据支撑
        accuracy = 5.0
        if has_findings and has_numbers:
            accuracy += 2.0
        if has_struct:
            accuracy += 1.0
        if output_len > 1500:
            accuracy += 1.0
        if "错误" in output or "error" in output.lower():
            accuracy += 0.5  # 诚实报告错误也是准确

        # completeness: 是否完整覆盖了任务
        if subtasks:
            done = sum(1 for s in subtasks if s.get("status") in ("completed", "success", "dispatched"))
            completeness = 4.0 + (done / max(len(subtasks), 1)) * 6.0
        elif status == "success":
            completeness = 7.0 if is_long else 5.0
            if has_report:
                completeness += 1.0
            if has_findings:
                completeness += 1.0
        elif status == "failed":
            completeness = 3.0 if output_len > 100 else 2.0
        else:
            completeness = 5.0

        # professionalism: 输出格式是否专业
        professionalism = 5.0
        if has_report:
            professionalism += 2.0
        if has_struct:
            professionalism += 1.0
        if is_long:
            professionalism += 1.0
        if has_code:
            professionalism += 0.5

        # actionability: 输出是否可执行、有下一步
        actionability = 5.0
        if has_findings:
            actionability += 2.0
        if has_code:
            actionability += 1.0
        if any(kw in output.lower() for kw in ("建议", "下一步", "修正", "recommend", "action")):
            actionability += 1.5

        # consistency: 格式一致性
        consistency = 6.0
        if has_report:
            consistency += 1.5
        if has_struct and has_numbers:
            consistency += 1.0
        if output_len > 1000:
            consistency += 0.5

        dims = {
            "accuracy": round(min(10, accuracy), 1),
            "completeness": round(min(10, completeness), 1),
            "professionalism": round(min(10, professionalism), 1),
            "actionability": round(min(10, actionability), 1),
            "consistency": round(min(10, consistency), 1),
        }
        overall = round(sum(dims.values()) / 5, 1)

        return {
            "overall": overall,
            "method": "rule_enhanced",
            "dimensions": dims,
            "issues": [],
            "signals": {"code": has_code, "struct": has_struct,
                        "findings": has_findings, "report": has_report},
        }

    def _card_score(self, result: dict, agent_card: dict) -> dict:
        """基于能力卡片 output_schema 校验输出完整性"""
        output_schema = agent_card.get("output_schema", {})
        required_fields = output_schema.get("required", [])

        if not required_fields:
            return {"overall": 7.0, "dimensions": {}, "method": "card_no_schema"}

        output = result.get("output", result.get("stdout", ""))
        output_dict = output if isinstance(output, dict) else {}

        present = 0
        missing = []
        for field in required_fields:
            if isinstance(output_dict, dict) and field in output_dict:
                present += 1
            elif field in str(output):
                present += 1
            else:
                missing.append(field)

        completeness = (present / max(len(required_fields), 1)) * 10

        return {
            "overall": round(completeness, 1),
            "method": "card_schema",
            "dimensions": {"schema_completeness": round(completeness, 1)},
            "missing_fields": missing,
        }

    def _llm_score(self, task: dict, result: dict) -> dict:
        """LLM-as-Judge 深度评分"""
        try:
            from modules.layer4.llm_judge import LLMJudge
            if not self._llm_judge:
                self._llm_judge = LLMJudge(
                    llm_client=self._get_llm_client(),
                    redis_client=self.redis
                )
            return self._llm_judge.evaluate_sync(task, result)
        except ImportError:
            return {"overall": 5.0, "method": "fallback", "dimensions": {}}

    def _human_score(self, task_id: str, ai_score: float,
                     human_review: dict = None) -> dict:
        """人类评分融合 (HybridScorer)"""
        try:
            from tools.hybrid_scorer import HybridScorer
            if not self._hybrid_scorer:
                self._hybrid_scorer = HybridScorer()
            return self._hybrid_scorer.calculate(task_id, ai_score, human_review)
        except ImportError:
            return {"score": ai_score, "source": "ai_only"}

    # ═══════════════════════════════════════════════
    # 融合逻辑
    # ═══════════════════════════════════════════════

    def _fuse(self, task, result, scores_from_sources, all_dimensions,
              all_issues, all_suggestions, sources_used, agent_card) -> dict:
        """多源评分融合 + 进化信号提取"""

        # ── 加权融合 overall ──
        total_weight = sum(
            self.SOURCE_WEIGHTS.get(s, 0.1) for s in scores_from_sources
        )

        if total_weight == 0:
            overall = 5.0
            weighted_dims = {}
        else:
            overall_sum = sum(
                scores_from_sources[s].get("overall", 5.0) * self.SOURCE_WEIGHTS.get(s, 0.1)
                for s in scores_from_sources
            )
            overall = round(overall_sum / total_weight, 1)

            # ── 按维度融合 ──
            weighted_dims = {}
            dim_weight_sums = {}
            for source, score_data in scores_from_sources.items():
                w = self.SOURCE_WEIGHTS.get(source, 0.1)
                dims = score_data.get("dimensions", score_data.get("details", {}))
                for dim, val in dims.items():
                    if isinstance(val, (int, float)):
                        weighted_dims[dim] = weighted_dims.get(dim, 0) + val * w
                        dim_weight_sums[dim] = dim_weight_sums.get(dim, 0) + w
            for dim in weighted_dims:
                if dim_weight_sums.get(dim, 0) > 0:
                    weighted_dims[dim] = round(weighted_dims[dim] / dim_weight_sums[dim], 1)

        # ── 判定 verdict ──
        if overall >= 7.0:
            verdict = "pass"
        elif overall >= 4.0:
            verdict = "retry"
        else:
            verdict = "reject"

        # ── 进化信号提取 ──
        evolution_signals = self._extract_evolution_signals(
            task, result, scores_from_sources, all_issues, overall, agent_card
        )

        # ── 兜底维度 ──
        if not weighted_dims:
            weighted_dims = {
                "accuracy": 5, "completeness": 5,
                "professionalism": 5, "actionability": 5, "consistency": 5,
            }

        return {
            "overall": overall,
            "passed": overall >= 6.0,
            "verdict": verdict,
            "sources_used": sources_used,
            "dimensions": weighted_dims,
            "key_issues": all_issues[:5],
            "suggestions": all_suggestions[:3],
            "evolution_signals": evolution_signals,
            "method": "unified_scorer",
        }

    def _extract_evolution_signals(self, task, result, scores, issues, overall,
                                    agent_card) -> dict:
        """从评分结果中提取进化信号"""
        signals = {
            "prompt_needs_optimization": False,
            "knowledge_gap": False,
            "agent_mismatch": False,
            "suggested_alternative_agent": None,
            "template_promotable": False,
            "suggested_search_queries": [],
        }

        issues_text = " ".join(str(i) for i in issues).lower()

        # 1. Prompt 优化信号: 格式/结构问题
        format_keywords = ["format", "structure", "json", "parse",
                           "格式", "结构", "解析", "输出格式"]
        if any(kw in issues_text for kw in format_keywords):
            signals["prompt_needs_optimization"] = True

        # 2. 知识缺口信号: 缺少知识/数据
        knowledge_keywords = ["knowledge", "data", "unknown", "not found",
                              "缺少", "未知", "没有", "不确定", "无法确认"]
        if any(kw in issues_text for kw in knowledge_keywords):
            signals["knowledge_gap"] = True
            for issue in issues[:2]:
                if isinstance(issue, str) and len(issue) > 10:
                    signals["suggested_search_queries"].append(issue[:100])

        # 3. Agent 不匹配: 卡片校验差但 LLM 认为好
        card_overall = scores.get("card", {}).get("overall", 7)
        llm_overall = scores.get("llm", {}).get("overall", 7)
        if card_overall < 5 and llm_overall > 6:
            signals["agent_mismatch"] = True
            # 尝试找到替代 Agent
            signals["suggested_alternative_agent"] = self._suggest_alternative(
                task, agent_card
            )

        # 4. 模板提升信号: 高分
        if overall >= 8.0:
            signals["template_promotable"] = True

        return signals

    def _suggest_alternative(self, task: dict, current_card: dict) -> Optional[str]:
        """建议替代 Agent"""
        if not self.redis or not current_card:
            return None
        task_action = task.get("action", "")
        current_name = current_card.get("name", "")
        try:
            agent_list = self.redis.smembers("agent:registry") or []
            for agent_name in agent_list:
                if agent_name == current_name:
                    continue
                card_raw = self.redis.get(f"agent:card:{agent_name}")
                if not card_raw:
                    continue
                card = json.loads(card_raw)
                role = card.get("role", "").lower()
                skills = card.get("skills", [])
                if role and any(w in task_action.lower() for w in role.split()):
                    return agent_name
                for skill in skills:
                    if skill.replace("-", " ") in task_action.lower():
                        return agent_name
        except Exception:
            pass
        return None

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _rough_overall(self, dimensions: dict) -> float:
        """从维度字典估算 overall"""
        if not dimensions:
            return 5.0
        return round(sum(dimensions.values()) / len(dimensions), 1)

    def _get_llm_client(self):
        """获取 LLM 客户端（用于 LLMJudge）"""
        try:
            from openai import OpenAI
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key and self.redis:
                api_key = self.redis.get("yaxiio:config:llm_api_key") or ""
            if api_key:
                return OpenAI(api_key=api_key, base_url="https://api.deepseek.com/v1")
        except Exception:
            pass
        return None
