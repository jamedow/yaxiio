#!/usr/bin/env python3
"""Integrate foolproof into constitution.py"""
import sys; sys.path.insert(0,'/opt/yaxiio')

path = "/opt/yaxiio/.pi/skills/commander/constitution.py"
with open(path) as f:
    content = f.read()

changes = 0

# 1. Add foolproof import
old = "import json, time, os"
new = (
    "import json, time, os\n"
    "from modules.shared.foolproof import assess_risk, friendly_error"
)
if old in content and "foolproof" not in content:
    content = content.replace(old, new)
    changes += 1
    print("OK: foolproof import")

# 2. Add risk assessment + friendly messages to review()
old_review = '''        # ── 规则1: 系统白名单 ──
        if action in self.SYSTEM_OPS:
            self.allowed_count += 1
            return Verdict.ALLOWED, f"系统白名单: {action}"

        # ── 规则2: 禁止直接执行 ──
        if action in self.FORBIDDEN_DIRECT:
            self.delegated_count += 1
            self._log_violation(action, "FORBIDDEN_DIRECT",
                               f"Commander 禁止直接执行 {action}，已路由到五层流水线")
            return Verdict.DELEGATED, f"业务操作 {action} 必须走 L1→L5 流水线"

        # ── 规则3: 高危模式检测 ──
        payload_str = json.dumps(payload, ensure_ascii=False)
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in payload_str:
                self.delegated_count += 1  # 降级也算走流水线
                self._log_violation(action, "DANGEROUS_PATTERN",
                                   f"检测到高危模式 '{pattern}'，已降级到 sandbox")
                return Verdict.DEGRADED, f"高危操作检测: {pattern}, 已强制 sandbox"

        # ── 规则4: 默认走流水线 ──
        self.delegated_count += 1
        return Verdict.DELEGATED, f"默认路由: {action} → L1→L5 流水线"'''

if old_review in content:
    new_review = '''        # ── 规则1: 系统白名单 ──
        if action in self.SYSTEM_OPS:
            self.allowed_count += 1
            return Verdict.ALLOWED, f"系统白名单: {action}"

        # ── 规则2: 禁止直接执行 ──
        if action in self.FORBIDDEN_DIRECT:
            self.delegated_count += 1
            self._log_violation(action, "FORBIDDEN_DIRECT",
                               f"Commander 禁止直接执行 {action}，已路由到五层流水线")
            return Verdict.DELEGATED, \
                friendly_error("任务提交",
                    f"'{action}' 是业务操作，不能直接执行。",
                    "已自动路由到 L1→L5 流水线，系统将自动拆解、调度、执行和评估。")

        # ── 规则3: 高危模式检测 ──
        payload_str = json.dumps(payload, ensure_ascii=False)
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in payload_str:
                self.delegated_count += 1
                risk = assess_risk(action, {"pattern": pattern})
                self._log_violation(action, "DANGEROUS_PATTERN",
                                   f"检测到高危模式 '{pattern}'，风险等级={risk['level']}，已降级到 sandbox")
                return Verdict.DEGRADED, \
                    friendly_error("安全检查",
                        f"payload 中包含高危模式 '{pattern}'。",
                        f"已强制在沙箱中执行。如需直接执行，请确认风险等级: {risk['level']}。")

        # ── 规则4: 默认走流水线 ──
        self.delegated_count += 1
        return Verdict.DELEGATED, f"默认路由: {action} → L1→L5 流水线"'''
    content = content.replace(old_review, new_review)
    changes += 1
    print("OK: review enhanced")
else:
    print("FAIL: review block")

# 3. Update REJECTED handler in handle_task (in yaxiio.py)
# Actually that's in yaxiio.py, not constitution.py

with open(path, "w") as f:
    f.write(content)
print(f"{changes}/2 applied")
