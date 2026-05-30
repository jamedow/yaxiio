#!/usr/bin/env python3
"""Fix indentation at line 907 — proper fix"""
path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path) as f:
    lines = f.readlines()

# Line 907 (0-indexed 906): 16 spaces -> 8 spaces
line = lines[906]
leading = len(line) - len(line.lstrip())
print(f"Line 907: {leading} spaces leading, first chars: {repr(line[:30])}")

if leading == 16 and line.lstrip().startswith('prompt'):
    # Keep everything after the first 16 chars, add 8 spaces
    lines[906] = '        ' + line[16:]
    print(f"Fixed: now {len(lines[906]) - len(lines[906].lstrip())} spaces leading")

with open(path, "w") as f:
    f.writelines(lines)

try:
    compile("".join(lines), "wf", "exec")
    print("✅ Syntax OK")
except SyntaxError as e:
    print(f"❌ {e}")
