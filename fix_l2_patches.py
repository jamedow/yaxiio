#!/usr/bin/env python3
"""Fix L2 experience injection - apply 4 patches to workflow_engine.py"""
import os

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path, "r") as f:
    content = f.read()

changes = 0

# ── Fix 1: _decompose_via_l2 — format experience + inject into call_layer ──
old1 = (
    "        # L0: Retrieve past experiences for this intent\n"
    "        past_exp = self.l0._retrieve_experiences(action_clean, available[:5])\n"
    "        if past_exp:\n"
    "            print(f\"[L0] {task_id} found {len(past_exp)} past experiences for '{action_clean}'\", flush=True)\n"
    "\n"
    "        try:\n"
    "            result = call_layer(2, \"decompose_task\",\n"
    "                               task_id=task_id, task=task_desc,\n"
    "                               available_agents=available[:8])"
)

new1 = (
    "        # L0: Retrieve past experiences for this intent\n"
    "        past_exp = self.l0._retrieve_experiences(action_clean, available[:5])\n"
    "        experience_context = \"\"\n"
    "        if past_exp:\n"
    "            print(f\"[L0] {task_id} found {len(past_exp)} past experiences for '{action_clean}'\", flush=True)\n"
    "            # Format experience for LLM injection\n"
    "            exp_lines = [\"## 历史经验（同类任务参考）\"]\n"
    "            for i, exp in enumerate(past_exp[:3]):\n"
    "                agents_used = exp.get(\"agents_involved\", [exp.get(\"agent\", \"?\")])\n"
    "                subtask_actions = exp.get(\"subtask_actions\", [])\n"
    "                score = exp.get(\"score\", \"?\")\n"
    "                success_mark = \"✅\" if exp.get(\"success\") else \"❌\"\n"
    "                exp_lines.append(f\"### 案例{i+1} (评分:{score}/10 {success_mark})\")\n"
    "                exp_lines.append(f\"- Agent: {', '.join(agents_used)}\")\n"
    "                if subtask_actions:\n"
    "                    steps = ' → '.join(str(sa)[:60] for sa in subtask_actions[:5])\n"
    "                    exp_lines.append(f\"- 步骤: {steps}\")\n"
    "            experience_context = \"\\n\".join(exp_lines)\n"
    "        else:\n"
    "            # Chroma semantic search fallback\n"
    "            try:\n"
    "                from modules.layer1.vector_store_chroma import ChromaVectorStore\n"
    "                vs = ChromaVectorStore()\n"
    "                semantic = vs.search(f\"task:{task_desc[:200]}\", top_k=3)\n"
    "                if semantic:\n"
    "                    exp_lines = [\"## 语义相似经验\"]\n"
    "                    for i, s in enumerate(semantic):\n"
    "                        exp_lines.append(f\"### 类似任务{i+1}\\n{s.get('text','')[:300]}\")\n"
    "                    experience_context = \"\\n\".join(exp_lines)\n"
    "                    print(f\"[L0] {task_id} Chroma 语义: {len(semantic)} 条\", flush=True)\n"
    "            except Exception:\n"
    "                pass\n"
    "\n"
    "        try:\n"
    "            result = call_layer(2, \"decompose_task\",\n"
    "                               task_id=task_id, task=task_desc,\n"
    "                               available_agents=available[:8],\n"
    "                               experience_context=experience_context[:1500])"
)

if old1 in content:
    content = content.replace(old1, new1)
    changes += 1
    print("✅ Fix 1: experience injection in _decompose_via_l2")
else:
    print("❌ Fix 1: pattern not found")

# ── Fix 2: pass experience_context to _llm_decompose ──
old2 = "        return self._llm_decompose(task_id, payload)"
new2 = "        return self._llm_decompose(task_id, payload, experience_context)"
if old2 in content:
    content = content.replace(old2, new2)
    changes += 1
    print("✅ Fix 2: pass experience_context to _llm_decompose")
else:
    print("❌ Fix 2: pattern not found")

# ── Fix 3: _llm_decompose signature ──
old3 = "    def _llm_decompose(self, task_id: str, payload: dict) -> list:"
new3 = '    def _llm_decompose(self, task_id: str, payload: dict, experience_context: str = "") -> list:'
if old3 in content:
    content = content.replace(old3, new3)
    changes += 1
    print("✅ Fix 3: _llm_decompose signature updated")
else:
    print("❌ Fix 3: pattern not found")

# ── Fix 4: inject experience_context into LLM decomposition prompt ──
old4_marker = 'prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.'
idx4 = content.find(old4_marker)
if idx4 >= 0:
    end_marker = 'prompt += "Task: " + task_desc[:400]'
    idx4_end = content.find(end_marker, idx4)
    if idx4_end >= 0:
        old4 = content[idx4:idx4_end + len(end_marker)]
        new4 = (
            '        prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.\n'
            '\n'
            'Available agents: 审计官(audit), 品牌策略师(brand/strategy), 翻译官(translate), UI/UX设计师(design), 前端工程师(frontend), LM内容工程师(content engineering)\n'
            '\n'
            '"""\n'
            '        if experience_context:\n'
            '            prompt += experience_context[:1200] + "\\n\\n"\n'
            '        prompt += "Task: " + task_desc[:400]'
        )
        content = content.replace(old4, new4)
        changes += 1
        print("✅ Fix 4: LLM prompt gets experience_context")
    else:
        print("❌ Fix 4: end marker not found")
else:
    print("❌ Fix 4: start marker not found")

# Write back
with open(path, "w") as f:
    f.write(content)

print(f"\n{changes}/4 fixes applied to {path}")
