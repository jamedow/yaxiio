"""Agent 角色继承 — 新Agent自动继承父角色能力"""
from typing import Dict, List, Optional

# 能力继承树
INHERITANCE_TREE = {
    "CodeAuditor": {
        "extends": None,
        "capabilities": ["code_audit", "security_scan", "quality_report"],
        "model": "deepseek-v4-pro"
    },
    "FrontendEngineer": {
        "extends": "CodeAuditor",  # 继承代码审计能力
        "capabilities": ["responsive_fix", "css_debug", "layout_audit"],
        "model": "deepseek-v4-pro"
    },
    "BackendEngineer": {
        "extends": "CodeAuditor",
        "capabilities": ["api_debug", "database_optimize", "performance_tune"],
        "model": "deepseek-v4-pro"
    },
    "SystemDiagnostician": {
        "extends": "CodeAuditor",
        "capabilities": ["oom_diagnose", "memory_leak", "crash_analysis"],
        "model": "deepseek-v4-pro"
    },
    "FullStackEngineer": {
        "extends": "FrontendEngineer",  # 多层继承
        "capabilities": ["fullstack_debug", "deployment_audit"],
        "model": "deepseek-v4-pro"
    }
}

class AgentInheritance:
    """角色继承管理器"""
    def __init__(self, tree: dict = None):
        self.tree = tree or INHERITANCE_TREE

    def resolve(self, role: str) -> dict:
        """解析角色的完整能力（含继承）"""
        if role not in self.tree:
            return self._default(role)

        caps = set()
        model = "deepseek-v4-pro"
        current = role

        # 沿继承链向上收集能力
        while current and current in self.tree:
            node = self.tree[current]
            for c in node.get("capabilities", []):
                caps.add(c)
            if not model or model == "deepseek-v4-pro":
                model = node.get("model", model)
            current = node.get("extends")

        return {
            "role": role,
            "capabilities": sorted(caps),
            "model": model,
            "inherits_from": self._get_lineage(role)
        }

    def _get_lineage(self, role: str) -> List[str]:
        lineage = [role]
        current = role
        while current in self.tree:
            parent = self.tree[current].get("extends")
            if parent:
                lineage.append(parent)
                current = parent
            else:
                break
        return lineage

    def _default(self, role: str) -> dict:
        return {"role": role, "capabilities": [role.lower()], "model": "deepseek-v4-pro", "inherits_from": []}

    def add_role(self, role: str, extends: str, capabilities: List[str], model: str = "deepseek-v4-pro"):
        self.tree[role] = {"extends": extends, "capabilities": capabilities, "model": model}

    def list_all(self) -> List[dict]:
        return [self.resolve(r) for r in self.tree]
