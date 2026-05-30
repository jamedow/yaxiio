#!/usr/bin/env python3
"""Add adaptive routing to model_router_v2"""
path = '/opt/yaxiio/modules/layer2/model_router_v2.py'
with open(path) as f: c = f.read()

# 1. Add performance tracking fields
old_init = 'self._cooldown_seconds = 60'
new_init = '''self._cooldown_seconds = 60

        # JIT-style performance tracking: task_type -> {model: {success, total, avg_score}}
        self._perf = {}
        self._hotspot_threshold = 10'''

c = c.replace(old_init, new_init)

# 2. Add record_performance, suggest_upgrade, get_best_model
old_rec = '    def record_success(self, model_name: str):'
idx = c.find(old_rec)
end_idx = c.find('\n    def update_from_redis', idx)
old_block = c[idx:end_idx]

new_block = '''    def record_success(self, model_name: str):
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

'''

c = c.replace(old_block, new_block)
with open(path, 'w') as f: f.write(c)
compile(c, 'mr2', 'exec')
print('OK')
