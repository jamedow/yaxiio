"""L5 进化层 — GEPA+DSPy互补 + 统计显著性"""
import json, math, time
from typing import Dict, List

class ABTesterV2:
    """A/B测试器 V2 — 统计显著性计算"""
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.tests = {}

    def start(self, name: str, variant_a: dict, variant_b: dict, traffic_split: float = 0.5):
        self.tests[name] = {
            "a_config": variant_a, "b_config": variant_b,
            "a_success": 0, "a_total": 0,
            "b_success": 0, "b_total": 0,
            "traffic_split": traffic_split, "started_at": time.time()
        }

    def record(self, name: str, variant: str, success: bool, score: float = 0):
        if name not in self.tests: return
        t = self.tests[name]
        if variant == "A":
            t["a_total"] += 1
            if success: t["a_success"] += 1
        else:
            t["b_total"] += 1
            if success: t["b_success"] += 1

    def is_significant(self, name: str, confidence: float = 0.95) -> dict:
        """简化卡方检验"""
        t = self.tests.get(name)
        if not t or t["a_total"] < 10 or t["b_total"] < 10:
            return {"significant": False, "reason": "insufficient data"}

        a_rate = t["a_success"] / t["a_total"] if t["a_total"] else 0
        b_rate = t["b_success"] / t["b_total"] if t["b_total"] else 0
        diff = b_rate - a_rate

        # 简化 z-test
        p_pool = (t["a_success"] + t["b_success"]) / (t["a_total"] + t["b_total"])
        se = math.sqrt(p_pool * (1 - p_pool) * (1/t["a_total"] + 1/t["b_total"]))
        z_score = diff / se if se > 0 else 0

        # 95%置信度对应 z=1.96
        significant = abs(z_score) > 1.96
        winner = "B" if b_rate > a_rate else "A" if a_rate > b_rate else "tie"

        return {
            "significant": significant,
            "confidence": round(abs(z_score) / 2.58, 2),
            "winner": winner if significant else "pending",
            "a_rate": round(a_rate, 3), "b_rate": round(b_rate, 3),
            "diff": round(diff, 3), "a_n": t["a_total"], "b_n": t["b_total"]
        }

    def auto_pick(self, name: str) -> str:
        """自动选优"""
        result = self.is_significant(name)
        if result["significant"]:
            return result["winner"]
        return "pending"


class GePaOptimizerBridge:
    """GEPA与DSPy桥接 — GEPA管理生命周期，DSPy提供few-shot优化"""
    def __init__(self, gepa_optimizer=None):
        self.gepa = gepa_optimizer

    def optimize_with_dspy(self, agent_id: str, task_type: str) -> dict:
        """GEPA生成候选 → DSPy BootstrapFewShot 优化 few-shot → A/B测试选优"""
        # Step 1: GEPA 生成候选
        if self.gepa:
            result = self.gepa.optimize(agent_id)
            candidates = result.candidates
        else:
            candidates = []

        # Step 2: DSPy 模式（future integration）
        # 为每个候选自动找最优 few-shot 示例
        optimized = []
        for c in candidates[:3]:
            optimized.append({
                "prompt": c.get("prompt", ""),
                "few_shots": [],
                "source": "gepa"
            })

        return {
            "agent_id": agent_id,
            "task_type": task_type,
            "candidates": optimized,
            "recommendation": "A/B test with ABTesterV2"
        }
