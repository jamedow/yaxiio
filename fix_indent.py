#!/usr/bin/env python3
"""Fix indentation at line 907"""
path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path) as f:
    lines = f.readlines()

# Line 905 (0-indexed: 904): return statement at 12 spaces
# Line 907 (0-indexed: 906): prompt = """ at 16 spaces -> should be 8 spaces
# Lines 909-911 (0-indexed: 908-910): triple-quoted content at 0 spaces (OK)

# Fix: reduce indent on line 907 from 16 to 8
for i in [906]:  # 0-indexed line 907
    if lines[i].startswith('                prompt'):
        lines[i] = '        prompt' + lines[i][24:]  # remove 8 spaces
        print(f"Fixed line {i+1}")

# Also check: is there a blank line and return before it?
for i in [904, 905]:
    print(f"Line {i+1}: [{len(lines[i]) - len(lines[i].lstrip())}sp] {lines[i].rstrip()[:80]}")

with open(path, "w") as f:
    f.writelines(lines)

# Verify
try:
    compile("".join(lines), "wf", "exec")
    print("✅ Syntax OK")
except SyntaxError as e:
    print(f"❌ Still broken: {e}")
