# L5 进化层重构方案

> 版本: 1.0 | 日期: 2026-05-29
> 涉及文件: `modules/layer5/unified_scorer.py` (新), `modules/layer5/gap_analyzer_v2.py` (新), `modules/layer5/experience_flywheel.py` (新)
> 修改文件: `workflow_engine.py`（`_do_L5`, `_cleanup_task`, `_analyze_gap`）, `modules/layer5/__init__.py`

---

## 一、当前问题

### 1.1 三套评分系统永不交汇

| 评分器 | 维度 | 被谁调用 | 状态 |
|--------|------|---------|------|
| `workflow_engine._do_L5()` | LLM deep_score → rule fallback → hybrid | 主流程 | ✅ 使用中 |
| `AutoScorer.score()` | completeness + code_quality + design | **无人调用** | ❌ 死代码 |
| `LLMJudge.evaluate()` | completeness/accuracy/format/usefulness/efficiency | **无人调用** | ❌ 死代码 |

### 1.2 GapAnalyzer 完全硬编码外贸场景

```python
# gap_analyzer.py — 当前代码
issues = {"mixed_lang": 0, "empty_fields": 0, "missing_pages": 0, "truncated": 0}
# ↑ "混杂语言"、"空字段"、"缺页" 是 LightingMetal 外贸网站的特定问题
```

### 1.3 经验飞轮断裂

经验被写入 Redis List，但从不被通过 Chroma 语义检索、从不回写模板。

### 1.4 DSPy 优化器形同虚设

`import dspy` → ImportError → 静默降级。

---

## 二、目标架构

```
任务完成 → UnifiedScorer (统一评分总线)
              │
              ├─ RuleScorer (零成本快速评分)
              ├─ CardScorer (能力卡片 Schema 校验)
              ├─ LLMJudge (深度评分)
              └─ HybridScorer (人类校准)
              │
              ▼
         进化信号提取:
           - prompt_needs_optimization
           - knowledge_gap
           - agent_mismatch
           - template_promotable
              │
              ▼
         UniversalGapAnalyzer (通用差距分析)
         零行业关键词，基于能力卡片 + L5 维度
              │
              ▼
         ExperienceFlywheel (经验飞轮)
           - 高分(≥8): 模板回写 + 向量化索引
           - 中分(5-7): 创建 A/B 变体
           - 低分(<5): 记录失败模式
```

---

## 三、实现: UnifiedScorer

**新建文件**: `modules/layer5/unified_scorer.py`

