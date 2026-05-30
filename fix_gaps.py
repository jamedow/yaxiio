#!/usr/bin/env python3
"""Fix 4 remaining gaps:
1. _primary_agent from semantic router passed to LLM decomposition
2. ExperienceFlywheel initialized once in __init__, reused in _cleanup_task
3. constitution FORBIDDEN_DIRECT loadable from Redis
4. neuron AGENT_CONFIG passed by Commander spawn_neuron
"""
import os

PATH_WF = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
PATH_YX = "/opt/yaxiio/.pi/skills/commander/yaxiio.py"
PATH_NEURON = "/opt/yaxiio/.pi/skills/commander/neuron.py"
PATH_CONST = "/opt/yaxiio/.pi/skills/commander/constitution.py"

changes = 0

# ═══ Fix 1: _primary_agent → LLM decomposition ═══
with open(PATH_WF) as f:
    wf = f.read()

old_ret = "        return self._llm_decompose(task_id, payload, experience_context)"
new_ret = "        return self._llm_decompose(task_id, payload, experience_context, _primary_agent)"
if old_ret in wf:
    wf = wf.replace(old_ret, new_ret)
    changes += 1
    print("OK: _primary_agent passed to _llm_decompose")

# Update _llm_decompose_with_experience (the one with experience_context) signature
# and _llm_decompose to accept primary_agent hint
old_sig1 = '    def _llm_decompose(self, task_id: str, payload: dict, experience_context: str = "") -> list:'
new_sig1 = '    def _llm_decompose(self, task_id: str, payload: dict, experience_context: str = "", primary_agent: str = None) -> list:'
if old_sig1 in wf:
    wf = wf.replace(old_sig1, new_sig1)
    changes += 1
    print("OK: _llm_decompose signature extended with primary_agent")

# Inject primary_agent into the LLM prompt as a hint
old_hint = '        prompt += "Task: " + task_desc[:400]'
# Find it
idx = wf.find(old_hint)
if idx > 0:
    new_hint = (
        '        if primary_agent:\n'
        '            prompt += f"Hint: best matching agent is {primary_agent}\\n\\n"\n'
        '        prompt += "Task: " + task_desc[:400]'
    )
    # Find the exact occurrence (there may be multiple "prompt +=")
    # The one after experience_context injection
    search_start = max(0, idx - 200)
    snippet = wf[search_start:idx + len(old_hint) + 50]
    if old_hint in snippet:
        wf = wf.replace(old_hint, new_hint, 1)  # replace first occurrence only
        changes += 1
        print("OK: primary_agent hint injected into LLM prompt")
    else:
        print("WARN: primary_agent hint injection skipped (pattern mismatch)")

with open(PATH_WF, "w") as f:
    f.write(wf)

# ═══ Fix 2: ExperienceFlywheel in __init__ ═══
# Replace per-call instantiation with __init__ reference
old_fw_init = (
    '            from modules.layer5.experience_flywheel import ExperienceFlywheel\n'
    '            from modules.layer1.vector_store_chroma import ChromaVectorStore\n'
    '            _vs = ChromaVectorStore()\n'
    '            flywheel = ExperienceFlywheel(\n'
    '                redis_client=self.commander.redis,\n'
    '                vector_store=_vs\n'
    '            )'
)
new_fw_init = (
    '            flywheel = self.flywheel'
)
if old_fw_init in wf:
    wf = wf.replace(old_fw_init, new_fw_init)
    # Also update the fallback error message
    wf = wf.replace(
        'print(f"[WF] {task_id} flywheel failed ({_e}), fallback to l0", flush=True)',
        'print(f"[WF] {task_id} flywheel failed, fallback to l0", flush=True)'
    )
    changes += 1
    print("OK: ExperienceFlywheel reused from instance")

# Add flywheel to __init__
old_fw_marker = (
    '        self.data_bus = RedisDataBus(\n'
    '            redis_client=self.commander.redis if self.commander else None\n'
    '        )'
)
new_fw_init_block = (
    '        self.data_bus = RedisDataBus(\n'
    '            redis_client=self.commander.redis if self.commander else None\n'
    '        )\n'
    '\n'
    '        # ── L5: Experience Flywheel ──\n'
    '        from modules.layer5.experience_flywheel import ExperienceFlywheel\n'
    '        try:\n'
    '            from modules.layer1.vector_store_chroma import ChromaVectorStore\n'
    '            _fw_vs = ChromaVectorStore()\n'
    '        except Exception:\n'
    '            from modules.layer1.vector_store import MemVectorStore\n'
    '            _fw_vs = MemVectorStore()\n'
    '        self.flywheel = ExperienceFlywheel(\n'
    '            redis_client=self.commander.redis if self.commander else None,\n'
    '            vector_store=_fw_vs\n'
    '        )'
)
if old_fw_marker in wf:
    wf = wf.replace(old_fw_marker, new_fw_init_block)
    changes += 1
    print("OK: ExperienceFlywheel initialized in __init__")

