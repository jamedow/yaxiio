"""
IntelligentModelRouter — 智能模型路由器
==========================================
多目标优化选择模型: 成本 × 延迟 × 能力 × 可用性。
替代 ModelRouter 的 3 条规则 9 个中文关键词。

支持 DeepSeek / OpenAI / Claude 自动切换。
失败自动 fallback + 冷却期。
"""
import os
import time
from typing import Dict, List, Optional


class IntelligentModelRouter:
    """多 Provider 智能模型路由"""

    MODEL_CAPABILITIES = {
        "deepseek-chat": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "max_tokens": 8192,
            "supports_thinking": True,
            "cost_per_1k_input": 0.14,
            "cost_per_1k_output": 0.28,
            "avg_latency_ms": 800,
            "strengths": ["reasoning", "code", "multilingual", "long_context"],
            "recommended_for": ["analyze", "decompose", "audit", "review"],
            "priority": 1,
        },
        "deepseek-v4-flash": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "max_tokens": 4096,
            "supports_thinking": False,
            "cost_per_1k_input": 0.07,
            "cost_per_1k_output": 0.14,
            "avg_latency_ms": 300,
            "strengths": ["translation", "classification", "simple_tasks"],
            "recommended_for": ["translate", "classify", "check", "query"],
            "priority": 1,
        },
    }

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._failure_counts: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}
        self._cooldown_seconds = 60

        # JIT-style performance tracking: task_type -> {model: {success, total, avg_score}}
        self._perf = {}
        self._hotspot_threshold = 10

    def select(self, task: dict, constraints: dict = None) -> dict:
        """
        多目标优化选择模型

        Args:
            task: {"action", "description", "estimated_tokens"}
            constraints: {"max_cost", "max_latency_ms", "prefer"}

        Returns:
            {"model", "provider", "base_url", "api_key", "thinking",
             "score", "fallback_model", "estimated_cost_usd", "selection_reason"}
        """
        constraints = constraints or {}
        required = self._estimate_requirements(task)
        candidates = self._filter_candidates(required, constraints)

        if not candidates:
            return self._emergency_fallback()

        scored = self._score_candidates(candidates, required, constraints)
        scored.sort(key=lambda x: -x["score"])

        best = scored[0]
        fallback = scored[1] if len(scored) > 1 else None

        return {
            "model": best["model"],
            "provider": best["provider"],
            "base_url": best["base_url"],
            "api_key": best["api_key"],
            "thinking": self._determine_thinking(best, required),
            "score": round(best["score"], 1),
            "fallback_model": fallback["model"] if fallback else None,
            "estimated_cost_usd": round(
                best["cost_per_1k_output"]
                * required.get("output_tokens", 500)
                / 1000,
                4,
            ),
            "selection_reason": self._explain_selection(best, required),
        }

    def fallback(self, failed_model: str) -> Optional[dict]:
        """当前模型失败后自动切换"""
        self._record_failure(failed_model)
        for name, caps in sorted(
            self.MODEL_CAPABILITIES.items(), key=lambda x: x[1]["priority"]
        ):
            if name != failed_model and self._is_available(name):
                return {
                    "model": name,
                    "provider": caps["provider"],
                    "base_url": caps["base_url"],
                    "api_key": os.environ.get(caps["api_key_env"], ""),
                    "thinking": "off",
                    "fallback_reason": f"{failed_model} failed, switched to {name}",
                }
        return None

    def record_success(self, model_name: str):
        """Record model success (reset failure count)"""
        caps = self.MODEL_CAPABILITIES.get(model_name, {})
        provider = caps.get("provider", model_name)
        self._failure_counts[provider] = 0
        if provider in self._last_failure_time:
            del self._last_failure_time[provider]

    def record_performance(self, task_type: str, model: str, score: float):
        """JIT-style: record model performance for adaptive routing"""
        if task_type not in self._perf:
            self._perf[task_type] = {}
        if model not in self._perf[task_type]:
            self._perf[task_type][model] = {"success": 0, "total": 0, "avg_score": 0.0}
        p = self._perf[task_type][model]
        p["total"] += 1
        if score >= 6.0:
            p["success"] += 1
        p["avg_score"] = round((p["avg_score"] * (p["total"] - 1) + score) / p["total"], 2)

    def suggest_upgrade(self, task_type: str, current_model: str):
        """JIT hotspot: suggest model upgrade/downgrade based on history"""
        if task_type not in self._perf:
            return None
        stats = self._perf[task_type].get(current_model, {})
        total = stats.get("total", 0)
        if total < self._hotspot_threshold:
            return None
        success_rate = stats.get("success", 0) / max(total, 1)
        tier_order = ["deepseek-flash", "deepseek-chat", "deepseek-max"]
        if current_model in tier_order:
            idx = tier_order.index(current_model)
            if success_rate < 0.7 and idx + 1 < len(tier_order):
                return {"action": "upgrade", "from": current_model,
                        "to": tier_order[idx + 1],
                        "reason": "success_rate={:.0%} < 70%".format(success_rate)}
            elif success_rate > 0.9 and total > 20 and idx > 0:
                return {"action": "downgrade", "from": current_model,
                        "to": tier_order[idx - 1],
                        "reason": "success_rate={:.0%} > 90%, safe downgrade".format(success_rate)}
        return None

    def get_best_model(self, task_type: str):
        """Return historically best model for this task type"""
        if task_type not in self._perf:
            return None
        best, best_score = None, 0
        for model, stats in self._perf[task_type].items():
            if stats["total"] >= 3 and stats["avg_score"] > best_score:
                best_score = stats["avg_score"]
                best = model
        return best


    def update_from_redis(self):
        """从 Redis 动态更新模型配置"""
        if not self.redis:
            return
        try:
            raw = self.redis.get("yaxiio:config:model_capabilities")
            if raw:
                updated = json.loads(raw)
                self.MODEL_CAPABILITIES.update(updated)
        except Exception:
            pass

    # ═══════════════════════════════════════════════
    # 内部
    # ═══════════════════════════════════════════════

    def _estimate_requirements(self, task: dict) -> dict:
        """从任务描述估算所需模型能力"""
        desc = str(task.get("description", "")) + " " + str(task.get("action", ""))
        desc_lower = desc.lower()

        req = {"strengths": [], "estimated_tokens": 2000, "task_type": "general"}

        if len(desc) > 500:
            req["estimated_tokens"] = 6000
        elif len(desc) > 200:
            req["estimated_tokens"] = 4000

        analyze_kw = ["analyze", "decompose", "audit", "review", "分析", "拆解", "审计"]
        if any(kw in desc_lower for kw in analyze_kw):
            req["strengths"].append("reasoning")
            req["task_type"] = "analyze"
            req["estimated_tokens"] = max(req["estimated_tokens"], 6000)

        code_kw = ["code", "build", "deploy", "fix", "代码", "修复"]
        if any(kw in desc_lower for kw in code_kw):
            req["strengths"].append("code")

        translate_kw = ["translate", "翻译", "multilingual", "多语言"]
        if any(kw in desc_lower for kw in translate_kw):
            req["strengths"].append("multilingual")
            req["task_type"] = "translate"

        generate_kw = ["generate", "create", "生成", "创建"]
        if any(kw in desc_lower for kw in generate_kw):
            req["strengths"].append("creative")
            req["task_type"] = "generate"

        if any('\u4e00' <= c <= '\u9fff' for c in desc):
            req["strengths"].append("multilingual")

        return req

    def _filter_candidates(self, required: dict, constraints: dict) -> List[tuple]:
        """过滤可用且满足硬约束的模型"""
        candidates = []
        for model_name, caps in self.MODEL_CAPABILITIES.items():
            if not self._is_available(model_name):
                continue
            if constraints.get("max_cost"):
                if caps["cost_per_1k_output"] > constraints["max_cost"]:
                    continue
            if constraints.get("max_latency_ms"):
                if caps["avg_latency_ms"] > constraints["max_latency_ms"]:
                    continue
            if required.get("estimated_tokens", 0) > caps["max_tokens"]:
                continue
            candidates.append((model_name, caps))
        return candidates

    def _score_candidates(self, candidates: List[tuple], required: dict,
                          constraints: dict) -> List[dict]:
        """多目标评分"""
        scored = []
        for model_name, caps in candidates:
            score = 0.0

            # 能力匹配度 (40%)
            if required.get("strengths"):
                match = sum(1 for s in required["strengths"] if s in caps["strengths"])
                score += (match / len(required["strengths"])) * 4

            # 推荐匹配度 (20%)
            if required.get("task_type", "") in caps.get("recommended_for", []):
                score += 2

            # 成本分 (15%)
            cost = caps["cost_per_1k_input"] + caps["cost_per_1k_output"]
            score += min((1.0 / (cost + 0.01)) * 1.5, 2.0)

            # 延迟分 (15%)
            score += min((500 / (caps["avg_latency_ms"] + 100)) * 1.5, 2.0)

            # 偏好 (10%)
            prefer = constraints.get("prefer", "")
            if prefer and prefer in caps["strengths"]:
                score += 1.5

            # 优先级惩罚
            score -= (caps["priority"] - 1) * 0.5

            scored.append({
                "model": model_name,
                "provider": caps["provider"],
                "base_url": caps["base_url"],
                "api_key": os.environ.get(caps["api_key_env"], ""),
                "cost_per_1k_output": caps["cost_per_1k_output"],
                "score": round(score, 1),
                "caps": caps,
            })
        return scored

    def _is_available(self, model_name: str) -> bool:
        """检查模型是否可用（API Key + 冷却期）"""
        caps = self.MODEL_CAPABILITIES.get(model_name)
        if not caps:
            return False
        api_key = os.environ.get(caps["api_key_env"], "")
        if not api_key:
            return False
        provider = caps["provider"]
        failures = self._failure_counts.get(provider, 0)
        if failures >= 3:
            last_fail = self._last_failure_time.get(provider, 0)
            if time.time() - last_fail < self._cooldown_seconds:
                return False
        return True

    def _determine_thinking(self, best: dict, required: dict) -> str:
        """决定是否启用 thinking 模式"""
        caps = best.get("caps", {})
        if not caps.get("supports_thinking", False):
            return "off"
        task_type = required.get("task_type", "general")
        if task_type in ("analyze", "audit"):
            return "high"
        if task_type in ("translate",):
            return "off"
        return "medium"

    def _explain_selection(self, best: dict, required: dict) -> str:
        """生成选择解释"""
        caps = best.get("caps", {})
        reasons = []
        matched = [
            s for s in required.get("strengths", []) if s in caps.get("strengths", [])
        ]
        if matched:
            reasons.append(f"能力匹配: {', '.join(matched)}")
        if caps.get("cost_per_1k_output", 0) < 0.5:
            reasons.append("低成本")
        if caps.get("avg_latency_ms", 1000) < 500:
            reasons.append("低延迟")
        return "; ".join(reasons) if reasons else "综合最优"

    def _emergency_fallback(self) -> dict:
        """全部模型不可用时的紧急兜底"""
        for name, caps in sorted(
            self.MODEL_CAPABILITIES.items(), key=lambda x: x[1]["priority"]
        ):
            api_key = os.environ.get(caps["api_key_env"], "")
            if api_key:
                return {
                    "model": name,
                    "provider": caps["provider"],
                    "base_url": caps["base_url"],
                    "api_key": api_key,
                    "thinking": "off",
                    "score": 0,
                    "fallback_model": None,
                    "estimated_cost_usd": caps["cost_per_1k_output"] * 0.5,
                    "selection_reason": "EMERGENCY: all models unavailable",
                }
        return {
            "model": "deepseek-chat",
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "thinking": "off",
            "score": 0,
            "fallback_model": None,
            "estimated_cost_usd": 0,
            "selection_reason": "CRITICAL: no API key found",
        }

    def _record_failure(self, model_name: str):
        """记录模型失败"""
        caps = self.MODEL_CAPABILITIES.get(model_name, {})
        provider = caps.get("provider", model_name)
        self._failure_counts[provider] = self._failure_counts.get(provider, 0) + 1
        self._last_failure_time[provider] = time.time()

    def status(self) -> dict:
        """返回所有模型的状态"""
        result = {}
        for name, caps in self.MODEL_CAPABILITIES.items():
            provider = caps["provider"]
            result[name] = {
                "provider": provider,
                "available": self._is_available(name),
                "failures": self._failure_counts.get(provider, 0),
                "in_cooldown": (
                    self._failure_counts.get(provider, 0) >= 3
                    and time.time() - self._last_failure_time.get(provider, 0)
                    < self._cooldown_seconds
                ),
            }
        return result


# Import json for update_from_redis
import json