```python
"""
UnifiedScorer — 统一评分总线
==============================
融合所有评分源，按策略决定使用哪些评分器。

策略:
  - "fast":     rule + card，不调 LLM
  - "standard": rule + card + llm（LLM 失败降级）
  - "deep":     rule + card + llm + human（全部源）
"""
import json
import time
import os
from typing import Dict, List, Optional


class UnifiedScorer:
    """统一评分总线"""

    STRATEGIES = {
        "fast": {"sources": ["rule", "card"], "llm_fallback": False, "timeout_ms": 100},
        "standard": {"sources": ["rule", "card", "llm"], "llm_fallback": True, "timeout_ms": 5000},
        "deep": {"sources": ["rule", "card", "llm", "human"], "llm_fallback": False, "timeout_ms": 10000},
    }

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._llm_judge = None
        self._hybrid_scorer = None

    def score(self, task: dict, result: dict, strategy: str = "standard",
              agent_card: dict = None, human_review: dict = None) -> dict:
        """
        统一评分入口

        Returns:
            {"overall": 7.5, "passed": true, "verdict": "pass",
             "sources_used": ["rule","card","llm"],
             "dimensions": {...}, "key_issues": [...], "suggestions": [...],
             "evolution_signals": {...}}
        """
        cfg = self.STRATEGIES.get(strategy, self.STRATEGIES["standard"])
        scores_from_sources = {}
        all_dimensions = {}
        all_issues = []
        all_suggestions = []
        sources_used = []

        # Source 1: RuleScorer (零成本，总是执行)
        if "rule" in cfg["sources"]:
            try:
                rule_score = self._rule_score(task, result)
                scores_from_sources["rule"] = rule_score
                sources_used.append("rule")
                if rule_score.get("dimensions"):
                    all_dimensions.update(rule_score["dimensions"])
                if rule_score.get("issues"):
                    all_issues.extend(rule_score["issues"])
            except Exception:
                pass

        # Source 2: CardScorer (能力卡片 Schema 校验)
        if "card" in cfg["sources"] and agent_card:
            try:
                card_score = self._card_score(result, agent_card)
                scores_from_sources["card"] = card_score
                sources_used.append("card")
                if card_score.get("dimensions"):
                    all_dimensions.update(card_score["dimensions"])
                if card_score.get("missing_fields"):
                    all_issues.append(f"缺失输出字段: {', '.join(card_score['missing_fields'])}")
            except Exception:
                pass

        # Source 3: LLMJudge
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
            except Exception:
                if not cfg["llm_fallback"]:
                    raise

        # Source 4: Human
        if "human" in cfg["sources"] and human_review:
            try:
                human_score = self._human_score(task.get("task_id", ""),
                                                 all_dimensions.get("overall", 5), human_review)
                if human_score.get("source") == "hybrid":
                    scores_from_sources["human"] = human_score
                    sources_used.append("human")
            except Exception:
                pass

        return self._fuse(task, result, scores_from_sources, all_dimensions,
                          all_issues, all_suggestions, sources_used, agent_card)

    def _rule_score(self, task: dict, result: dict) -> dict:
        """使用 AutoScorer 或内置简易规则"""
        try:
            from modules.layer4.auto_scorer import AutoScorer
            return AutoScorer().score(task, result)
        except ImportError:
            return self._builtin_rule_score(task, result)

    def _builtin_rule_score(self, task: dict, result: dict) -> dict:
        """内置规则评分（AutoScorer 不可用时）"""
        status = result.get("status", "")
        output = str(result.get("output", result.get("stdout", "")))
        subtasks = result.get("subtasks", [])

        if subtasks:
            done = sum(1 for s in subtasks if s.get("status") in ("completed", "success", "dispatched"))
            completeness = 5.0 + (done / max(len(subtasks), 1)) * 5.0
        elif status == "success":
            completeness = 8.0 if len(output) > 100 else 6.0
        elif status == "failed":
            completeness = 2.0
        else:
            completeness = 5.0

        quality = min(9.0, 4.0 + len(output) / 500) if output else 3.0
        has_structure = "```" in output or "1." in output or "##" in output
        structure = 7.0 if has_structure else 5.0
        overall = round(completeness * 0.4 + quality * 0.3 + structure * 0.3, 1)

        return {
            "overall": overall, "method": "rule",
            "dimensions": {"completeness": round(completeness, 1),
                          "quality": round(quality, 1),
                          "structure": round(structure, 1)},
            "issues": [],
        }

    def _card_score(self, result: dict, agent_card: dict) -> dict:
        """基于能力卡片 output_schema 校验"""
        output_schema = agent_card.get("output_schema", {})
        required_fields = output_schema.get("required", [])
        if not required_fields:
            return {"overall": 7.0, "dimensions": {}, "method": "card_no_schema"}

        output = result.get("output", result.get("stdout", ""))
        output_dict = output if isinstance(output, dict) else {}
        present = 0
        missing = []
        for field in required_fields:
            if (isinstance(output_dict, dict) and field in output_dict) or field in str(output):
                present += 1
            else:
                missing.append(field)

        completeness = (present / max(len(required_fields), 1)) * 10
        return {
            "overall": round(completeness, 1), "method": "card_schema",
            "dimensions": {"schema_completeness": round(completeness, 1)},
            "missing_fields": missing,
        }

    def _llm_score(self, task: dict, result: dict) -> dict:
        """LLM 深度评分"""
        try:
            from modules.layer4.llm_judge import LLMJudge
            if not self._llm_judge:
                self._llm_judge = LLMJudge(llm_client=self._get_llm_client(), redis_client=self.redis)
            return self._llm_judge.evaluate_sync(task, result)
        except ImportError:
            return {"overall": 5.0, "method": "fallback", "dimensions": {}}

    def _human_score(self, task_id: str, ai_score: float, human_review: dict = None) -> dict:
        """人类评分融合"""
        try:
            from tools.hybrid_scorer import HybridScorer
            if not self._hybrid_scorer:
                self._hybrid_scorer = HybridScorer()
            return self._hybrid_scorer.calculate(task_id, ai_score, human_review)
        except ImportError:
            return {"score": ai_score, "source": "ai_only"}

    def _fuse(self, task, result, scores_from_sources, all_dimensions,
              all_issues, all_suggestions, sources_used, agent_card) -> dict:
        """多源评分融合 + 进化信号提取"""
        source_weights = {"llm": 0.40, "rule": 0.30, "card": 0.20, "human": 0.10}
        total_weight = sum(source_weights.get(s, 0.1) for s in scores_from_sources)

        if total_weight == 0:
            overall = 5.0
            weighted_dims = {}
        else:
            overall_sum = sum(
                scores_from_sources[s].get("overall", 5.0) * source_weights.get(s, 0.1)
                for s in scores_from_sources
            )
            overall = round(overall_sum / total_weight, 1)

            weighted_dims = {}
            dim_counts = {}
            for source, score_data in scores_from_sources.items():
                w = source_weights.get(source, 0.1)
                for dim, val in score_data.get("dimensions", score_data.get("details", {})).items():
                    if isinstance(val, (int, float)):
                        weighted_dims[dim] = weighted_dims.get(dim, 0) + val * w
                        dim_counts[dim] = dim_counts.get(dim, 0) + w
            for dim in weighted_dims:
                if dim_counts.get(dim, 0) > 0:
                    weighted_dims[dim] = round(weighted_dims[dim] / dim_counts[dim], 1)

        verdict = "pass" if overall >= 7.0 else ("retry" if overall >= 4.0 else "reject")
        evolution_signals = self._extract_evolution_signals(task, result, scores_from_sources,
                                                            all_issues, overall, agent_card)

        return {
            "overall": overall, "passed": overall >= 6.0, "verdict": verdict,
            "sources_used": sources_used,
            "dimensions": weighted_dims if weighted_dims else {"accuracy": 5, "completeness": 5},
            "key_issues": all_issues[:5], "suggestions": all_suggestions[:3],
            "evolution_signals": evolution_signals,
        }

    def _extract_evolution_signals(self, task, result, scores, issues, overall, agent_card) -> dict:
        """从评分结果中提取进化信号"""
        signals = {
            "prompt_needs_optimization": False, "knowledge_gap": False,
            "agent_mismatch": False, "suggested_alternative_agent": None,
            "template_promotable": False, "suggested_search_queries": [],
        }
        issues_text = " ".join(str(i) for i in issues).lower()

        # Prompt 优化信号
        if any(kw in issues_text for kw in ["format", "structure", "json", "格式", "结构", "parse"]):
            signals["prompt_needs_optimization"] = True

        # 知识缺口信号
        if any(kw in issues_text for kw in ["knowledge", "data", "unknown", "not found",
                                             "缺少", "未知", "没有", "不确定"]):
            signals["knowledge_gap"] = True
            for issue in issues[:2]:
                if isinstance(issue, str) and len(issue) > 10:
                    signals["suggested_search_queries"].append(issue[:100])

        # Agent 不匹配
        card_overall = scores.get("card", {}).get("overall", 7)
        llm_overall = scores.get("llm", {}).get("overall", 7)
        if card_overall < 5 and llm_overall > 6:
            signals["agent_mismatch"] = True

        # 模板提升
        if overall >= 8.0:
            signals["template_promotable"] = True

        return signals

    def _get_llm_client(self):
        """获取 LLM 客户端"""
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
```

---

## 四、实现: UniversalGapAnalyzer

**新建文件**: `modules/layer5/gap_analyzer_v2.py`

```python
"""
UniversalGapAnalyzer — 通用差距分析器
=======================================
零行业硬编码。完全基于能力卡片的 output_schema 和 L5 评分维度。
"""
from typing import Dict, List


