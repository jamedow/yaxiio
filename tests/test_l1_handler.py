#!/usr/bin/env python3
"""L1 Handler 纯函数测试"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from workflow_l1 import analyze_intent_l1, resolve_intent, L1Handler

def test_resolve_intent():
    """测试意图解析逻辑"""
    print("=== resolve_intent ===\n")
    passed = 0; failed = 0
    
    def check(name, condition):
        nonlocal passed, failed
        if condition: passed += 1
        else: failed += 1; print(f"  ❌ {name}")
    
    # 1. MCP 返回了 intent
    l1 = {"primary_intent": "translate", "confidence": 0.9}
    state = resolve_intent(l1, {"action": "unknown"})
    check("MCP intent preserved", state["primary_intent"] == "translate")
    check("MCP confidence preserved", state["confidence"] == 0.9)
    
    # 2. INTENT_TOOL_MAP 覆盖
    state = resolve_intent(l1, {"action": "site_audit"})
    check("site_audit → audit override", state["primary_intent"] == "audit")
    check("override confidence", state["confidence"] == 0.99)
    check("override flag set", state.get("l1_action_override") == True)
    
    # 3. 默认值
    l1_empty = {}
    state = resolve_intent(l1_empty, {"action": "unknown"})
    check("default intent", state["primary_intent"] == "general")
    check("default confidence", state["confidence"] == 0.5)
    
    # 4. l1_result 保留
    check("l1_result preserved", state["l1_result"] == l1_empty)
    
    total = passed + failed
    print(f"\n  {passed}/{total} passed")
    return failed == 0


def test_l1_handler():
    """测试 L1Handler 类"""
    print("\n=== L1Handler ===\n")
    passed = 0; failed = 0
    
    def check(name, condition):
        nonlocal passed, failed
        if condition: passed += 1
        else: failed += 1; print(f"  ❌ {name}")
    
    handler = L1Handler()
    check("handler instantiated", handler is not None)
    check("has analyze method", hasattr(handler, 'analyze'))
    
    total = passed + failed
    print(f"\n  {passed}/{total} passed")
    return failed == 0


def test_pure_functions():
    """验证纯函数无副作用"""
    print("\n=== Pure Function ===\n")
    passed = 0; failed = 0
    
    def check(name, condition):
        nonlocal passed, failed
        if condition: passed += 1
        else: failed += 1; print(f"  ❌ {name}")
    
    # resolve_intent 不应修改输入
    l1_input = {"primary_intent": "test", "confidence": 0.8}
    l1_copy = dict(l1_input)
    payload = {"action": "test_action"}
    
    resolve_intent(l1_input, payload)
    check("input not mutated", l1_input == l1_copy)
    
    total = passed + failed
    print(f"\n  {passed}/{total} passed")
    return failed == 0


if __name__ == "__main__":
    ok1 = test_resolve_intent()
    ok2 = test_l1_handler()
    ok3 = test_pure_functions()
    
    if ok1 and ok2 and ok3:
        print("\n🎉 All L1 handler tests passed!")
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
