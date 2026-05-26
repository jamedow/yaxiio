"""模型路由器"""
class ModelRouter:
    RULES = {
        "complex": {"model":"deepseek-v4-pro","thinking":"max","keywords":["分析","拆解","优化","审计"]},
        "stable": {"model":"deepseek-v4-pro","thinking":"high","keywords":["修复","创建","生成"]},
        "fast": {"model":"deepseek-v4-flash","thinking":"off","keywords":["翻译","查询","检查"]},
    }
    def select_model(self, task: dict) -> dict:
        desc = str(task.get("description","")) + str(task.get("action",""))
        for rule in self.RULES.values():
            for kw in rule["keywords"]:
                if kw in desc: return rule
        return self.RULES["stable"]
