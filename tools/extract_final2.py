#!/usr/bin/env python3
"""Final extraction batch — remaining inline methods from workflow_engine.py"""
path = '/opt/yaxiio/.pi/skills/commander/workflow_engine.py'
with open(path) as f: c = f.read()

extractions = [
    # (method_name, params_after_self)
    ("_clone_agents_for_task", "task_id, subtasks"),
    ("_schedule_via_l3", "task_id, subtasks"),
    ("_execute_subtask", "task_id, sid, subtask, payload"),
    ("_orchestrate_subtasks", "task_id, subtasks, payload"),
    ("_decompose_via_l2", "task_id, payload"),
    ("_llm_decompose", "task_id, payload, experience_context, primary_agent"),
    ("_do_L5", "task_id, action, plan, l4, state"),
    ("_process_simple", "task_id, payload"),
    ("_process_complex", "task_id, payload"),
]

for method_name, params in extractions:
    marker = f'    def {method_name}(self,'
    idx_s = c.find(marker)
    if idx_s < 0:
        print(f"SKIP {method_name}: not found")
        continue
    
    # Find the end: next '    def ' at same indent level
    idx_e = len(c)
    next_def = c.find('\n    def ', idx_s + len(marker))
    if next_def > idx_s:
        idx_e = next_def
    
    old = c[idx_s:idx_e]
    
    # Keep _process_complex and _process_simple in place — they're orchestration
    if method_name in ('_process_simple', '_process_complex', '_do_L5'):
        print(f"KEEP {method_name}: orchestration logic stays in WorkflowEngine")
        continue
    
    params_list = params.split(', ')
    args_pass = ', '.join(p for p in params_list)
    
    new = f'    def {method_name}(self, {params}):\n        from workflow_utils_extracted import {method_name}\n        return {method_name}(self, {args_pass})\n\n'
    c = c.replace(old, new)
    print(f"{method_name}: {len(old)} -> {len(new)} chars")

with open(path, 'w') as f: f.write(c)
print(f"\nFinal line count: {len(c.splitlines())}")