class UniversalGapAnalyzer:
    """通用差距分析器"""

    def analyze(self, task: dict, results: dict, l5_scores: dict,
                agent_card: dict = None) -> dict:
        """
        分析任务差距并生成改进行动

        Returns:
            {"has_gap": True/False, "gap_summary": "...",
             "next_actions": [{...}], "priority": "high"|"medium"|"low"}
        """
        score = l5_scores.get("overall", 5)
        verdict = l5_scores.get("verdict", "pass")
        dimensions = l5_scores.get("dimensions", {})
        evolution_signals = l5_scores.get("evolution_signals", {})

        has_gap = score < 7 or verdict in ("retry", "reject")
        if not has_gap:
            return {"has_gap": False, "gap_summary": f"目标已达成 (score={score}/10)",
                    "next_actions": [], "priority": "low"}

        actions = []

        # Gap 1: Schema 缺失
        missing_fields = l5_scores.get("missing_fields", [])
        if missing_fields:
            card_agent = agent_card.get("name", "通用Agent") if agent_card else "通用Agent"
            actions.append({
                "action": "补全输出字段", "agent": card_agent,
                "description": f"输出缺少必需字段: {', '.join(missing_fields)}",
                "priority": "high", "intent": "complete_output"
            })

        # Gap 2: 低分维度定向改进
        dim_agents = self._get_dim_agents(agent_card)
        for dim, val in sorted(dimensions.items(), key=lambda x: x[1]):
            if val < 7:
                agent = dim_agents.get(dim, agent_card.get("name", "审计官") if agent_card else "审计官")
                actions.append({
                    "action": f"提升{dim}维度", "agent": agent,
                    "description": f"当前{dim}评分 {val}/10，需提升至 ≥7",
                    "priority": "high" if val < 5 else "medium", "intent": "improve_dimension"
                })

        # Gap 3: Agent 不匹配
        if evolution_signals.get("agent_mismatch"):
            alt = evolution_signals.get("suggested_alternative_agent")
            if alt:
                actions.append({
                    "action": "更换执行Agent", "agent": alt,
                    "description": f"当前Agent可能不匹配，建议更换为 {alt}",
                    "priority": "high", "intent": "reassign_agent"
                })

        # Gap 4: 知识缺口
        if evolution_signals.get("knowledge_gap"):
            queries = evolution_signals.get("suggested_search_queries", [])
            actions.append({
                "action": "补充领域知识", "agent": "_tool_", "tool": "web_search",
                "description": f"搜索相关知识: {'; '.join(queries[:2])}",
                "priority": "medium", "intent": "research"
            })

        # Gap 5: Prompt 优化
        if evolution_signals.get("prompt_needs_optimization"):
            actions.append({
                "action": "优化Agent提示词",
                "agent": agent_card.get("name", "通用Agent") if agent_card else "通用Agent",
                "description": "输出格式/结构存在问题，建议优化 agent prompt 中的 output 格式说明",
                "priority": "medium", "intent": "optimize_prompt"
            })

        return {
            "has_gap": True,
            "gap_summary": f"Score={score}/10, {len(actions[:3])} 个改进项",
            "next_actions": actions[:3],
            "priority": "high" if score < 5 else "medium",
            "dimensions": dimensions,
            "evolution_signals": evolution_signals,
        }

    def to_subtasks(self, task_id: str, gap: dict, payload: dict, round_num: int) -> list:
        """将差距分析结果转换为可执行的子任务"""
        actions = gap.get("next_actions", [])
        if not actions:
            return []
        subtasks = []
        for i, act in enumerate(actions):
            sid = f"s{round_num}_{i+1}"
            subtasks.append({
                "id": sid, "action": act.get("action", "")[:60],
                "agent": act.get("agent", "审计官"), "depends": [],
                "prompt": act.get("description", "")[:500],
                "tool": act.get("tool", ""),
                "priority": act.get("priority", "medium").upper(),
            })
        return subtasks

    def _get_dim_agents(self, agent_card: dict) -> Dict[str, str]:
        """从能力卡片推导谁最适合改进某个维度"""
        if not agent_card:
            return self._default_dim_agents()
        custom = agent_card.get("improvement_agents", {})
        return custom if custom else self._default_dim_agents()

    def _default_dim_agents(self) -> Dict[str, str]:
        """默认维度→Agent 映射"""
        return {
            "accuracy": "审计官", "completeness": "LM内容工程师",
            "professionalism": "品牌策略师", "actionability": "售前经理",
            "consistency": "翻译官", "code_quality": "前端工程师",
            "design": "UI/UX设计师", "schema_completeness": "审计官",
            "quality": "审计官", "structure": "LM内容工程师",
        }
