#!/usr/bin/env python3
"""Fix semantic check ordering: whitelist first, then semantics"""
path = '/opt/yaxiio/.pi/skills/commander/constitution.py'
with open(path) as f: c = f.read()

old = '        # ── 规则0: 语义校验（新增，必须在白名单之前）'
end_marker = '        # ── 规则2: 禁止直接执行 ──'
idx_s = c.find(old)
idx_e = c.find(end_marker, idx_s)
old_block = c[idx_s:idx_e]

new_block = '''        # ── 规则1: 系统白名单 ──
        if action in self.SYSTEM_OPS:
            self.allowed_count += 1
            return Verdict.ALLOWED, f"系统白名单: {action}"

        # ── 规则1.5: 语义校验（白名单之后，禁止执行之前）──
        semantic = self.verifier.verify(action, payload)
        if not semantic["passed"]:
            self.delegated_count += 1
            self._log_violation(action, "SEMANTIC_CHECK",
                               f"语义校验失败: {semantic['issues'][0][:100]}")
            return Verdict.DEGRADED, f"语义校验失败: {semantic['issues'][0]}"

'''

c = c.replace(old_block, new_block)
with open(path, 'w') as f: f.write(c)
compile(c, 'const', 'exec')
print('OK')
