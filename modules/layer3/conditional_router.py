class ConditionalRouter:
    """条件分支路由器"""
    BRANCHES = {
        "site_audit": {
            "on_success": {"action": "report", "message": "Audit completed"},
            "on_fail": {"action": "site_fix", "message": "Auto-trigger fix"},
            "score_threshold": 6.0
        },
        "site_fix": {
            "on_success": {"action": "site_audit", "message": "Verify fix"},
            "on_fail": {"action": "notify", "message": "Manual review needed"},
        },
        "debug_oom": {
            "on_success": {"action": "site_fix", "message": "Apply OOM fix"},
            "on_fail": {"action": "escalate", "message": "Escalate to SystemDiagnostician"},
        }
    }

    def __init__(self, state_machine=None):
        self.sm = state_machine

    def route(self, task_id: str, result: dict) -> dict:
        """根据结果决定下一步"""
        state = self.sm.get(task_id) if self.sm else {}
        action = state.get("action", "") if state else ""
        status = result.get("status", "")
        score = result.get("score", result.get("overall", 0))

        branches = self.BRANCHES.get(action, {})
        if status == "success" and score >= branches.get("score_threshold", 6.0):
            return branches.get("on_success", {"action": "complete"})
        else:
            return branches.get("on_fail", {"action": "notify", "message": "Task failed"})

    def add_branch(self, action: str, on_success: dict, on_fail: dict, threshold: float = 6.0):
        self.BRANCHES[action] = {"on_success": on_success, "on_fail": on_fail, "score_threshold": threshold}

    def list_branches(self) -> dict:
        return self.BRANCHES