```

---

## 五、实现: ExperienceFlywheel

**新建文件**: `modules/layer5/experience_flywheel.py`

```python
"""
ExperienceFlywheel — 经验飞轮
================================
闭合"存经验 → 用经验 → 改善模板"的完整数据飞轮。

三阶段:
  1. 高分 (≥8): 向量化索引 + 模板回写提升基线
  2. 中分 (5-7): 创建 A/B 变体
  3. 低分 (<5): 记录失败模式
"""
import json
import time
from typing import Dict, List, Set


class ExperienceFlywheel:
    """经验飞轮"""

    def __init__(self, redis_client, vector_store):
        self.redis = redis_client
        self.vs = vector_store

    def save_experience(self, task_id: str, task_description: str,
                        subtasks: List[dict], final_score: float,
                        l5_signals: dict, agents_used: Set[str],
                        intent: str = "general"):
        """任务完成后保存经验"""
        # 1. 向量化索引
        experience_text = self._format_experience(task_id, task_description, subtasks, final_score, agents_used)
        self.vs.add(f"exp:{intent}:{task_id}", experience_text, {
            "type": "experience", "intent": intent, "score": final_score,
            "agents": list(agents_used), "subtask_count": len(subtasks),
            "timestamp": time.time(), "success": final_score >= 7,
        })

        # 2. Redis List 快速兜底
        for agent in agents_used:
            if agent.startswith("_"):
                continue
            exp = {"task_id": task_id, "agent": agent, "intent": intent,
                   "score": final_score, "subtask_count": len(subtasks),
                   "success": final_score >= 7, "agents_involved": list(agents_used),
                   "subtask_actions": [s.get("action", "")[:60] for s in subtasks[:5]],
                   "ts": time.time()}
            key = f"exp:{intent}:{agent}"
            self.redis.client.lpush(key, json.dumps(exp, ensure_ascii=False))
            self.redis.client.ltrim(key, 0, 49)

        # 3. Agent 信用分
        for agent in agents_used:
            self._update_agent_credit(agent, final_score)

        # 4. 按分数执行后续动作
        if final_score >= 8.0:
            self._on_high_score(task_id, agents_used, l5_signals)
        elif final_score >= 5.0:
            self._on_medium_score(task_id, agents_used, l5_signals)
        else:
            self._on_low_score(task_id, agents_used, l5_signals, subtasks)

    def retrieve_experiences(self, task_description: str, intent: str = "general",
                             top_k: int = 5) -> List[dict]:
        """为新任务检索相关经验（语义搜索）"""
        results = []
        try:
            semantic = self.vs.search(f"task:{intent}:{task_description[:300]}", top_k=top_k)
            for r in semantic:
                meta = r.get("meta", r.get("metadata", {}))
                if meta.get("type") == "experience" and meta.get("score", 0) >= 7:
                    results.append({"source": "chroma", "score": meta.get("score", 0),
                                    "agents": meta.get("agents", []),
                                    "text": r.get("text", "")[:500]})
        except Exception:
            pass
        # Redis 兜底
        if len(results) < 3:
            try:
                for key in [f"exp:{intent}:all"]:
                    for item in self.redis.client.lrange(key, 0, 4):
                        try:
                            exp = json.loads(item)
                            if exp.get("score", 0) >= 7:
                                results.append({"source": "redis", "score": exp.get("score", 0),
                                                "agents": exp.get("agents_involved", []),
                                                "actions": exp.get("subtask_actions", [])})
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass
        seen = set()
        unique = [r for r in results if not (r.get("task_id", id(r)) in seen or seen.add(r.get("task_id", id(r))))]
        return unique[:top_k]

    def _on_high_score(self, task_id: str, agents_used: Set[str], l5_signals: dict):
        """高分 → 模板回写"""
        for agent_name in agents_used:
            self._promote_template(agent_name, task_id, l5_signals)

    def _on_medium_score(self, task_id: str, agents_used: Set[str], l5_signals: dict):
        """中分 → 创建 A/B 变体"""
        if l5_signals.get("prompt_needs_optimization"):
            for agent_name in agents_used:
                self._create_ab_variant(agent_name, task_id)

    def _on_low_score(self, task_id: str, agents_used: Set[str],
                      l5_signals: dict, subtasks: List[dict]):
        """低分 → 记录失败模式"""
        pattern = {"task_id": task_id, "score": l5_signals.get("overall", 0),
                   "issues": l5_signals.get("key_issues", []),
                   "agents": list(agents_used),
                   "subtask_actions": [s.get("action", "")[:80] for s in subtasks[:5]]}
        try:
            self.redis.client.lpush("yaxiio:failure_patterns", json.dumps(pattern, ensure_ascii=False))
            self.redis.client.ltrim("yaxiio:failure_patterns", 0, 99)
        except Exception:
            pass

    def _promote_template(self, agent_name: str, task_id: str, l5_signals: dict):
        """高分模板提升"""
        card_key = f"agent:card:{agent_name}"
        try:
            card_raw = self.redis.get(card_key)
            if not card_raw:
                return
            card = json.loads(card_raw)
            version = card.get("_template_version", 1) + 1
            card["_template_version"] = version
            card["_last_promoted_score"] = l5_signals.get("overall", 0)
            card["_last_promoted_task"] = task_id
            card["_promoted_at"] = time.time()
            self.redis.set(card_key, json.dumps(card, ensure_ascii=False))
            print(f"[Flywheel] 📈 {agent_name} 模板提升至 v{version}", flush=True)
        except Exception as e:
            print(f"[Flywheel] 模板提升失败: {e}", flush=True)

    def _create_ab_variant(self, agent_name: str, task_id: str):
        """为中分创建 A/B 测试变体"""
        try:
            from modules.layer5.ab_tester import ABTester
            tester = ABTester()
            card_key = f"agent:card:{agent_name}"
            card_raw = self.redis.get(card_key)
            if not card_raw:
                return
            card = json.loads(card_raw)
            variant_a = card.get("system_prompt", "")
            variant_b = variant_a + ("\n\n输出要求:\n1. 以 JSON 格式返回结果\n"
                                     "2. 包含 reasoning 字段说明推理过程\n"
                                     "3. 包含 confidence 字段说明置信度")
            test_name = f"prompt:{agent_name}:{task_id[:12]}"
            tester.start(test_name, {"prompt": variant_a}, {"prompt": variant_b}, traffic_split=0.3)
            print(f"[Flywheel] 🧪 创建 A/B 测试: {test_name}", flush=True)
        except Exception:
            pass

    def _update_agent_credit(self, agent_name: str, score: float):
        """EMA 更新 Agent 信用分"""
        try:
            credit_key = f"agent:credit:{agent_name}"
            current = float(self.redis.get(credit_key) or "7.0")
            new_credit = current * 0.8 + score * 0.2
            self.redis.set(credit_key, str(round(new_credit, 2)))
        except Exception:
            pass

    def _format_experience(self, task_id, task_description, subtasks, score, agents):
        """格式化经验文本"""
        parts = [f"Task: {task_description[:200]}", f"Score: {score}/10",
                 f"Agents: {', '.join(agents)}", "Subtasks:"]
        for st in subtasks[:8]:
            parts.append(f"  - {st.get('action', '')[:80]} → {st.get('agent', '')}")
        return "\n".join(parts)
