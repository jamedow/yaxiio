#!/usr/bin/env python3
"""
Yaxiio 修复执行器 — 将 Agent 产出的修复方案转化为实际操作
==========================================================
输入: Agent 产出的 JSON 修复计划
输出: 实际 MongoDB UPDATE + Redis SET 操作

修复指令格式:
{
  "fixes": [
    {"action":"mongo_set","path":"/zh/industries/power/solar-farm","field":"heroTitle","value":"新值"},
    {"action":"redis_set","key":"page:industries:power:solar-farm:solar-farm.heroTitle:zh","value":"新值"},
    {"action":"redis_sync_industry","industry":"power"},
    {"action":"deploy_hook"},
    {"action":"verify","url":"https://www.lightingmetal.com/zh/...","keyword":"地面光伏"}
  ]
}
"""

import sys, json, subprocess, os

MONGO = "mongodb://172.17.0.1:27017"
REDIS_HOST = os.environ.get("REDIS_HOST", "47.79.20.2")

def mongo_set(path, field, value):
    from pymongo import MongoClient
    m = MongoClient(MONGO)
    coll = m['lightingmetal']['page_content']
    doc = coll.find_one({'path': path})
    if not doc: return {"error": "not found", "path": path}
    content = doc.get('content', {})
    pk = list(content.keys())[0]
    if pk not in content: content[pk] = {}
    content[pk][field] = value
    coll.update_one({'_id': doc['_id']}, {'$set': {'content': content}})
    return {"ok": True, "path": path, "field": field}

def redis_set(key, value):
    import redis as _r
    r = _r.Redis(host=REDIS_HOST, port=6379, decode_responses=True)
    r.setex(key, 86400*7, value)
    return {"ok": True, "key": key}

def redis_sync_industry(industry):
    subprocess.run(["python3", "/opt/commander/tools/content_sync.py", "industry", industry],
                   capture_output=True, timeout=60)
    return {"ok": True, "industry": industry}

def deploy_hook():
    subprocess.run(["python3", "/opt/commander/tools/deploy_hook.py", "verify", "power"],
                   capture_output=True, timeout=60)
    return {"ok": True}

def verify(url, keyword):
    r = subprocess.run(["curl", "-sL", "-m", "10", url], capture_output=True, text=True)
    return {"ok": keyword in r.stdout, "url": url}

ACTIONS = {
    "mongo_set": lambda a: mongo_set(a["path"], a["field"], a["value"]),
    "redis_set": lambda a: redis_set(a["key"], a["value"]),
    "redis_sync_industry": lambda a: redis_sync_industry(a["industry"]),
    "deploy_hook": lambda a: deploy_hook(),
    "verify": lambda a: verify(a["url"], a["keyword"]),
}

def execute(plan_json: str) -> dict:
    """执行修复计划"""
    plan = json.loads(plan_json) if isinstance(plan_json, str) else plan_json
    fixes = plan.get("fixes", [])
    results = []
    for i, fix in enumerate(fixes):
        action = fix.get("action", "")
        if action in ACTIONS:
            try:
                r = ACTIONS[action](fix)
                results.append({"step": i+1, "action": action, "result": r})
            except Exception as e:
                results.append({"step": i+1, "action": action, "error": str(e)[:200]})
        else:
            results.append({"step": i+1, "action": action, "error": "unknown action"})
    ok = sum(1 for r in results if r.get("result", {}).get("ok", True))
    return {"total": len(results), "ok": ok, "results": results}

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            plan = json.load(f)
    else:
        plan = json.load(sys.stdin)
    print(json.dumps(execute(plan), indent=2, ensure_ascii=False))
