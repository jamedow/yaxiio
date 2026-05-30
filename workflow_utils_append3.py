

def agent_skill_map(engine) -> dict:
    """Dynamic agent to skill mapping — extracted from WorkflowEngine"""
    import json
    if getattr(engine, "_cached_skill_map", None):
        return engine._cached_skill_map
    _map = {}
    try:
        if engine.commander and engine.commander.redis:
            agents = engine.commander.redis.smembers("agent:registry") or []
            for name in agents:
                card_raw = engine.commander.redis.get(f"agent:card:{name}")
                if card_raw:
                    card = json.loads(card_raw)
                    skills = card.get("skills", [])
                    _map[name] = skills[0] if skills else ""
    except Exception:
        pass
    _fallback = {
        "UI/UX设计师": "ui-ux-designer", "品牌策略师": "strategic-partner",
        "前端工程师": "infrastructure-engineer", "翻译官": "translate-engine",
        "审计官": "audit-engine", "售前经理": "product-search",
        "商务经理": "product-search", "通用Agent": "",
        "修复Agent": "backend-engineer", "系统医生": "system-doctor",
        "LM内容工程师": "lm-content-engineer",
    }
    for k, v in _fallback.items():
        if k not in _map: _map[k] = v
    engine._cached_skill_map = _map
    return _map


def do_l3_l4(engine, task_id, payload, plan, state):
    """L3 dispatch + L4 execution — extracted from WorkflowEngine._do_L3_L4"""
    import json, time
    if __import__('os').environ.get('MCP_LAYERS_ENABLED_L4', ''):
        return {"mcp_routed": True, "layer": "L4", "phase": "not_implemented"}
    
    agent_name = plan.get("agent")
    l3 = {"dispatched": False, "agent": agent_name, "neuron_spawned": False}
    l4 = {}
    
    if agent_name and engine.commander:
        agent_skill = engine._agent_skill_map().get(agent_name, "")
        thinking_override = payload.get("_thinking", None)
        spawned = engine.commander.spawn_neuron(agent_name, agent_skill, thinking=thinking_override, task_id=task_id)
        state["_last_thinking"] = thinking_override or "medium"
        l3["neuron_spawned"] = spawned
        time.sleep(1)
        
        agent_channel = f"lightingmetal:agent:{agent_name}"
        try:
            msg = {"type": "task", "taskId": task_id, "from": "workflow",
                   "to": agent_name, "replyTo": "lightingmetal:agent:commander",
                   "payload": {k: v for k, v in payload.items() if not k.startswith("_")}}
            count = engine.commander.redis.client.publish(
                agent_channel, json.dumps(msg, ensure_ascii=False, default=str))
            l3["dispatched"] = count > 0
            l3["subscribers"] = count
        except Exception as e:
            l3["error"] = str(e)[:100]
    state["l3_result"] = l3
    
    tool_name = plan.get("tool")
    if tool_name and engine.commander and hasattr(engine.commander, 'arsenal') and engine.commander.arsenal.has(tool_name):
        try:
            l4 = {"status": "success", "arsenal_tool": tool_name,
                  "result": engine.commander.arsenal.call(tool_name, task_id, payload)}
        except Exception as e:
            l4 = {"status": "error", "error": str(e)[:500]}
    elif l3.get("dispatched"):
        l4 = engine._wait_for_neuron_response(task_id, agent_name, timeout=120)
    else:
        l4 = {"status": "error", "error": "无法分发任务到 Agent"}
    return l4
