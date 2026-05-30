

def cleanup_task(engine, task_id, subtasks, final_score):
    """Post-task cleanup — extracted from WorkflowEngine._cleanup_task"""
    import os, redis as _r
    
    agents_used = set(s["agent"] for s in subtasks)
    action = engine._current_intent or "general"

    # Primary: ExperienceFlywheel
    try:
        flywheel = engine.flywheel
        flywheel.save_experience(
            task_id=task_id,
            task_description=str(engine._current_intent or ""),
            subtasks=subtasks,
            final_score=float(final_score),
            l5_signals={},
            agents_used=agents_used,
            intent=action,
        )
        print(f"[WF] {task_id} flywheel: {len(agents_used)} agents, score={final_score}", flush=True)
    except Exception as _e:
        print(f"[WF] {task_id} flywheel failed, fallback to l0", flush=True)
        try:
            _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                         password=os.environ.get("REDIS_PASSWORD", ""),
                         decode_responses=True)
            engine.l0._save_experience(task_id, subtasks, final_score, agents_used, _rd)
        except Exception:
            pass

    # Cleanup: destroy task memory
    try:
        _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                     password=os.environ.get("REDIS_PASSWORD", ""),
                     decode_responses=True)
        for agent in agents_used:
            _rd.delete(f"agent:{agent}:{task_id}:memory")
        engine.snapshot.cleanup(task_id)
    except Exception:
        pass


def save_experience(engine, task_id, subtasks, final_score, agents_used, r):
    """L0: Save structured experience for future retrieval — extracted from WorkflowEngine"""
    import json, time
    intent = engine._current_intent or "general"
    for agent in agents_used:
        if agent.startswith("_"): continue
        exp = {
            "task_id": task_id, "agent": agent, "intent": intent,
            "score": final_score, "subtask_count": len(subtasks),
            "ts": time.time(), "success": final_score >= 7,
            "agents_involved": list(agents_used),
            "subtask_actions": [s.get("action", "")[:60] for s in subtasks[:5]],
        }
        key = f"exp:{intent}:{agent}"
        r.lpush(key, json.dumps(exp, ensure_ascii=False))
        r.ltrim(key, 0, 49)
    all_key = f"exp:{intent}:all"
    intent_exp = {"task_id": task_id, "score": final_score, "agents": list(agents_used),
                  "actions": [s.get("action","")[:60] for s in subtasks[:5]],
                  "ts": time.time(), "success": final_score >= 7}
    r.lpush(all_key, json.dumps(intent_exp, ensure_ascii=False))
    r.ltrim(all_key, 0, 49)
    print(f"[L0] saved experience: {intent} score={final_score} agents={list(agents_used)}", flush=True)
