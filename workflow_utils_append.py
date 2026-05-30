
def legacy_l5_score(engine, task_id, action, plan, l4, state, output_text, agent_name):
    """Legacy L5 scoring fallback — extracted from WorkflowEngine"""
    import json, time
    context = json.dumps({"action": action, "intent": state.get("primary_intent", ""),
                          "total_rounds": state.get("total_rounds", 1)}, ensure_ascii=False)
    try:
        from mcp_bridge import call_layer
        l5 = call_layer(5, "deep_score", task_id=task_id, action=action,
                       agent_name=agent_name, output=output_text[:3000], context=context)
        if l5.get("method") == "llm":
            result = {
                "overall": l5.get("overall", 5), "method": "llm_deep_score",
                "dimensions": {k: l5.get(k, 0) for k in
                               ["accuracy","completeness","professionalism","actionability","consistency"]},
                "key_issues": l5.get("key_issues", []), "suggestions": l5.get("suggestions", []),
                "verdict": l5.get("verdict", "pass"),
                "needs_review": l5.get("verdict") in ("retry", "reject"),
                "needs_evolution": l5.get("overall", 5) < 5,
            }
            engine.score_history.append({"task_id": task_id, "score": result["overall"], "ts": time.time()})
            return result
    except Exception:
        pass

    has_result = bool(output_text and len(output_text) > 50)
    subtask_count = len(l4.get("results", {}))
    completeness = 8 if has_result else (5 if subtask_count > 0 else 3)
    quality = min(9, 4 + len(output_text) // 500) if has_result else 3
    base = {
        "accuracy": 5 + (2 if subtask_count >= 3 else 0), "completeness": completeness,
        "professionalism": 6 + (1 if len(output_text) > 1000 else 0),
        "actionability": 6 + (2 if "```" in output_text or "1." in output_text else 0), "consistency": 7,
    }
    base_overall = round(sum(base.values()) / len(base))
    result = {"overall": base_overall, "method": "rule_fallback", "dimensions": base,
              "needs_review": base_overall < 7, "needs_evolution": base_overall < 5,
              "verdict": "pass" if base_overall >= 7 else ("retry" if base_overall >= 4 else "reject")}
    engine.score_history.append({"task_id": task_id, "score": base_overall, "ts": time.time()})
    return result
