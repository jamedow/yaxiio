# Yaxiio v1.1 - AGPLv3
"""Workflow utilities — static helpers for LLM, skills, scoring"""
import json

class WorkflowUtils:
    @staticmethod
    def _bump_thinking(current: str) -> str:
        order = ["off", "low", "medium", "high", "max"]
        try:
            idx = order.index(current)
            return order[min(idx + 1, len(order) - 1)]
        except ValueError:
            return "high"

    @staticmethod
    def _agent_skill_map() -> dict:
        return {
            "UI/UX设计师": "ui-ux-designer",
            "品牌策略师": "strategic-partner",
            "前端工程师": "infrastructure-engineer",
            "翻译官": "translate-engine",
            "审计官": "audit-engine",
            "售前经理": "product-search",
            "商务经理": "product-search",
            "通用Agent": "",
            "修复Agent": "backend-engineer",
            "系统医生": "system-doctor",
            "LM内容工程师": "lm-content-engineer",
        }

    @staticmethod
    def _get_llm():
        try:
            from constitution import LLMAdapter
            return LLMAdapter(api_key="", base_url="https://api.deepseek.com/v1",
                            model="deepseek-chat", thinking="medium")
        except:
            return None

    @staticmethod
    def _call_llm(prompt: str, timeout: float = 30.0) -> str:
        llm = WorkflowUtils._get_llm()
        if not llm:
            return ""
        try:
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=1000,
            )
            return resp.choices[0].message.content
        except:
            return ""