```

---

## 六、修改 workflow_engine._do_L5()

```python
def _do_L5(self, task_id: str, action: str, plan: dict,
           l4: dict, state: dict) -> dict:
    """L5 评分 — 统一评分总线"""
    if MCP_LAYERS_ENABLED.get("L5"):
        return {"mcp_routed": True, "layer": "L5", "phase": "not_implemented"}

    output_text = self._extract_output_text(l4)
    agent_name = self._resolve_agent_name(plan)

    task_info = {"task_id": task_id, "action": action,
                 "description": str(state.get("summary", ""))[:500], "type": action}
    result_info = {"output": output_text[:3000],
                   "subtasks": plan.get("subtasks", []) if isinstance(plan, dict) else [],
                   "status": "success" if l4.get("results") else "partial"}

    agent_card = None
    try:
        card_raw = self.commander.redis.get(f"agent:card:{agent_name}")
        if card_raw:
            agent_card = json.loads(card_raw)
    except Exception:
        pass

    subtask_count = len(plan.get("subtasks", [])) if isinstance(plan, dict) else 1
    strategy = "fast" if subtask_count <= 1 and len(output_text) < 500 else (
        "deep" if subtask_count >= 5 else "standard")

    try:
        from modules.layer5.unified_scorer import UnifiedScorer
        scorer = UnifiedScorer(redis_client=self.commander.redis)
        result = scorer.score(task=task_info, result=result_info,
                              strategy=strategy, agent_card=agent_card)
    except Exception as e:
        print(f"[WF] UnifiedScorer failed, fallback: {e}", flush=True)
        result = self._legacy_l5_fallback(task_id, action, output_text, agent_name)

    if result["overall"] < 7:
        self._try_optimize_on_low_score(task_id, action, output_text, result)

    self.score_history.append({"task_id": task_id, "score": result["overall"], "ts": time.time()})
    return result
