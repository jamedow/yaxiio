#!/usr/bin/env python3
"""雅溪审计 Agent — 独立进程，订阅PubSub，LLM驱动审计"""
import os, sys, json, time, asyncio

sys.path.insert(0, "/app/.pi/skills/commander")
os.environ["DEEPSEEK_API_KEY"] = "sk-a5be6b7a32e041bca1fe0a110288e488"

from agent_lifecycle_v2 import LLMAdapter

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")
CHANNEL = "lightingmetal:agent:commander"
NAME = "审计Agent"

import redis as rlib

def log(msg):
    print(f"[{NAME}] {time.strftime('%H:%M:%S')} {msg}", flush=True)

def run_audit(codebase="/app/lightingmetal/customer-portal"):
    llm = LLMAdapter(api_key="sk-a5be6b7a32e041bca1fe0a110288e488",
                     base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro")
    
    log(f"🔍 扫描 {codebase}")
    vue_files = []
    for root, dirs, files in os.walk(codebase):
        dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
        for fn in files:
            if fn.endswith(".vue"):
                fp = os.path.join(root, fn)
                with open(fp) as fh:
                    ct = fh.read()
                    ds = sum(1 for kw in ["card-zhongli","btn-zhongli","fangsheng","chain-divider","meander","vision","zhongli-navbar"] if kw in ct)
                    old = sum(1 for kw in ["bg-white","bg-gray","text-gray","shadow-lg"] if kw in ct)
                    th = sum(1 for kw in ["useIndustryTheme","texture-kunlun","texture-myth","texture-jingzhe","texture-ding","texture-canal"] if kw in ct)
                    vue_files.append({"f":fp.replace(codebase,""),"l":len(ct.split("\n")),"ds":ds,"old":old,"th":th})
    
    findings = []
    if llm.available:
        summary = "\n".join(f"{v['f']}: {v['l']}L ds={v['ds']} old={v['old']} theme={v['th']}" for v in sorted(vue_files,key=lambda x:-x["ds"])[:15])
        try:
            loop = asyncio.new_event_loop()
            report = loop.run_until_complete(llm.chat(
                f"Audit {len(vue_files)} Vue files:\n{summary}\nAnalyze design compliance, old UI residue, theme pushdown. Markdown Chinese 300 chars."
            ))
            loop.close()
            findings.append(report)
            log(f"💭 LLM: {report[:100]}...")
        except Exception as e:
            findings.append(f"LLM error: {e}")
    
    os.makedirs("/app/.pi/blackboard/reports", exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    rp = f"/app/.pi/blackboard/reports/audit-{ts}.md"
    rpt = f"# Yaxiio Audit\n> {ts} | {len(vue_files)} files\n\n| File | Lines | ds | old | theme |\n|------|-------|----|-----|-------|\n"
    for v in sorted(vue_files,key=lambda x:-x["ds"])[:20]:
        rpt += f"| {v['f']} | {v['l']} | {v['ds']} | {v['old']} | {v['th']} |\n"
    rpt += "\n## Analysis\n\n" + "\n".join(findings)
    with open(rp, "w") as f: f.write(rpt)
    log(f"📄 {rp}")
    return rp

def main():
    log("🟢 启动")
    while True:
        try:
            r = rlib.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)
            pubsub = r.pubsub()
            pubsub.subscribe(CHANNEL)
            log("📡 已订阅")
            
            for msg in pubsub.listen():
                if msg["type"] != "message": continue
                try:
                    data = json.loads(msg["data"])
                    if data.get("type") != "task": continue
                    payload = data.get("payload", {})
                    if payload.get("action") == "site_audit":
                        log(f"📋 任务: {data.get('taskId')}")
                        run_audit(payload.get("codebase", "/app/lightingmetal/customer-portal"))
                except Exception as e:
                    log(f"⚠️ {e}")
        except Exception as e:
            log(f"⚠️ 断开: {e}, 5s重连")
            time.sleep(5)

if __name__ == "__main__":
    main()
