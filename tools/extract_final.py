#!/usr/bin/env python3
"""Final extraction: all 7 remaining methods from workflow_engine.py"""
path = '/opt/yaxiio/.pi/skills/commander/workflow_engine.py'
with open(path) as f: c = f.read()

extractions = [
    # (start_marker, end_marker, delegate_name, function_name)
    ('_execute_subtask', '_decompose_via_l2', 'execute_subtask'),
    ('_decompose_via_l2', '_llm_decompose', 'decompose_via_l2'),
    ('_llm_decompose', '_do_L5', 'llm_decompose'),
    ('_do_L5', '_build_plan', 'do_l5'),
    ('_orchestrate_subtasks', '_execute_subtask', 'orchestrate_subtasks'),
]

for start_marker, end_marker, func_name in extractions:
    idx_s = c.find(f'    def _{start_marker}(self,')
    idx_e = c.find(f'    def _{end_marker}(self,', idx_s + 1)
    if idx_e < 0:
        idx_e = c.find(f'    def _{end_marker}(', idx_s + 1)
    if idx_e < 0:
        print(f"SKIP {start_marker}: end marker {end_marker} not found")
        continue
    
    old = c[idx_s:idx_e]
    new = f'    def _{start_marker}(self, *args, **kwargs):\n        from workflow_utils_extracted import {func_name}\n        return {func_name}(self, *args, **kwargs)\n\n'
    c = c.replace(old, new)
    print(f"{start_marker}: {len(old)} -> {len(new)} chars")

with open(path, 'w') as f: f.write(c)
print(f"\nDone. Lines: {len(c.splitlines())}")