```

---

## 七、修改 workflow_engine._cleanup_task()

```python
def _cleanup_task(self, task_id: str, subtasks: list, final_score: float):
    """任务清理 — 使用 ExperienceFlywheel 闭合经验飞轮"""
    agents_used = set(s.get("agent", "?") for s in subtasks)
    action = self._current_intent or "general"
    try:
        from modules.layer5.experience_flywheel import ExperienceFlywheel
        from modules.layer1.vector_store_chroma import ChromaVectorStore
        vs = ChromaVectorStore()
        flywheel = ExperienceFlywheel(redis_client=self.commander.redis, vector_store=vs)
        flywheel.save_experience(task_id=task_id, task_description=str(self._current_intent or ""),
                                 subtasks=subtasks, final_score=final_score,
                                 l5_signals={}, agents_used=agents_used, intent=action)
        import redis as _r
        r = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                    password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
        for agent in agents_used:
            r.delete(f"agent:{agent}:{task_id}:memory")
        print(f"[WF] {task_id} 经验飞轮闭合: {len(agents_used)} agents, score={final_score}", flush=True)
    except Exception as e:
        print(f"[WF] {task_id} 经验飞轮异常: {e}", flush=True)
        if hasattr(self, "l0") and self.l0:
            try:
                import redis as _r
                r = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                            password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
                self.l0._save_experience(task_id, subtasks, final_score, agents_used, r)
            except Exception:
                pass
