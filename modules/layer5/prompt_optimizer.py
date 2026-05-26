import json
import redis
from typing import List, Dict

try:
    from modules.shared.config import REDIS_CONFIG, REDIS_FAILURE_KEY
except ImportError:
    REDIS_CONFIG = {"host": "localhost", "port": 6379, "db": 0}
    REDIS_FAILURE_KEY = "prompt_failures"

class PromptOptimizer:
    def __init__(self):
        try:
            self.redis = redis.Redis(**REDIS_CONFIG)
        except Exception:
            self.redis = None
        self.history_key = REDIS_FAILURE_KEY

    def optimize(self, failures: list) -> dict:
        if not failures:
            return {"action": "skip", "count": 0}
        history = self._load_history()
        reason = self._analyze(failures)
        original = failures[0].get("prompt", "")
        variants = self._generate_variants(original, reason)
        best = self._ab_select(variants, history)
        return {
            "action": "generate_variants",
            "count": len(variants),
            "optimized_prompt": best,
            "variants": variants,
        }

    def record(self, prompt: str, success: bool):
        if self.redis:
            try:
                self.redis.lpush(self.history_key, json.dumps({"prompt": prompt, "success": success}))
                self.redis.ltrim(self.history_key, 0, 99)
            except Exception:
                pass

    def _load_history(self) -> List[Dict]:
        if self.redis is None:
            return []
        try:
            items = self.redis.lrange(self.history_key, 0, 100)
            return [json.loads(it) for it in items]
        except Exception:
            return []

    def _analyze(self, failures: List[Dict]) -> str:
        reasons = []
        patterns = {
            "format_error": ["json", "parse", "format"],
            "empty_output": ["empty", "void", "none"],
            "hallucination": ["hallucin", "factual", "verify"],
            "length_issue": ["length", "truncat", "too long", "token"],
        }
        for f in failures:
            err = f.get("error", "").lower()
            for reason, keywords in patterns.items():
                if any(k in err for k in keywords):
                    reasons.append(reason)
                    break
            else:
                reasons.append("other")
        return max(set(reasons), key=reasons.count) if reasons else "other"

    def _generate_variants(self, base: str, reason: str) -> List[str]:
        prompt = base or "You are a helpful assistant."
        if reason == "format_error":
            return [
                prompt + "\n\nOutput must be strictly valid JSON.",
                prompt + "\nRespond with a JSON object containing the requested data.",
                prompt + "\nBefore finalizing, validate your JSON output.",
            ]
        elif reason == "empty_output":
            return [
                prompt + "\nEnsure you provide a non-empty, complete response.",
                "Think step by step and then answer: " + prompt,
                prompt + "\nEven if uncertain, give your best reasoned answer.",
            ]
        elif reason == "hallucination":
            return [
                prompt + "\nOnly use information from the provided context. If unknown, say so.",
                "Based strictly on the documents: " + prompt,
                prompt + "\nCite sources for all factual claims.",
            ]
        elif reason == "length_issue":
            return [
                prompt + "\nKeep your response concise, under 200 words.",
                "Be brief and direct: " + prompt,
                prompt + "\nSummarize in a single paragraph.",
            ]
        else:
            return [
                prompt + "\nLet's think step by step.",
                "You are an expert. " + prompt,
                prompt + "\nProvide a detailed, high-quality answer.",
            ]

    def _ab_select(self, variants: List[str], history: List[Dict]) -> str:
        if not variants:
            return ""
        if not history:
            return variants[0]
        scores = [0.0] * len(variants)
        for h in history:
            h_prompt = h.get("prompt", "")
            h_success = h.get("success", False)
            h_words = set(h_prompt.lower().split())
            for i, v in enumerate(variants):
                v_words = set(v.lower().split())
                common = len(v_words & h_words)
                sim = common / len(v_words) if v_words else 0
                scores[i] += sim * (1.0 if h_success else -0.5)
        best_idx = scores.index(max(scores)) if scores else 0
        return variants[best_idx]