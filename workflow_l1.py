"""
L1 Perception Handler — 感知层纯函数 + 委托封装
===============================================
从 workflow_engine._do_L1() 提取，零 self 依赖。

纯函数: analyze_intent_l1(payload) → l1_result
处理器: L1Handler.analyze(task_id, payload) → state dict

测试: tests/test_l1_handler.py
"""
import json
from mcp_bridge import call_layer
from workflow_engine import INTENT_TOOL_MAP


def analyze_intent_l1(payload: dict) -> dict:
    """
    L1 感知 — 纯函数。
    
    输入: payload (任务参数)
    输出: MCP L1 的意图分析结果
    
    无副作用，无 self 依赖，天然可测试。
    """
    l1_text = {k: v for k, v in payload.items() if not k.startswith("_")}
    return call_layer(1, "analyze_intent", text=json.dumps(l1_text, ensure_ascii=False))


def resolve_intent(l1_result: dict, payload: dict) -> dict:
    """
    解析 L1 结果，应用 INTENT_TOOL_MAP 覆盖。
    
    输入: l1_result (MCP 返回), payload (原始参数)
    输出: {"primary_intent": str, "confidence": float, "l1_result": dict, ...}
    
    纯函数，无副作用。
    """
    primary = l1_result.get("primary_intent", "general")
    confidence = l1_result.get("confidence", 0.5)
    action = payload.get("action", "")
    action_clean = action.replace("site_", "").replace("translate_", "")
    
    state = {"l1_result": l1_result}
    
    if action_clean in INTENT_TOOL_MAP:
        primary = action_clean
        confidence = 0.99
        state["l1_action_override"] = True
    elif action in INTENT_TOOL_MAP:
        primary = action
        confidence = 0.99
        state["l1_action_override"] = True
    
    state["primary_intent"] = primary
    state["confidence"] = confidence
    return state


class L1Handler:
    """
    L1 感知处理器 — 委托模式封装。
    
    WorkflowEngine 通过此类委托 L1 感知逻辑，
    而非内联在 _do_L1() 方法中。
    """
    
    def analyze(self, task_id: str, payload: dict) -> dict:
        """
        执行 L1 感知并返回状态。
        
        用法:
            handler = L1Handler()
            state = handler.analyze(task_id, payload)
        """
        print(f"[WF] {task_id} L1 感知...", flush=True)
        l1 = analyze_intent_l1(payload)
        return resolve_intent(l1, payload)
