"""
任务拆解器 - 基于关键词+规则+LLM的三级拆解策略
"""
import json
import re
import logging
from typing import List, Dict, Optional, Any

# 尝试导入项目共享配置和LLM客户端
try:
    from modules.shared.config import config  # Yaxiio config
except ImportError:
    class _DefaultConfig:
        keyword_plans = {}
        llm_decomposer_enabled = True
    config = _DefaultConfig()

LLMClient = None  # unavailable

logger = logging.getLogger(__name__)


class TaskDecomposer:
    """任务拆解器，使用策略模式根据任务描述自动生成子任务列表"""

    def __init__(self):
        # Level 1: 关键词精确匹配计划（可从配置扩展）
        self.keyword_plans = getattr(config, 'keyword_plans', {
            "site_audit": [
                {"action": "scan_codebase"},
                {"action": "llm_analyze", "depends_on": [1]},
                {"action": "write_report", "depends_on": [2]}
            ],
            "site_fix": [
                {"action": "read_audit"},
                {"action": "generate_fixes", "depends_on": [1]},
                {"action": "apply_fixes", "depends_on": [2]}
            ],
        })
        # Level 2: 规则引擎（按顺序匹配）
        self.rules = [
            self._rule_fix,
            self._rule_build,
            self._rule_analyze,
            self._rule_crud,
            self._rule_generic,
        ]
        # Level 3: LLM支持
        self.llm_enabled = getattr(config, 'llm_decomposer_enabled', True)
        self.llm_client = LLMClient() if (LLMClient and self.llm_enabled) else None

    def decompose(self, task: dict) -> list:
        """
        拆解任务为子任务列表
        Args:
            task: 包含 action, description, params, context 等字段的字典
        Returns:
            list[dict]: 每个子任务含 id, action, 可选 depends_on
        """
        action = task.get("action", "")

        # 1. 关键词精确匹配
        if action in self.keyword_plans:
            return self._finalize_plan(self.keyword_plans[action])

        # 2. 规则引擎匹配
        for rule in self.rules:
            plan = rule(task)
            if plan:
                return self._finalize_plan(plan)

        # 3. LLM 生成
        if self.llm_client:
            try:
                plan = self._llm_decompose(task)
                if plan:
                    return self._finalize_plan(plan)
            except Exception as e:
                logger.warning(f"LLM decomposition failed: {e}")

        # 最终回退：将任务本身作为单一子任务
        return [{"id": 1, "action": action, "params": task.get("params", {})}]

    def _finalize_plan(self, steps: list) -> list:
        """规范化子任务列表：确保每个步骤有id，depends_on引用正确"""
        finalized = []
        for i, step in enumerate(steps):
            new_step = dict(step)
            if "id" not in new_step:
                new_step["id"] = i + 1
            finalized.append(new_step)
        return finalized

    # ---------- 规则定义 ----------
    def _rule_fix(self, task: dict) -> Optional[list]:
        action = task.get("action", "").lower()
        if any(kw in action for kw in ("fix", "repair", "resolve", "patch")):
            return [
                {"action": "diagnose_issue", "params": task.get("params")},
                {"action": "plan_fix", "depends_on": [1]},
                {"action": "execute_fix", "depends_on": [2]},
                {"action": "verify_fix", "depends_on": [3]},
            ]
        return None

    def _rule_build(self, task: dict) -> Optional[list]:
        action = task.get("action", "").lower()
        if any(kw in action for kw in ("build", "compile", "deploy", "release")):
            return [
                {"action": "prepare_environment"},
                {"action": "fetch_dependencies", "depends_on": [1]},
                {"action": "run_build", "depends_on": [2]},
                {"action": "run_tests", "depends_on": [3]},
                {"action": "publish_artifacts", "depends_on": [4]},
            ]
        return None

    def _rule_analyze(self, task: dict) -> Optional[list]:
        action = task.get("action", "").lower()
        if any(kw in action for kw in ("analyze", "audit", "inspect", "review")):
            return [
                {"action": "collect_data"},
                {"action": "run_analysis", "depends_on": [1]},
                {"action": "generate_report", "depends_on": [2]},
            ]
        return None

    def _rule_crud(self, task: dict) -> Optional[list]:
        """处理 CRUD 类任务"""
        action = task.get("action", "").lower()
        if any(kw in action for kw in ("create", "read", "update", "delete")):
            entity = task.get("entity", "resource")
            return [
                {"action": f"validate_{entity}"},
                {"action": f"{action}_{entity}", "depends_on": [1]},
                {"action": f"log_{action}", "depends_on": [2]},
            ]
        return None

    def _rule_generic(self, task: dict) -> Optional[list]:
        """通用规则：描述本身是步骤列表"""
        desc = task.get("description", task.get("desc", ""))
        if isinstance(desc, list) and all(isinstance(s, (str, dict)) for s in desc):
            steps = []
            for i, s in enumerate(desc):
                if isinstance(s, str):
                    steps.append({"action": s})
                else:
                    steps.append(s)
            return steps
        return None

    # ---------- LLM 策略 ----------
    def _llm_decompose(self, task: dict) -> Optional[list]:
        if not self.llm_client:
            return None
        prompt = self._build_llm_prompt(task)
        response = self.llm_client.complete(prompt)
        return self._parse_llm_response(response)

    def _build_llm_prompt(self, task: dict) -> str:
        action = task.get("action", "")
        desc = task.get("description", "")
        params = task.get("params", {})
        context = task.get("context", "")
        prompt = f"""You are a task planner. Decompose the following task into subtasks.
Task Action: {action}
Description: {desc}
Parameters: {json.dumps(params, indent=2)}
Context: {context}

Output a JSON list of subtasks. Each subtask has:
- "id": int
- "action": string
- "depends_on": list of int (optional)

JSON:
"""
        return prompt

    def _parse_llm_response(self, text: str) -> list:
        # 提取 JSON 列表
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if not match:
                raise ValueError("No JSON list found in LLM response")
            data = json.loads(match.group())
        if not isinstance(data, list):
            raise ValueError("LLM response is not a list")
        return data