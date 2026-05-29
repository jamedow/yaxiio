#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""Yaxiio 五语内容审计 v2.0 — 精准对比 content 字段"""
import json, time, os, re
from collections import defaultdict
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://$MONGO_HOST:27017")
SOURCE = "zh"
TARGETS = ["en", "ru", "ar", "es"]
CN = re.compile(r'[\u4e00-\u9fff]')

def has_cn(s): return bool(CN.search(str(s)))

def audit():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)
    coll = client["example_db"]["page_content"]
    t0 = time.time()

    # 加载所有页面，按标准化 path 分组
    pages = defaultdict(dict)
    for doc in coll.find({"content": {"$exists": True, "$ne": {}}},
                         {"path": 1, "content": 1, "pageType": 1, "lang": 1}):
        p = doc["path"]
        for l in ["zh","en","ru","ar","es"]:
            if p.startswith(f"/{l}/"):
                p = "/" + p[len(f"/{l}/"):]; break
        lang = doc.get("lang", "unknown")
        pages[p][lang] = doc

    print(f"[审计] {sum(len(v) for v in pages.values())} 条有内容的记录, {len(pages)} 唯一路径")

    findings = {
        "mixed_lang": [],   # 非中文页含中文
        "truncated": [],    # 译文截断
        "missing_page": [], # 缺页面
        "field_mismatch": [], # 字段数不一致
        "empty_field": [],  # 空字段
        "stats": {"scanned": 0, "issues": 0}
    }

    for path, langs in pages.items():
        src = langs.get(SOURCE)
        if not src: continue
        src_content = src.get("content", {})
        if not src_content: continue

        # 取第一个 page_key 作为代表
        src_key = list(src_content.keys())[0]
        src_fields = src_content[src_key]
        if not isinstance(src_fields, dict): continue

        src_field_keys = set(src_fields.keys())
        findings["stats"]["scanned"] += 1

        for tlang in TARGETS:
            tdoc = langs.get(tlang)
            if not tdoc:
                findings["missing_page"].append({"path": path, "lang": tlang})
                findings["stats"]["issues"] += 1
                continue

            tcontent = tdoc.get("content", {})
            tfields = tcontent.get(src_key, {})
            if not isinstance(tfields, dict):
                findings["missing_page"].append({"path": path, "lang": tlang, "reason": "no content key"})
                findings["stats"]["issues"] += 1
                continue

            tkeys = set(tfields.keys())

            # 1. 字段数差异
            missing = src_field_keys - tkeys
            extra = tkeys - src_field_keys
            if missing:
                findings["field_mismatch"].append({
                    "path": path, "lang": tlang,
                    "missing": sorted(missing)[:5], "total_missing": len(missing)
                })
                findings["stats"]["issues"] += 1

            # 2. 逐字段检查
            for fk in src_field_keys & tkeys:
                sv = str(src_fields[fk]).strip()
                tv = str(tfields[fk]).strip()

                # 空字段
                if not tv:
                    findings["empty_field"].append({
                        "path": path, "lang": tlang, "field": fk
                    })
                    findings["stats"]["issues"] += 1
                    continue

                # 语言混杂
                if tlang != SOURCE and has_cn(tv):
                    findings["mixed_lang"].append({
                        "path": path, "lang": tlang, "field": fk,
                        "snippet": tv[:80]
                    })
                    findings["stats"]["issues"] += 1

                # 截断检测 (翻译后明显短于原文)
                if len(sv) > 30 and len(tv) < len(sv) * 0.3:
                    findings["truncated"].append({
                        "path": path, "lang": tlang, "field": fk,
                        "src_len": len(sv), "tgt_len": len(tv)
                    })
                    findings["stats"]["issues"] += 1

    # 生成报告
    ts = time.strftime("%Y%m%d-%H%M%S")
    rp = f"/app/.pi/blackboard/reports/multilang-audit-{ts}.md"
    os.makedirs(os.path.dirname(rp), exist_ok=True)
    s = findings["stats"]
    elapsed = time.time() - t0

    report = f"""# ExampleCorp 五语内容审计报告

> {time.strftime('%Y-%m-%d %H:%M:%S')} | 扫描 {s['scanned']} 页 | {elapsed:.1f}s

## 概览

| 类型 | 数量 |
|------|:--:|
| 语言混杂(中文残留) | {len(findings['mixed_lang'])} |
| 译文截断 | {len(findings['truncated'])} |
| 缺失页面 | {len(findings['missing_page'])} |
| 字段缺失 | {len(findings['field_mismatch'])} |
| 空字段 | {len(findings['empty_field'])} |
| **合计** | **{s['issues']}** |

---

## 1. 语言混杂 — 非中文页出现中文 ({len(findings['mixed_lang'])})

"""
    # 按语言分组
    by_lang = defaultdict(list)
    for f in findings["mixed_lang"]:
        by_lang[f["lang"]].append(f)
    for lang in TARGETS:
        items = by_lang[lang][:10]
        if items:
            report += f"\n### {lang}\n"
            for item in items:
                report += f"- `{item['path']}` [{item['field']}] → \"{item['snippet']}\"\n"

    report += f"\n## 2. 译文截断 ({len(findings['truncated'])})\n\n"
    for item in findings["truncated"][:20]:
        report += f"- `{item['path']}` [{item['lang']}] `{item['field']}` 原文{item['src_len']}字→译文{item['tgt_len']}字\n"

    report += f"\n## 3. 缺失页面 ({len(findings['missing_page'])})\n\n"
    for item in findings["missing_page"][:20]:
        report += f"- `{item['path']}` 缺 **{item['lang']}**\n"

    report += f"\n## 4. 字段缺失 ({len(findings['field_mismatch'])})\n\n"
    for item in findings["field_mismatch"][:20]:
        report += f"- `{item['path']}` [{item['lang']}] 缺 {item['total_missing']} 字段: {', '.join(item['missing'][:3])}\n"

    report += f"\n## 5. 空字段 ({len(findings['empty_field'])})\n\n"
    for item in findings["empty_field"][:20]:
        report += f"- `{item['path']}` [{item['lang']}] `{item['field']}`\n"

    with open(rp, "w") as f:
        f.write(report)

    print(f"[审计] 报告: {rp}")
    print(f"[审计] 混杂{len(findings['mixed_lang'])} 截断{len(findings['truncated'])} 缺页{len(findings['missing_page'])} 缺字段{len(findings['field_mismatch'])} 空{len(findings['empty_field'])}")
    print("DONE")
    return findings

if __name__ == "__main__":
    audit()