```

---

## 八、修改 workflow_engine._analyze_gap()

```python
def _analyze_gap(self, task_id: str, payload: dict, results: dict, l5_scores: dict) -> dict:
    """差距分析 — 使用通用 GapAnalyzer"""
    try:
        from modules.layer5.gap_analyzer_v2 import UniversalGapAnalyzer
        analyzer = UniversalGapAnalyzer()
        agent_card = None
        try:
            primary_agent = l5_scores.get("primary_agent", "审计官")
            card_raw = self.commander.redis.get(f"agent:card:{primary_agent}")
            if card_raw:
                agent_card = json.loads(card_raw)
        except Exception:
            pass
        return analyzer.analyze(
            task={"action": payload.get("action", ""),
                  "description": str(payload.get("task", ""))[:300]},
            results=results, l5_scores=l5_scores, agent_card=agent_card)
    except Exception as e:
        print(f"[WF] UniversalGapAnalyzer failed: {e}", flush=True)
        if hasattr(self, "gap") and self.gap:
            return self.gap.analyze(task_id, payload, results, l5_scores)
        return {"has_gap": False, "next_actions": []}
```

---

## 九、`modules/layer5/__init__.py` 新增导出

```python
from modules.layer5.unified_scorer import UnifiedScorer              # 新增
from modules.layer5.gap_analyzer_v2 import UniversalGapAnalyzer      # 新增
from modules.layer5.experience_flywheel import ExperienceFlywheel    # 新增
```

---

## 十、迁移步骤

```bash
# 启用统一评分器
export YAXIIO_UNIFIED_SCORER=true
export YAXIIO_SCORING_STRATEGY=standard   # fast | standard | deep

# 启用经验飞轮
export YAXIIO_EXPERIENCE_FLYWHEEL=true

# 回滚
export YAXIIO_UNIFIED_SCORER=false    # 切回旧的 _do_L5
export YAXIIO_EXPERIENCE_FLYWHEEL=false  # 切回旧的 l0._save_experience
```

---

## 十一、预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| 评分系统数量 | 3 套（永不交汇） | 1 套（统一总线） |
| GapAnalyzer 行业硬编码 | 6 个外贸专属关键词 | 0（完全通用） |
| 经验是否被后续任务使用 | 否（只存不取） | 是（Chroma 语义检索） |
| 模板是否自动优化 | 否 | 是（高分回写） |
| A/B 测试自动化 | 手动创建 | 中分自动创建 |
| DSPy 优化 | ImportError 降级 | 真正集成（需安装 dspy） |
| 评分策略可配置 | 否 | 是（fast/standard/deep） |