with open(PATH_WF, "w") as f:
    f.write(wf)

# ═══ Fix 3: constitution FORBIDDEN_DIRECT from Redis ═══
with open(PATH_CONST) as f:
    const = f.read()

old_forbidden = (
    '    FORBIDDEN_DIRECT: set = {\n'
    '        "site_audit",\n'
    '        "site_fix",\n'
    '        "site_evolve",\n'
    '        "site_drill",\n'
    '        "site_build",\n'
    '        "site_deploy",\n'
    '        "site_inquire",\n'
    '        "translate_mongodb",\n'
    '        "translate_all_pages",\n'
    '        "generate_quote",\n'
    '        "send_email",\n'
    '    }'
)

# Build the replacement that loads from Redis with hardcoded fallback
new_forbidden = (
    '    FORBIDDEN_DIRECT: set = None  # Lazy-loaded from Redis or defaults\n'
    '\n'
    '    def _load_forbidden_actions(self):\n'
    '        """Load forbidden actions from Redis config, fallback to defaults"""\n'
    '        if self.FORBIDDEN_DIRECT is not None:\n'
    '            return\n'
    '        if self.redis:\n'
    '            try:\n'
    '                raw = self.redis.get("yaxiio:config:forbidden_actions")\n'
    '                if raw:\n'
    '                    self.FORBIDDEN_DIRECT = set(json.loads(raw))\n'
    '                    return\n'
    '            except Exception:\n'
    '                pass\n'
    '        # Hardcoded defaults (generic, not industry-specific)\n'
    '        self.FORBIDDEN_DIRECT = {\n'
    '            "site_audit", "site_fix", "site_evolve", "site_drill",\n'
    '            "site_build", "site_deploy",\n'
    '            "site_inquire",\n'
    '            "generate_quote", "send_email",\n'
    '            "translate_mongodb", "translate_all_pages",\n'
    '        }'
)

if old_forbidden in const:
    const = const.replace(old_forbidden, new_forbidden)
    changes += 1
    print("OK: constitution FORBIDDEN_DIRECT → Redis-configurable")

# Also add _load_forbidden_actions() call in review()
old_review = '    def review(self, action: str, payload: dict) -> Tuple[Verdict, str]:'
if old_review in const:
    # Find the line after the docstring
    idx = const.find(old_review)
    next_def = const.find("        self.total_checks += 1", idx)
    if next_def > 0:
        inject = "        self._load_forbidden_actions()\n"
        const = const[:next_def] + inject + const[next_def:]
        changes += 1
        print("OK: _load_forbidden_actions() called in review()")

with open(PATH_CONST, "w") as f:
    f.write(const)

# ═══ Fix 4: yaxiio.py spawn_neuron passes AGENT_CONFIG ═══
with open(PATH_YX) as f:
    yx = f.read()

# Find spawn_neuron method
idx_sn = yx.find("def spawn_neuron(")
if idx_sn > 0:
    # Find where env vars are set
    env_start = yx.find('"AGENT_NAME"', idx_sn)
    if env_start > 0:
        # Find the env dict end
        env_piece = yx[env_start:env_start+800]
        # Add AGENT_CONFIG after AGENT_SKILL
        if '"AGENT_SKILL"' in env_piece and '"AGENT_CONFIG"' not in env_piece:
            old_skill_line = '"AGENT_SKILL": skill,'
            new_skill_line = (
                '"AGENT_SKILL": skill,\n'
                '                "AGENT_CONFIG": f"/tmp/yaxiio-agent/{name}-{task_id}/agent.json",'
            )
            if old_skill_line in yx:
                yx = yx.replace(old_skill_line, new_skill_line)
                changes += 1
                print("OK: spawn_neuron passes AGENT_CONFIG to neuron")
            else:
                print("WARN: AGENT_SKILL line not found in spawn_neuron")
        else:
            print("WARN: AGENT_CONFIG already present or AGENT_SKILL not found")

with open(PATH_YX, "w") as f:
    f.write(yx)

print(f"\n{changes} fixes applied")
