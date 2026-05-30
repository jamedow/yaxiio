#!/usr/bin/env python3
"""Wire ExperienceFlywheel into workflow_engine._cleanup_task + update exports"""

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path, "r") as f:
    content = f.read()

changes = 0

# ── Replace _cleanup_task to use ExperienceFlywheel ──
old_cleanup = (
    "    def _cleanup_task(self, task_id: str, subtasks: list, final_score: int):\n"
    '        """Post-task cleanup: save L0 experience, merge template, destroy memory"""\n'
    "        agents_used = set(s[\"agent\"] for s in subtasks)"
)

if old_cleanup in content:
    # Find the full method
    idx = content.find(old_cleanup)
    # Find next method
    next_method = content.find("\n    def _save_experience", idx)
    if next_method < 0:
        next_method = content.find("\n    def _summarize", idx)
    if next_method < 0:
        next_method = content.find("\n    def _llm_decompose", idx)
    if next_method < 0:
        next_method = content.find("\n    def _do_L1", idx)

    if next_method > idx:
        old_method = content[idx:next_method]
        new_method = (
            '    def _cleanup_task(self, task_id: str, subtasks: list, final_score: int):\n'
            '        """Post-task cleanup: ExperienceFlywheel + destroy memory"""\n'
            '        agents_used = set(s["agent"] for s in subtasks)\n'
            '        action = self._current_intent or "general"\n'
            '\n'
            '        # ── Primary: ExperienceFlywheel ──\n'
            '        try:\n'
            '            from modules.layer5.experience_flywheel import ExperienceFlywheel\n'
            '            from modules.layer1.vector_store_chroma import ChromaVectorStore\n'
            '            _vs = ChromaVectorStore()\n'
            '            flywheel = ExperienceFlywheel(\n'
            '                redis_client=self.commander.redis,\n'
            '                vector_store=_vs\n'
            '            )\n'
            '            flywheel.save_experience(\n'
            '                task_id=task_id,\n'
            '                task_description=str(self._current_intent or ""),\n'
            '                subtasks=subtasks,\n'
            '                final_score=float(final_score),\n'
            '                l5_signals={},\n'
            '                agents_used=agents_used,\n'
            '                intent=action,\n'
            '            )\n'
            '            print(f"[WF] {task_id} flywheel: {len(agents_used)} agents, score={final_score}", flush=True)\n'
            '        except Exception as _e:\n'
            '            print(f"[WF] {task_id} flywheel failed ({_e}), fallback to l0", flush=True)\n'
            '            # Fallback to legacy L0 storage\n'
            '            try:\n'
            '                import redis as _r\n'
            '                _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,\n'
            '                             password=os.environ.get("REDIS_PASSWORD", ""),\n'
            '                             decode_responses=True)\n'
            '                self.l0._save_experience(task_id, subtasks, final_score, agents_used, _rd)\n'
            '            except Exception:\n'
            '                pass\n'
            '\n'
            '        # ── Cleanup: destroy task memory ──\n'
            '        try:\n'
            '            import redis as _r\n'
            '            _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,\n'
            '                         password=os.environ.get("REDIS_PASSWORD", ""),\n'
            '                         decode_responses=True)\n'
            '            for agent in agents_used:\n'
            '                _rd.delete(f"agent:{agent}:{task_id}:memory")\n'
            '            # Cleanup workflow snapshot\n'
            '            self.snapshot.cleanup(task_id)\n'
            '        except Exception:\n'
            '            pass\n'
        )
        content = content.replace(old_method, new_method)
        changes += 1
        print(f"OK: _cleanup_task → ExperienceFlywheel (old: {len(old_method)} chars)")
    else:
        print("FAIL: _cleanup_task end not found")
else:
    print("FAIL: _cleanup_task start not found")

with open(path, "w") as f:
    f.write(content)

print(f"\n{changes}/1 changes applied")
