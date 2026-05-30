"""
Workflow Utilities — 纯工具函数
================================
从 workflow_engine 提取的辅助方法，零或极少 self 依赖。

_summarize_results(): 格式化子任务结果
_build_plan(): INTENT_TOOL_MAP 查找
_check_and_heal(): 故障检测 + 医生派遣
"""
import json


def summarize_results(task_id: str, subtasks: list, results: dict) -> str:
    """汇总所有子任务结果 — 纯函数"""
    lines = [f"## 子任务执行汇总 ({task_id})"]
    for st in subtasks:
        sid = st["id"]
        r = results.get(sid, {})
        status = "✅" if r.get("ok") else "❌"
        output = str(r.get("output", r.get("error", "")))[:200]
        lines.append(f"- {status} **{st['action']}** ({st['agent']}): {output}")
    return "\n".join(lines)


def build_plan(primary_intent: str, action: str, payload: dict, 
               arsenal_tools: list, intent_map: dict) -> dict:
    """构建执行计划 — 纯函数。依赖 INTENT_TOOL_MAP 通过参数注入。"""
    if primary_intent in intent_map:
        plan = dict(intent_map[primary_intent])
        plan["match_type"] = "exact"
        plan["intent"] = primary_intent
        return plan
    
    for ik, ti in intent_map.items():
        if ik in primary_intent or primary_intent in ik:
            plan = dict(ti)
            plan["match_type"] = "fuzzy"
            plan["intent"] = primary_intent
            return plan
    
    if action in (arsenal_tools or []):
        return {"tool": action, "agent": "审计官", "desc": f"tool:{action}",
                "match_type": "direct", "intent": primary_intent}
    
    return {"tool": None, "agent": "审计官", "desc": f"通用:{action}",
            "match_type": "fallback", "intent": primary_intent, "command": f"echo 'task'"}


def check_and_heal(task_id: str, subtasks: list, results: dict, commander) -> dict:
    """故障检测 + 自动修复 — 依赖 commander 通过参数注入。
    
    Returns: 受影响 Agent 的故障统计 dict
    """
    agent_failures = {}
    for st in subtasks:
        sid = st["id"]
        r = results.get(sid, {})
        if not r.get("ok"):
            agent = st.get("agent", "unknown")
            agent_failures[agent] = agent_failures.get(agent, 0) + 1
    
    for agent, count in agent_failures.items():
        if count >= 2 and commander:
            failure_type = "low_quality"
            for st in subtasks:
                if st.get("agent") == agent and results.get(st["id"], {}).get("error") == "timeout":
                    failure_type = "slow_response"
                    break
            
            print(f"[WF] 🏥 {agent} 连续失败 {count} 次, 派系统医生 (type={failure_type})", flush=True)
            commander.handle_agent_failure(
                agent, failure_type, task_id,
                details=f"连续{count}个子任务失败"
            )
    
    return agent_failures
        return self._legacy_l5_score(task_id, action, plan, l4, state, output_text, agent_name)
    
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
