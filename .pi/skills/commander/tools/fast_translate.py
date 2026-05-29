#!/usr/bin/env python3
"""Fast batch: concurrent LLM calls with key pool"""
import sys, os, re, json, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymongo import MongoClient
from openai import OpenAI
import redis as _r

MONGO = os.environ.get("MONGO_URI", "mongodb://$MONGO_HOST:27017")
CN = re.compile(r'[\u4e00-\u9fff]')
def has_cn(s): return bool(CN.search(str(s)))

r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password="$REDIS_PASSWORD", decode_responses=True)
key = r.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
llm = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1", max_retries=1, timeout=30)
LANG = {"en":"English","ru":"Russian","ar":"Arabic","es":"Spanish"}

client = MongoClient(MONGO)
coll = client["example_db"]["page_content"]

# Find top 400 pages with most Chinese
scored = []
for doc in coll.find({"lang":{"$ne":"zh"},"content":{"$exists":True}},{"path":1,"lang":1,"content":1}):
    cn_count = 0
    for pk, fields in doc.get("content",{}).items():
        if isinstance(fields, dict):
            cn_count += sum(1 for fv in fields.values() if has_cn(str(fv)) and len(str(fv))<300)
    if cn_count > 0:
        scored.append((cn_count, doc))
scored.sort(key=lambda x: -x[0])
print("Pages with Chinese:", len(scored))

def fix_page(data):
    _, doc = data
    lang = doc.get("lang","")
    content = doc.get("content",{})
    tasks = []
    for pk, fields in content.items():
        if not isinstance(fields, dict): continue
        for fk, fv in fields.items():
            fv_str = str(fv).strip()
            if has_cn(fv_str) and len(fv_str) < 300:
                tasks.append((pk, fk, fv_str))
    
    if not tasks: return 0
    
    # Concurrent translate
    def translate_one(item):
        pk, fk, text = item
        try:
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role":"user","content":"Translate to "+LANG.get(lang,lang)+". Only output translation:\n"+text}],
                max_tokens=150, temperature=0.2
            )
            t = resp.choices[0].message.content.strip().strip('"').strip("'")
            if t and not has_cn(t[:30]):
                return (pk, fk, t)
        except: pass
        return None
    
    results = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(translate_one, t) for t in tasks[:20]]
        for f in as_completed(futures):
            r = f.result()
            if r: results.append(r)
    
    for pk, fk, t in results:
        if pk in content and isinstance(content[pk], dict):
            content[pk][fk] = t
    
    if results:
        coll.update_one({"_id":doc["_id"]},{"$set":{"content":content}})
    return len(results)

# Process in batches of 20 pages
total = 0
for batch_start in range(0, min(len(scored), 300), 20):
    batch = scored[batch_start:batch_start+20]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = [ex.submit(fix_page, d) for d in batch]
        for f in as_completed(futures):
            total += f.result()
    print("  %d fixed (%d/%d)" % (total, batch_start+20, min(len(scored), 300)), flush=True)

import subprocess
subprocess.run(["python3","/opt/commander/tools/content_sync.py","full"],capture_output=True,timeout=120)
subprocess.run(["python3","/opt/commander/tools/deploy_hook.py","verify","power"],capture_output=True,timeout=60)
print("Total fixed:", total)
