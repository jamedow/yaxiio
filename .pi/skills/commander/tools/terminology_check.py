#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""Agent Tool: Check terminology consistency against standard dictionary"""
import json, sys, os, re, argparse
from collections import defaultdict
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://$MONGO_HOST:27017")

# Standard terminology dictionary (zh → en)
STD_TERMS = {
    "螺旋地桩": "ground screw",
    "太阳能光伏支架": "solar PV mounting structure",
    "光伏支架": "PV mounting bracket",
    "地桩": "ground pile",
    "破碎机": "crusher",
    "振动筛": "vibrating screen",
    "输送机": "conveyor",
    "颚式破碎机": "jaw crusher",
    "圆锥破碎机": "cone crusher",
    "反击式破碎机": "impact crusher",
    "球磨机": "ball mill",
    "浮选机": "flotation machine",
    "磁选机": "magnetic separator",
    "旋流器": "hydrocyclone",
    "浓缩机": "thickener",
    "过滤机": "filter press",
    "挖掘机": "excavator",
    "装载机": "loader",
    "推土机": "bulldozer",
    "平地机": "grader",
    "压路机": "roller",
    "摊铺机": "paver",
    "铣刨机": "milling machine",
    "混凝土搅拌站": "concrete batching plant",
    "沥青搅拌站": "asphalt mixing plant",
    "稳定土拌合站": "stabilized soil mixing plant",
    "破碎筛分": "crushing and screening",
    "制砂机": "sand making machine",
    "洗砂机": "sand washing machine",
    "给料机": "feeder",
    "仓储": "warehousing",
    "物流": "logistics",
    "钢结构": "steel structure",
    "护栏": "guardrail",
    "围栏": "fencing",
    "格栅": "grating",
    "盖板": "cover plate",
    "井盖": "manhole cover",
    "路灯": "street light",
    "交通标志": "traffic sign",
    "声屏障": "sound barrier",
    "隔音墙": "noise barrier wall",
    "桥墩": "bridge pier",
    "桥台": "bridge abutment",
    "支座": "bearing",
    "伸缩缝": "expansion joint",
    "排水管": "drainage pipe",
    "供水管": "water supply pipe",
    "燃气管": "gas pipe",
    "热力管": "heat pipe",
    "电缆桥架": "cable tray",
    "配电柜": "distribution cabinet",
    "变压器": "transformer",
    "开关柜": "switchgear",
    "环网柜": "ring main unit",
    "箱变": "compact substation",
    "光伏组件": "PV module",
    "逆变器": "inverter",
    "汇流箱": "combiner box",
    "储能电池": "energy storage battery",
    "PCS": "PCS",
    "BMS": "BMS",
    "EMS": "EMS",
    "风力发电机": "wind turbine",
    "塔筒": "tower",
    "叶片": "blade",
    "机舱": "nacelle",
    "轮毂": "hub",
    "齿轮箱": "gearbox",
}

def check_terms(args):
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    coll = client["example_db"]["page_content"]
    
    filt = {"content": {"$exists": True, "$ne": {}}}
    if args.industry:
        filt["path"] = {"$regex": f"/industries/{args.industry}/"}
    
    lang_filter = args.lang or "en"
    docs = list(coll.find(filt, {"path": 1, "lang": 1, "content": 1}).limit(200))
    
    inconsistencies = []
    term_usage = defaultdict(lambda: defaultdict(int))  # term → {correct_count, incorrect_count}
    
    for doc in docs:
        if doc.get("lang") != lang_filter:
            continue
        path = doc["path"]
        content = doc.get("content", {})
        
        for pk, fields in content.items():
            if not isinstance(fields, dict):
                continue
            for fk, fv in fields.items():
                text = str(fv)
                for zh_term, std_en in STD_TERMS.items():
                    if zh_term in text or (lang_filter == "en" and any(variant in text.lower() for variant in [std_en.lower(), std_en.lower().replace(" ", "-"), std_en.lower().replace(" ", "")])):
                        term_usage[zh_term]["checked"] += 1
                        # Check if the standard English term appears
                        if lang_filter == "en" and std_en.lower() not in text.lower():
                            # Look for alternative translations
                            alt_match = re.search(r'[A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+){0,3}', text)
                            actual = alt_match.group(0) if alt_match else "(unknown)"
                            if actual.lower() != std_en.lower():
                                inconsistencies.append({
                                    "path": path,
                                    "field": fk,
                                    "zh_term": zh_term,
                                    "expected": std_en,
                                    "found": actual,
                                    "snippet": text[:100]
                                })
                                term_usage[zh_term]["incorrect"] += 1
    
    result = {
        "total_checked": sum(v.get("checked", 0) for v in term_usage.values()),
        "inconsistencies": len(inconsistencies),
        "details": inconsistencies[:50],
        "term_stats": {k: {"checked": v.get("checked",0), "incorrect": v.get("incorrect",0)} 
                       for k, v in sorted(term_usage.items()) if v.get("checked", 0) > 0}
    }
    
    print(json.dumps(result, ensure_ascii=False, default=str))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--industry", help="Industry name filter")
    parser.add_argument("--lang", default="en", help="Language to check")
    args = parser.parse_args()
    try:
        check_terms(args)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
