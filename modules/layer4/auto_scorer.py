"""LLM自动评分 - 多维度评估版"""
import re
import math
from typing import Dict, Any

# 尝试导入外部依赖，若不可用则使用默认实现
try:
    from modules.shared.config import SCORING_WEIGHTS, PASS_THRESHOLD
except ImportError:
    SCORING_WEIGHTS = {"completeness": 0.4, "code_quality": 0.3, "design": 0.3}
    PASS_THRESHOLD = 6.0

try:
    from .code_analyzer import static_analysis
except ImportError:
    def static_analysis(code: str) -> Dict[str, float]:
        """内置简易静态分析，返回0-10质量分"""
        if not code:
            return {"score": 5.0}
        lines = code.split('\n')
        total = max(1, len(lines))
        comments = sum(1 for l in lines if l.strip().startswith('#') or '"""' in l or "'''" in l)
        blanks = sum(1 for l in lines if not l.strip())
        code_lines = total - blanks - comments
        # 注释率
        comment_ratio = comments / total
        # 平均行长度（复杂度启发）
        avg_len = sum(len(l) for l in lines) / total if total else 0
        # 是否存在异常处理
        has_error_handling = any('try' in l or 'except' in l or 'raise' in l for l in lines)
        # 函数/类定义数量
        defs = len(re.findall(r'\bdef\b|\bclass\b', code))
        
        score = 5.0
        score += min(2.0, comment_ratio * 10)  # 注释最多+2
        if has_error_handling: score += 1.5
        score += min(1.5, defs * 0.3)  # 定义数适度加分
        if avg_len > 80: score -= 0.5  # 过长行惩罚
        if total > 0 and code_lines < total * 0.5: score -= 1.0  # 代码密度过低
        return {"score": max(0.0, min(10.0, score))}


class AutoScorer:
    """多维度自动评分器，从完成度、代码质量、设计合规三维度评估任务结果"""
    
    def score(self, task: dict, result: dict) -> dict:
        # 完成度评估 (40%)
        completeness = self._completeness(task, result)
        # 代码质量评估 (30%)
        quality = self._code_quality(result)
        # 设计合规评估 (30%)
        design = self._design_compliance(task, result)
        
        # 加权总分
        s = (completeness * SCORING_WEIGHTS["completeness"] +
             quality * SCORING_WEIGHTS["code_quality"] +
             design * SCORING_WEIGHTS["design"])
        s = round(min(10.0, max(0.0, s)), 2)
        
        return {
            "score": s,
            "task_id": task.get("task_id", ""),
            "passed": s >= PASS_THRESHOLD,
            "details": {
                "completeness": round(completeness, 2),
                "code_quality": round(quality, 2),
                "design": round(design, 2)
            }
        }
    
    def _completeness(self, task: dict, result: dict) -> float:
        """评估任务完成度和需求覆盖率"""
        # 若结果明确失败，完成度极低
        if result.get("status") == "failed":
            return 2.0
        
        # 基于子任务完成率
        subs = result.get("subtasks", [])
        if subs:
            done = sum(1 for x in subs if x.get("status") in ("completed", "dispatched", "success"))
            ratio = done / len(subs)
            return 5.0 + ratio * 5.0  # 5-10分
        
        # 基于需求匹配度
        reqs = task.get("requirements", [])
        if reqs:
            output_text = str(result.get("output", ""))
            matched = sum(1 for r in reqs if r.lower() in output_text.lower())
            ratio = matched / len(reqs) if reqs else 0
            return 3.0 + ratio * 7.0  # 3-10分
        
        # 默认基于状态
        if result.get("status") == "success":
            return 8.0
        return 5.0
    
    def _code_quality(self, result: dict) -> float:
        """评估产出代码/内容的质量"""
        # 优先检查显式的code字段
        code = result.get("code", "")
        if not code:
            # 尝试从output或artifacts中提取代码
            artifacts = result.get("artifacts", [])
            for art in artifacts:
                if isinstance(art, dict) and art.get("type") == "code":
                    code = art.get("content", "")
                    break
        if not code:
            code = result.get("output", "") if isinstance(result.get("output"), str) else ""
        
        if not code:
            # 无可分析代码，给予基础分
            return 5.0
        
        analysis = static_analysis(code)
        return analysis.get("score", 5.0)
    
    def _design_compliance(self, task: dict, result: dict) -> float:
        """评估设计模式和架构合规性"""
        task_type = task.get("type", "").lower()
        output = result.get("output", {}) if isinstance(result.get("output"), dict) else {}
        code = result.get("code", "")
        all_text = str(output) + str(code) + str(result.get("design_doc", ""))
        
        score = 5.0
        # 根据任务类型检查必要结构
        if task_type == "api":
            if "endpoint" in all_text.lower() or "route" in all_text.lower(): score += 2.0
            if "request" in all_text.lower() and "response" in all_text.lower(): score += 1.5
            if "method" in all_text.lower() or "get" in all_text.lower() or "post" in all_text.lower(): score += 1.0
        elif task_type == "module" or task_type == "class":
            if "class " in code: score += 2.0
            if "def " in code: score += 1.0
            if "__init__" in code: score += 1.5
        elif task_type == "function":
            if "def " in code: score += 2.0
            if "return" in code: score += 1.0
            if "docstring" in all_text.lower() or '"""' in code: score += 1.5
        
        # 通用合规检查
        if "error handling" in all_text.lower() or "try" in code or "except" in code: score += 1.0
        if "test" in all_text.lower() or "unittest" in all_text.lower(): score += 1.0
        
        return max(0.0, min(10.0, score))