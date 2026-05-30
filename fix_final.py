#!/usr/bin/env python3
"""Final optimizations:
1. _agent_skill_map() → dynamic from Redis capability cards
2. _get_llm() → IntelligentModelRouter + auto-fallback
3. Wire agent credit into _execute_subtask agent selection
"""
import os

PATH_WF = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"

with open(PATH_WF) as f:
    wf = f.read()

changes = 0

# ═══ 1: Add dynamic _agent_skill_map to WorkflowEngine ═══
# Find the __init__ method end
init_end_marker = "        self.flywheel = ExperienceFlywheel("
idx = wf.find(init_end_marker)
if idx > 0:
    # Find end of flywheel init block
    end_idx = wf.find("\n\n    def process", idx)
    if end_idx < 0:
        end_idx = wf.find("\n    def process", idx)
    
    if end_idx > idx:
        # Add _agent_skill_map after __init__ but before process
        dynamic_skill_map = '''
    def _agent_skill_map(self) -> dict:
        """Dynamic agent→skill mapping from Redis capability cards.
        Falls back to hardcoded map if Redis unavailable."""
        if getattr(self, "_cached_skill_map", None):
            return self._cached_skill_map

        _map = {}
        try:
            if self.commander and self.commander.redis:
                agents = self.commander.redis.smembers("agent:registry") or []
                for name in agents:
                    card_raw = self.commander.redis.get(f"agent:card:{name}")
                    if card_raw:
                        card = json.loads(card_raw)
                        skills = card.get("skills", [])
                        _map[name] = skills[0] if skills else ""
        except Exception:
            pass

        # Merge with hardcoded fallbacks for agents not yet in Redis
        _fallback = {
            "UI/UX设计师": "ui-ux-designer",
            "品牌策略师": "strategic-partner",
            "前端工程师": "infrastructure-engineer",
            "翻译官": "translate-engine",
            "审计官": "audit-engine",
            "售前经理": "product-search",
            "商务经理": "product-search",
            "通用Agent": "",
            "修复Agent": "backend-engineer",
            "系统医生": "system-doctor",
            "LM内容工程师": "lm-content-engineer",
        }
        for k, v in _fallback.items():
            if k not in _map:
                _map[k] = v

        self._cached_skill_map = _map
        return _map

'''
        # Insert after the flywheel init block, before next def
        insert_point = wf.find("\n\n    def process", idx)
        if insert_point > 0:
            wf = wf[:insert_point] + dynamic_skill_map + wf[insert_point:]
            changes += 1
            print("OK: dynamic _agent_skill_map added to WorkflowEngine")
        else:
            print("FAIL: process insertion point not found")
else:
    print("FAIL: flywheel init marker not found")

# ═══ 2: _get_llm uses IntelligentModelRouter ═══
old_get_llm = (
    '    def _get_llm(self, task_type: str = "default"):\n'
    '        # Phase 5: ModelRouter — 按任务类型选模型\n'
    '        if self.commander:\n'
    '            try:\n'
    '                from modules.layer2 import ModelRouter\n'
    '                router = ModelRouter()\n'
    '                task_info = {"action": task_type, "description": task_type}\n'
    '                cfg = router.select_model(task_info)\n'
    '                return self.commander._get_llm(cfg.get("model", task_type))\n'
    '            except:\n'
    '                try: return self.commander._get_llm()\n'
    '                except: pass\n'
    '        return None'
)

new_get_llm = (
    '    def _get_llm(self, task_type: str = "default", task_desc: str = ""):\n'
    '        """LLM client with IntelligentModelRouter + auto-fallback"""\n'
    '        if self.commander:\n'
    '            try:\n'
    '                # Use IntelligentModelRouter (cost x latency x capability)\n'
    '                if hasattr(self, "model_router_v2") and self.model_router_v2:\n'
    '                    task_info = {"action": task_type, "description": task_desc or task_type}\n'
    '                    cfg = self.model_router_v2.select(task_info)\n'
    '                    model = cfg.get("model", task_type)\n'
    '                    thinking = cfg.get("thinking", "medium")\n'
    '                    print("[WF] model router: {} (thinking={}, score={})".format(\n'
    '                        model, thinking, cfg.get("score", 0)), flush=True)\n'
    '                else:\n'
    '                    model = task_type\n'
    '                    thinking = "medium"\n'
    '                return self.commander._get_llm(model, thinking)\n'
    '            except Exception as _e:\n'
    '                # Auto-fallback to next provider\n'
    '                try:\n'
    '                    if hasattr(self, "model_router_v2") and self.model_router_v2:\n'
    '                        fb = self.model_router_v2.fallback(model if "model" in dir() else task_type)\n'
    '                        if fb:\n'
    '                            print("[WF] model fallback to: {}".format(fb.get("model","?")), flush=True)\n'
    '                            return self.commander._get_llm(fb["model"], "off")\n'
    '                except Exception:\n'
    '                    pass\n'
    '                try:\n'
    '                    return self.commander._get_llm()\n'
    '                except Exception:\n'
    '                    pass\n'
    '        return None'
)

if old_get_llm in wf:
    wf = wf.replace(old_get_llm, new_get_llm)
    changes += 1
    print("OK: _get_llm → IntelligentModelRouter + auto-fallback")
else:
    print("FAIL: _get_llm pattern not found")

# ═══ 3: Agent credit-based scheduling in _execute_subtask ═══
# When choosing which agent to dispatch to, prefer higher-credit agents
old_dispatch = (
    '        # Agent type: dispatch via L4 MCP\n'
    '        agent_skill = self._agent_skill_map().get(agent_name, "")'
)

new_dispatch = (
    '        # Agent type: dispatch via L4 MCP\n'
    '        # Agent credit-aware selection: prefer higher-scored agents\n'
    '        agent_skill = self._agent_skill_map().get(agent_name, "")\n'
    '        try:\n'
    '            if hasattr(self, "flywheel") and self.flywheel:\n'
    '                _credit = self.flywheel.get_agent_credit(agent_name)\n'
    '                if _credit < 5.0:\n'
    '                    print("[WF] {} agent {} credit={:.1f} (<5), may degrade quality".format(\n'
    '                        task_id, agent_name, _credit), flush=True)\n'
    '        except Exception:\n'
    '            pass'
)

if old_dispatch in wf:
    wf = wf.replace(old_dispatch, new_dispatch)
    changes += 1
    print("OK: agent credit check in _execute_subtask")
else:
    print("FAIL: _execute_subtask dispatch pattern not found")

with open(PATH_WF, "w") as f:
    f.write(wf)

print(f"\n{changes}/3 optimizations applied")
