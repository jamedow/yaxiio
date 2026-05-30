#!/usr/bin/env python3
"""Submit L4 page audit task to Yaxiio Commander"""
import json, time, os
import redis

r = redis.Redis(
    protocol=2,
    host="127.0.0.1", port=6379,
    password=os.environ.get("REDIS_PASSWORD", ""),
    decode_responses=True
)

task_id = f"l4-audit-{int(time.time())}"
task = {
    "type": "task",
    "taskId": task_id,
    "from": "pi-operator",
    "to": "commander",
    "replyTo": "lightingmetal:agent:commander",
    "payload": {
        "action": "site_audit",
        "task": (
            "审计 LightingMetal 外贸网站的 L4 页面完备度。"
            "根据 pages-tree.md 对比 i18n 目录找出缺失的 L4 页面。"
        ),
        "target": "power",
        "codebase": "/opt/lightingMetal/customer-portal"
    }
}

r.publish("lightingmetal:agent:commander", json.dumps(task, ensure_ascii=False))
print(f"OK: {task_id}")
