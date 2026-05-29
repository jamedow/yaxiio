#!/usr/bin/env python3
"""Agent Tool: Batch translate mixed-language content and update MongoDB"""
import sys, os, re, json, time
from pymongo import MongoClient
from openai import OpenAI
import redis as _r

MONGO = os.environ.get("MONGO_URI", "mongodb://$MONGO_HOST:27017")
CN = re.compile(r'[\u4e00-\u9fff]')
def has_cn(s): return bool(CN.search(str(s)))

r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password="$REDIS_PASSWORD", decode_responses=True)
key = r.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
llm = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
LANG = {"en":"English","ru":"Russian","ar":"Arabic","es":"Spanish"}

def run(batch_num=1, total_batches=9, lang_filter=None):
    client = MongoClient(MONGO)
    coll = client["example_db"]["page_content"]
    
    # Find pages with Chinese
    docs = list(coll.find({"lang":{"$ne":"zh"},"content":{"$exists":True}},
                          {"path":1,"lang":1,"content":1}))
    
    # Filter and sort by CN count
    scored = []
    for doc in docs:
        if lang_filter and doc.get("lang") != lang_filter: continue
        cn_count = 0
        for pk, fields in doc.get("content",{}).items():
            if isinstance(fields, dict):
                cn_count += sum(1 for fv in fields.values() if has_cn(str(fv)) and len(str(fv))<300)
        if cn_count > 0:
            scored.append((cn_count, doc))
    
    scored.sort(key=lambda x: -x[0])
    
    batch_size = 30
    start = (batch_num - 1) * batch_size
    end = start + batch_size if batch_num < total_batches else len(scored)
    batch = scored[start:end]
    
    total_fixed = 0
    for _, doc in batch:
        lang = doc.get("lang","")
        content = doc.get("content",{})
        modified = False
        for pk, fields in content.items():
            if not isinstance(fields, dict): continue
            for fk, fv in list(fields.items()):
                fv_str = str(fv).strip()
                if not has_cn(fv_str) or len(fv_str) > 300: continue
                try:
                    resp = llm.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role":"user","content":"Translate to "+LANG.get(lang,lang)+". Only output translation:\n"+fv_str}],
                        max_tokens=150, temperature=0.2
                    )
                    t = resp.choices[0].message.content.strip().strip('"').strip("'")
                    if t and not has_cn(t[:30]):
                        content[pk][fk] = t
                        modified = True; total_fixed += 1
                except: pass
        if modified:
            coll.update_one({"_id":doc["_id"]},{"$set":{"content":content}})
    
    result = {"batch": "%d/%d"%(batch_num,total_batches), "pages_processed": len(batch),
              "fields_fixed": total_fixed, "status": "ok"}
    print(json.dumps(result, ensure_ascii=False))
    return result

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=1, help="Batch number (1-based)")
    p.add_argument("--total", type=int, default=9, help="Total batches")
    p.add_argument("--lang", help="Language filter (en/ru/ar/es)")
    args = p.parse_args()
    run(args.batch, args.total, args.lang)
