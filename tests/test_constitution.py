"""宪法系统单元测试"""
import sys, os
sys.path.insert(0, "/opt/yaxiio/.pi/skills/commander")
from constitution import YaxiioConstitution, Verdict

class TestConstitution:
    def __init__(self):
        self.c = YaxiioConstitution(redis_client=None)
        self.passed = 0
        self.failed = 0

    def assert_verdict(self, name, action, payload, expected_verdict):
        verdict, reason = self.c.review(action, payload)
        if verdict == expected_verdict:
            self.passed += 1
            print(f"  OK  {name}: {verdict.value}")
        else:
            self.failed += 1
            print(f"  FAIL {name}: expected {expected_verdict.value}, got {verdict.value}")

    def run(self):
        print("=== Constitution Tests ===\n")
        self.assert_verdict("session_end whitelist", "session_end", {}, Verdict.ALLOWED)
        self.assert_verdict("agent_export whitelist", "agent_export", {}, Verdict.ALLOWED)
        self.assert_verdict("status whitelist", "status", {}, Verdict.ALLOWED)
        self.assert_verdict("site_audit delegated", "site_audit", {}, Verdict.DELEGATED)
        self.assert_verdict("generate_quote delegated", "generate_quote", {}, Verdict.DELEGATED)
        self.assert_verdict("rm -rf degraded", "cleanup", {"cmd": "rm -rf /tmp/*"}, Verdict.DEGRADED)
        self.assert_verdict("ssh degraded", "deploy", {"cmd": "ssh root@server"}, Verdict.DEGRADED)
        self.assert_verdict("eval degraded", "execute", {"code": "eval(user_input)"}, Verdict.DEGRADED)
        self.assert_verdict("unknown delegated", "unknown_action", {}, Verdict.DELEGATED)

        stats = self.c.stats()
        print(f"\n  Stats: total={stats['total_checks']} allowed={stats['allowed']} delegated={stats['delegated']} violations={stats['violations']}")
        
        expected_rate = (stats['allowed'] + stats['delegated']) / max(1, stats['total_checks'])
        actual_rate = stats['compliance_rate']
        if abs(expected_rate - actual_rate) < 0.001:
            self.passed += 1
            print(f"  OK  compliance_rate fix verified: {actual_rate:.0%}")
        else:
            self.failed += 1
            print(f"  FAIL compliance_rate: expected {expected_rate:.0%}, got {actual_rate:.0%}")

        print(f"\n{'='*40}")
        print(f"Results: {self.passed} passed, {self.failed} failed")
        return self.failed == 0

if __name__ == "__main__":
    ok = TestConstitution().run()
    sys.exit(0 if ok else 1)
