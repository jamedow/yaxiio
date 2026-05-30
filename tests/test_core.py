#!/usr/bin/env python3
"""
Yaxiio Core Tests — 测试基线
=============================
constitution.py: 四种裁决、白名单、危险模式
task_state_machine.py: API完整性
"""
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.pi', 'skills', 'commander'))
from constitution import YaxiioConstitution, Verdict

# ═══ Constitution Tests ═══

def test_constitution():
    print("=== Constitution Tests ===\n")
    c = YaxiioConstitution(redis_client=None)
    passed = 0; failed = 0
    
    def check(action, payload, expected, name):
        nonlocal passed, failed
        verdict, reason = c.review(action, payload)
        if verdict == expected:
            passed += 1; print(f"  ✅ {name}")
        else:
            failed += 1; print(f"  ❌ {name}: expected {expected.value}, got {verdict.value}")
    
    # Rule 1: Whitelist
    print("Rule 1: SYSTEM_OPS")
    check("status", {}, Verdict.ALLOWED, "status is whitelisted")
    check("session_end", {}, Verdict.ALLOWED, "session_end is whitelisted")
    check("agent_export", {}, Verdict.ALLOWED, "agent_export is whitelisted")
    
    # Rule 2: Forbidden direct
    print("\nRule 2: FORBIDDEN_DIRECT")
    check("site_audit", {"task":"audit"}, Verdict.DELEGATED, "site_audit delegated")
    check("generate_quote", {}, Verdict.DELEGATED, "generate_quote delegated")
    
    # Rule 3: Dangerous patterns
    print("\nRule 3: DANGEROUS_PATTERNS")
    check("x", {"cmd":"rm -rf /tmp"}, Verdict.DEGRADED, "rm -rf → sandbox")
    check("x", {"cmd":"docker exec"}, Verdict.DEGRADED, "docker exec → sandbox")
    check("x", {"cmd":"eval("}, Verdict.DEGRADED, "eval() → sandbox")
    
    # Rule 4: Default
    print("\nRule 4: Default")
    check("translate", {}, Verdict.DELEGATED, "default → delegated")
    check("custom", {}, Verdict.DELEGATED, "custom → delegated")
    
    # Stats
    stats = c.stats()
    violations = c.recent_violations()
    print(f"\nStats: {stats['total_checks']} checks, {stats['allowed']} allowed, {len(violations)} violations")
    
    total = passed + failed
    print(f"\n{'='*40}\nResults: {passed}/{total} passed")
    return failed == 0


# ═══ StateMachine Tests ═══

def test_state_machine():
    print("\n\n=== TaskStateMachine Tests ===\n")
    passed = 0; failed = 0
    
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.pi', 'skills', 'commander'))
    from task_state_machine import TaskStateMachine
    
    # Verify import
    passed += 1; print("  ✅ TaskStateMachine importable")
    
    # Verify methods
    for m in ['create','start_layer','complete_layer','subtask_start','subtask_done','get']:
        if hasattr(TaskStateMachine, m):
            passed += 1; print(f"  ✅ method: {m}")
        else:
            failed += 1; print(f"  ❌ missing: {m}")
    
    total = passed + failed
    print(f"\nResults: {passed}/{total} passed")
    return failed == 0


if __name__ == "__main__":
    ok1 = test_constitution()
    ok2 = test_state_machine()
    if ok1 and ok2:
        print("\n🎉 All core tests passed!")
    else:
        print("\n❌ Some tests failed")
        sys.exit(1)
