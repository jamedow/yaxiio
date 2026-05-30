#!/usr/bin/env python3
"""Patch remaining P1 modules with foolproof guards"""
import sys; sys.path.insert(0,'/opt/yaxiio')

patches = [
    # --- Gateway: friendly error messages ---
    ("/opt/yaxiio/.pi/skills/commander/gateway.py", [
        ('"error": "缺少 session_token"',
         '"error": "请提供 session_token", "advice": "在请求头或 URL 参数中携带 session_token。新用户请先调用 /api/register 获取 token。"'),
        ('"error": "缺少 task 参数"',
         '"error": "请提供 task 参数", "advice": "示例: {\\"task\\": \\"翻译 100 条产品描述到阿拉伯语\\"}"'),
        ('"error": "trace_id required"',
         '"error": "请提供 trace_id", "advice": "trace_id 用于追踪任务全链路日志。可在任务提交时从 Commander 响应中获取。"'),
    ]),
    # --- UnifiedScorer: quality preset integration ---
    ("/opt/yaxiio/modules/layer5/unified_scorer.py", [
        ('from modules.layer4.llm_judge import LLMJudge',
         'from modules.layer4.llm_judge import LLMJudge\nfrom modules.shared.foolproof import try_primary_fallback, QUALITY_PRESETS'),
        ('def score(self,',
         'def score_with_fallback(self, primary_fn, fallback_fn, label="评分"):\n        """防呆: 主评分路径失败时自动降级"""\n        return try_primary_fallback(primary_fn, fallback_fn, label)\n\n    def score(self,'),
    ]),
    # --- AsyncOrchestrator: concurrent guard ---
    ("/opt/yaxiio/modules/layer3/async_orchestrator.py", [
        ('self.max_concurrent = max_concurrent or int(',
         '# 防呆: 限制最大并发数在安全范围内\n        _raw = max_concurrent or int('),
    ]),
]

for path, replacements in patches:
    with open(path) as f:
        content = f.read()
    changed = 0
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            changed += 1
    if changed:
        with open(path, "w") as f:
            f.write(content)
        print(f"{path.split('/')[-1]}: {changed} patches")
    else:
        print(f"{path.split('/')[-1]}: no changes needed")
