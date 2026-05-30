"""
评分维度注册表 — Commander 按任务类型动态调配
==============================================
不写死维度，由 Commander 根据 action 选择，Redis 可热更新。

用法:
  registry = ScoreDimensionRegistry(redis_client)
  dims = registry.get_dimensions("site_audit")
  score = registry.score_with(dims, output_text)
"""

import json, os

# ═══════════════════════════════════════════════
# 维度定义 (可按需扩展)
# ═══════════════════════════════════════════════

# 所有可用维度的信号探测规则
SIGNAL_RULES = {
    "accuracy": {
        "name": "准确性",
        "keywords": [],  # 通用，不需要特定关键词
        "structural_bonus": 1.0,
        "length_bonus_1500": 1.0,
        "error_honesty_bonus": 0.5,  # 诚实报告错误
    },
    "completeness": {
        "name": "完整度",
        "report_bonus": 1.0,
        "findings_bonus": 1.0,
    },
    "professionalism": {
        "name": "专业性",
        "report_bonus": 2.0,
        "structural_bonus": 1.0,
        "length_bonus_800": 1.0,
        "code_bonus": 0.5,
    },
    "actionability": {
        "name": "可执行性",
        "findings_bonus": 2.0,
        "code_bonus": 1.0,
        "keywords": ["建议", "下一步", "修正", "行动", "recommend", "action", "fix", "方案"],
        "keyword_bonus": 1.5,
    },
    "consistency": {
        "name": "一致性",
        "report_bonus": 1.5,
        "structural_and_numbers_bonus": 1.0,
        "length_bonus_1000": 0.5,
    },
    "code_quality": {
        "name": "代码质量",
        "code_bonus": 3.0,
        "structural_bonus": 1.0,
        "keywords": ["refactor", "优化", "clean", "lint", "test", "coverage"],
        "keyword_bonus": 2.0,
    },
    "fluency": {
        "name": "流畅度",
        "keywords": [],  # 翻译专用，LLM judge 评估
    },
    "terminology": {
        "name": "术语准确",
        "keywords": [],  # 翻译专用
    },
    "aesthetics": {
        "name": "美观度",
        "keywords": ["layout", "spacing", "color", "responsive"],
        "keyword_bonus": 1.0,
    },
    "usability": {
        "name": "可用性",
        "keywords": ["click", "navigate", "user", "mobile", "accessibility"],
        "keyword_bonus": 1.0,
    },
}

# ═══════════════════════════════════════════════
# 任务类型 → 评分维度映射 (可 Redis 覆盖)
# ═══════════════════════════════════════════════

DEFAULT_ACTION_DIMENSIONS = {
    "site_audit": {
        "dimensions": ["accuracy", "completeness", "professionalism", "actionability", "consistency"],
        "weights": {"accuracy": 0.25, "completeness": 0.25, "professionalism": 0.20, "actionability": 0.15, "consistency": 0.15},
    },
    "site_fix": {
        "dimensions": ["accuracy", "actionability", "code_quality", "completeness"],
        "weights": {"accuracy": 0.25, "actionability": 0.30, "code_quality": 0.25, "completeness": 0.20},
    },
    "site_evolve": {
        "dimensions": ["actionability", "code_quality", "completeness", "consistency"],
        "weights": {"actionability": 0.30, "code_quality": 0.25, "completeness": 0.25, "consistency": 0.20},
    },
    "translate": {
        "dimensions": ["accuracy", "fluency", "terminology", "consistency"],
        "weights": {"accuracy": 0.30, "fluency": 0.30, "terminology": 0.25, "consistency": 0.15},
    },
    "design": {
        "dimensions": ["aesthetics", "usability", "professionalism", "completeness"],
        "weights": {"aesthetics": 0.30, "usability": 0.30, "professionalism": 0.20, "completeness": 0.20},
    },
    # 通用兜底
    "default": {
        "dimensions": ["accuracy", "completeness", "professionalism", "actionability", "consistency"],
        "weights": {"accuracy": 0.25, "completeness": 0.25, "professionalism": 0.20, "actionability": 0.15, "consistency": 0.15},
    },
}


