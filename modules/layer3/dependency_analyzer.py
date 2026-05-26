"""依赖分析器"""
class DependencyAnalyzer:
    def analyze(self, subtasks: list) -> dict:
        s = [x for x in subtasks if x.get("depends_on")]; p = [x for x in subtasks if not x.get("depends_on")]
        return {"serial":s,"parallel":p,"can_parallelize":len(p)>1}
