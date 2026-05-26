import os, time
from typing import Optional

PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "api_key_env": "DEEPSEEK_API_KEY",
        "priority": 1,
        "cost_per_1k_input": 0.14,
        "cost_per_1k_output": 0.28,
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "api_key_env": "OPENAI_API_KEY",
        "priority": 2,
        "cost_per_1k_input": 2.50,
        "cost_per_1k_output": 10.00,
    },
    "claude": {
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-4-20250514"],
        "api_key_env": "ANTHROPIC_API_KEY",
        "priority": 2,
        "cost_per_1k_input": 3.00,
        "cost_per_1k_output": 15.00,
    }
}


class MultiProviderRouter:
    """多Provider路由：按优先级+成本+可用性自动选择"""
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._status = {}  # provider → {available, failures, last_check}

    def select(self, task: dict = None, prefer: str = None) -> dict:
        """选择最佳Provider和模型"""
        # 优先级: 指定 > priority排序 > 可用性过滤
        providers = sorted(PROVIDERS.items(), key=lambda x: x[1]["priority"])

        if prefer and prefer in PROVIDERS:
            return self._build_config(prefer, PROVIDERS[prefer]["models"][0])

        for name, cfg in providers:
            if self._is_available(name):
                return self._build_config(name, cfg["models"][0])

        # 全部不可用，返回默认
        return self._build_config("deepseek", "deepseek-v4-flash")

    def fallback(self, failed_provider: str) -> Optional[dict]:
        """当前Provider失败后切换"""
        self._record_failure(failed_provider)
        providers = sorted(PROVIDERS.items(), key=lambda x: x[1]["priority"])
        for name, cfg in providers:
            if name != failed_provider and self._is_available(name):
                return self._build_config(name, cfg["models"][0])
        return None

    def _build_config(self, provider: str, model: str) -> dict:
        cfg = PROVIDERS[provider]
        return {
            "provider": provider, "model": model,
            "base_url": cfg["base_url"],
            "api_key": os.environ.get(cfg["api_key_env"], ""),
            "cost": {"input": cfg["cost_per_1k_input"], "output": cfg["cost_per_1k_output"]}
        }

    def _is_available(self, provider: str) -> bool:
        if provider not in PROVIDERS: return False
        key = os.environ.get(PROVIDERS[provider]["api_key_env"], "")
        if not key: return False
        status = self._status.get(provider, {})
        failures = status.get("failures", 0)
        if failures >= 3:
            last_check = status.get("last_check", 0)
            if time.time() - last_check < 60:  # 冷却1分钟
                return False
        return True

    def _record_failure(self, provider: str):
        s = self._status.get(provider, {"failures": 0, "last_check": 0})
        s["failures"] = s.get("failures", 0) + 1
        s["last_check"] = time.time()
        self._status[provider] = s

    def _record_success(self, provider: str):
        self._status[provider] = {"failures": 0, "last_check": time.time()}

    def status(self) -> dict:
        return {p: {"available": self._is_available(p), **self._status.get(p, {})} for p in PROVIDERS}


"""条件分支 — 任务支持 if/else 流程"""
