#!/usr/bin/env python3
"""宪法审查 — 单元测试"""
from constitution import YaxiioConstitution, Verdict

def test_system_ops_allow():
    c = YaxiioConstitution()
    for op in ["session_end", "agent_export", "status"]:
        v, _ = c.review(op, {})
        assert v == Verdict.ALLOWED, f"{op} should be ALLOWED"

def test_forbidden_delegated():
    c = YaxiioConstitution()
    for op in ["site_audit", "site_deploy", "translate_mongodb"]:
        v, _ = c.review(op, {})
        assert v == Verdict.DELEGATED, f"{op} should be DELEGATED"

def test_dangerous_patterns():
    c = YaxiioConstitution()
    v, _ = c.review("unknown_action", {"cmd": "rm -rf /tmp/test"})
    assert v == Verdict.DEGRADED, "Dangerous pattern should be DEGRADED"

def test_default_delegated():
    c = YaxiioConstitution()
    v, _ = c.review("some_new_action", {})
    assert v == Verdict.DELEGATED, "Unknown should be DELEGATED"

def test_stats():
    c = YaxiioConstitution()
    c.review("status", {})
    c.review("site_audit", {})
    s = c.stats()
    assert s["total_checks"] == 2
    assert s["allowed"] == 1

if __name__ == "__main__":
    test_system_ops_allow()
    test_forbidden_delegated()
    test_dangerous_patterns()
    test_default_delegated()
    test_stats()
    print("✅ 5/5 constitution tests passed")
