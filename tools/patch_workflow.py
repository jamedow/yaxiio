#!/usr/bin/env python3
"""Patch workflow_engine.py + yaxiio.py with foolproof guards"""
import sys; sys.path.insert(0,'/opt/yaxiio')

for path, label in [
    ("/opt/yaxiio/.pi/skills/commander/workflow_engine.py", "workflow_engine"),
    ("/opt/yaxiio/.pi/skills/commander/yaxiio.py", "yaxiio"),
]:
    with open(path) as f:
        content = f.read()
    changes = 0
    
    # --- Common patch: add foolproof import ---
    if "foolproof" not in content:
        for marker in ["from task_state_machine import TaskStateMachine",
                       "from workflow_engine import WorkflowEngine"]:
            if marker in content:
                content = content.replace(marker,
                    f"from modules.shared.foolproof import safe_default, validate_in_range\n{marker}")
                changes += 1
                print(f"{label}: foolproof import")
                break
    
    # --- workflow_engine specific patches ---
    if label == "workflow_engine":
        # Patch POLL_TIMEOUT to use safe default
        old_poll = "POLL_TIMEOUT = 120"
        if old_poll in content:
            content = content.replace(old_poll,
                "POLL_TIMEOUT = safe_default('task_timeout')  # 防呆: 使用集中管理的默认值")
            changes += 1
            print(f"{label}: POLL_TIMEOUT -> safe_default")
        
        # Patch validate subtask count
        old_sub = "        subtasks = self._decompose_via_l2(task_id, payload)"
        if old_sub in content:
            new_sub = """        subtasks = self._decompose_via_l2(task_id, payload)
        # 防呆: 限制子任务数量
        max_subtasks = safe_default('subtask_max_count')
        if len(subtasks) > max_subtasks:
            print(f"[WF] {task_id} 子任务过多 ({len(subtasks)}), 截断到 {max_subtasks}", flush=True)
            subtasks = subtasks[:max_subtasks]"""
            content = content.replace(old_sub, new_sub)
            changes += 1
            print(f"{label}: subtask count guard")
    
    # --- yaxiio specific patches ---
    if label == "yaxiio":
        # Patch handle_task REJECTED message
        old_rej = '"constitution_advice": "请通过五层 MCP 流水线提交此任务"'
        if old_rej in content:
            new_rej = '''"constitution_advice": "该操作被宪法拒绝。请通过 Dashboard 或 API 提交任务，系统将自动走 L1→L5 流水线。如有疑问，查看 /opt/yaxiio/docs/CONSTITUTION.md"'''
            content = content.replace(old_rej, new_rej)
            changes += 1
            print(f"{label}: friendly reject message")
        
        # Patch max concurrent agents
        old_mx = "MAX_AGENTS = 10"
        if old_mx not in content:
            old_mx = "max_concurrent"
        # Add safe default for max_concurrent
        old_loop = "while self.running:"
        if old_loop in content and "safe_default" in content:
            pass  # already imported
    
    with open(path, "w") as f:
        f.write(content)
    print(f"  → {changes} changes\n")