class ScoreDimensionRegistry:
    """评分维度注册表 — Commander 的动态评分配置"""

    REDIS_KEY = "yaxiio:score:dimensions"

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._cache = {}

    def get_dimensions(self, action: str, payload: dict = None) -> dict:
        """
        获取评分维度配置。优先级:
          1. payload._score_dimensions 显式指定
          2. Redis 热配置
          3. DEFAULT_ACTION_DIMENSIONS 按 action 匹配
          4. default 兜底
        """
        # 1. 显式指定
        if payload:
            explicit = payload.get("_score_dimensions")
            if explicit and isinstance(explicit, dict):
                return explicit

        # 2. Redis 热配置
        try:
            if self.redis:
                raw = self.redis.get(f"{self.REDIS_KEY}:{action}")
                if raw:
                    return json.loads(raw)
        except Exception:
            pass

        # 3. action 匹配
        action_lower = str(action).lower()
        for prefix, config in DEFAULT_ACTION_DIMENSIONS.items():
            if action_lower == prefix or action_lower.startswith(prefix):
                return dict(config)

        # 4. 兜底
        return dict(DEFAULT_ACTION_DIMENSIONS["default"])

    def set_dimensions(self, action: str, config: dict):
        """热更新某个 action 的评分维度"""
        if self.redis:
            self.redis.setex(
                f"{self.REDIS_KEY}:{action}",
                86400 * 7,
                json.dumps(config, ensure_ascii=False)
            )

    def score_output(self, output_text: str, dimensions: list, weights: dict) -> dict:
        """
        对输出文本按指定维度打分 (纯规则，无 LLM)

        Returns: {"overall": 7.5, "dimensions": {...}, "signals": {...}}
        """
        output_len = len(output_text)

        # 通用信号
        has_code = "```" in output_text or "`" in output_text
        has_struct = any(m in output_text for m in ("##", "###", "**", "| ", "1.", "2.", "-"))
        has_findings = any(kw in output_text.lower() for kw in
                          ("发现", "问题", "建议", "结论", "诊断", "分析",
                           "finding", "issue", "recommend", "diagnos", "analys"))
        has_report = output_text.startswith("##") or output_text.startswith("# ")
        has_numbers = any(c.isdigit() for c in output_text[:200])
        is_long = output_len > 800

        dim_scores = {}
        signals = {
            "code": has_code, "struct": has_struct, "findings": has_findings,
            "report": has_report, "numbers": has_numbers, "length": output_len,
        }

        for dim_name in dimensions:
            rules = SIGNAL_RULES.get(dim_name, {})
            score = 5.0  # 基础分

            # 信号加分
            if has_code:
                score += rules.get("code_bonus", 0)
            if has_struct:
                score += rules.get("structural_bonus", 0)
            if has_findings:
                score += rules.get("findings_bonus", 0)
            if has_report:
                score += rules.get("report_bonus", 0)
            if has_numbers and rules.get("structural_and_numbers_bonus"):
                score += rules["structural_and_numbers_bonus"]

            # 长度加分
            if output_len > 1500:
                score += rules.get("length_bonus_1500", 0)
            elif output_len > 1000:
                score += rules.get("length_bonus_1000", 0)
            elif output_len > 800:
                score += rules.get("length_bonus_800", 0)

            # 关键词加分
            for kw in rules.get("keywords", []):
                if kw in output_text.lower():
                    score += rules.get("keyword_bonus", 0)
                    break

            # 诚实报告错误也是准确
            if dim_name == "accuracy" and ("错误" in output_text or "error" in output_text.lower()):
                score += rules.get("error_honesty_bonus", 0)

            dim_scores[dim_name] = round(min(10, score), 1)

        # 加权总分
        total_weight = sum(weights.get(d, 0.2) for d in dimensions) or 1.0
        overall = sum(dim_scores[d] * weights.get(d, 0.2) for d in dimensions) / total_weight
        overall = round(overall, 1)

        return {
            "overall": overall,
            "method": "commander_rule",
            "dimensions": dim_scores,
            "weights_used": {d: weights.get(d, 0.2) for d in dimensions},
            "signals": signals,
        }
