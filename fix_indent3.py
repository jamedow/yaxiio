#!/usr/bin/env python3
"""Restore correct indentation of LLM decomposition block from git history"""
import subprocess

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"

# Read current file
with open(path) as f:
    current = f.read()

# Get the original block from before our changes (9c7b2bc)
result = subprocess.run(
    ["git", "-C", "/opt/yaxiio", "show",
     "9c7b2bc:.pi/skills/commander/workflow_engine.py"],
    capture_output=True, text=True
)
original = result.stdout

# Extract the correct block from original
# Find "prompt = \"\"\"Decompose" in original
orig_idx = original.find('        prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.')
if orig_idx < 0:
    print("FAIL: original block not found in git history")
    exit(1)

# Find end of the block: up to and including 'prompt += "Task: " + task_desc[:400]'
end_marker = 'prompt += "Task: " + task_desc[:400]'
orig_end = original.find(end_marker, orig_idx)
if orig_end < 0:
    print("FAIL: end marker not found")
    exit(1)

orig_block = original[orig_idx:orig_end + len(end_marker)]

# Find the bad block in current file
curr_idx = current.find('                prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.')
if curr_idx < 0:
    curr_idx = current.find('        prompt """Decompose this task')  # already damaged
if curr_idx < 0:
    print("FAIL: bad block not found in current file")
    # Show what's there
    idx = current.find('prompt = """Decompose')
    if idx > 0:
        print(f"  Found 'prompt = \"\"\"Decompose' at offset {idx}")
        print(f"  Context: {repr(current[idx-10:idx+80])}")
    exit(1)

# Find the bad block's end
curr_end_marker = 'prompt += "Task: " + task_desc[:400]'
curr_end = current.find(curr_end_marker, curr_idx)
if curr_end < 0:
    print("FAIL: current end marker not found")
    exit(1)

curr_block = current[curr_idx:curr_end + len(curr_end_marker)]

# Add the experience_context and primary_agent lines
enhanced_block = (
    '        prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.\n'
    '\n'
    'Available agents: 审计官(audit), 品牌策略师(brand/strategy), 翻译官(translate), UI/UX设计师(design), 前端工程师(frontend), LM内容工程师(content engineering)\n'
    '\n'
    '"""\n'
    '        if experience_context:\n'
    '            prompt += experience_context[:1200] + "\\n\\n"\n'
    '        if primary_agent:\n'
    '            prompt += f"Hint: best matching agent is {primary_agent}\\n\\n"\n'
    '        prompt += "Task: " + task_desc[:400]'
)

current = current.replace(curr_block, enhanced_block)
print(f"Replaced block: {len(curr_block)} -> {len(enhanced_block)} chars")

with open(path, "w") as f:
    f.write(current)

# Verify
try:
    compile(current, "wf", "exec")
    print("✅ Syntax OK!")
except SyntaxError as e:
    print(f"❌ {e}")
