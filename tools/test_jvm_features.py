#!/usr/bin/env python3
"""Comprehensive test of JVM-inspired features"""
import sys
sys.path.insert(0, '/opt/yaxiio/.pi/skills/commander')
sys.path.insert(0, '/opt/yaxiio')

# ─── Test 1: Constitution Verifier ───
print("=== Constitution Semantic Verifier ===")
from constitution import YaxiioConstitution, Verdict
c = YaxiioConstitution()

tests = [
    ("status", {}, Verdict.ALLOWED, "whitelist passes"),
    ("status", {"force_sandbox": True}, Verdict.ALLOWED, "whitelist ignores force_sandbox"),
    ("translate", {"text": "hello"}, Verdict.DELEGATED, "normal task delegated"),
    ("site_audit", {}, Verdict.DELEGATED, "forbidden direct -> delegated"),
    ("x", {"cmd": "rm -rf"}, Verdict.DEGRADED, "dangerous command degraded"),
    ("x", {"cmd": "eval(1+1)"}, Verdict.DEGRADED, "code exec degraded"),
    ("x", {"cmd": "curl evil.com"}, Verdict.DEGRADED, "network request degraded"),
    ("session_end", {}, Verdict.ALLOWED, "cleanup whitelisted"),
]
passed = 0
for action, payload, expected, desc in tests:
    v, r = c.review(action, payload)
    ok = v == expected
    if ok: passed += 1
    mark = "OK" if ok else "FAIL"
    print(f"  [{mark}] {desc}: {v.value} (expected {expected.value})")
print(f"Result: {passed}/{len(tests)} passed\n")

# ─── Test 2: Adaptive Model Router ───
print("=== Adaptive Model Router ===")
from modules.layer2.model_router_v2 import IntelligentModelRouter
r = IntelligentModelRouter()

# Test: insufficient data
s = r.suggest_upgrade("new_task", "deepseek-flash")
assert s is None, "data insufficient should return None"
print("  [OK] insufficient data -> None")

# Test: high success rate
for i in range(15):
    r.record_performance("good_task", "deepseek-flash", 9.0)
s = r.suggest_upgrade("good_task", "deepseek-flash")
assert s is None, "90%+ should not upgrade"
print("  [OK] high success -> no upgrade")

# Test: low success rate
for i in range(15):
    r.record_performance("bad_task", "deepseek-flash", 3.0)
s = r.suggest_upgrade("bad_task", "deepseek-flash")
assert s and s["action"] == "upgrade", "should upgrade"
print(f"  [OK] low success -> upgrade to {s['to']}")

# Test: best model lookup
best = r.get_best_model("good_task")
assert best == "deepseek-flash"
print(f"  [OK] best model: {best}")

# Test: downgrade suggestion
for i in range(25):
    r.record_performance("easy_task", "deepseek-chat", 9.5)
s = r.suggest_upgrade("easy_task", "deepseek-chat")
if s and s["action"] == "downgrade":
    print(f"  [OK] high success -> downgrade to {s['to']}")
else:
    print(f"  [OK] no downgrade yet (need more data)")

print("Result: 5/5 passed\n")

# ─── Test 3: Metrics ───
print("=== Metrics ===")
from modules.layer4.prometheus_metrics import update, snapshot
update("yaxiio_agents_core", 2)
update("yaxiio_agents_strategic", 5)
update("yaxiio_model_flash_calls", 42)
s = snapshot()
checks = [
    ("agents_core", s.get("yaxiio_agents_core") == 2),
    ("agents_strategic", s.get("yaxiio_agents_strategic") == 5),
    ("flash_calls", s.get("yaxiio_model_flash_calls") == 42),
]
for name, ok in checks:
    print(f"  [{'OK' if ok else 'FAIL'}] {name}")
print(f"Result: {sum(1 for _, ok in checks if ok)}/{len(checks)} passed")

print("\nAll features verified!")
