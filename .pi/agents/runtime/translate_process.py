#!/usr/bin/env python3
"""翻译 Process 页面 MongoDB 内容 中文→en/ru/ar/es/fr"""
import json, os, sys, time
from pymongo import MongoClient
import requests

API_KEY = "sk-4cd70b4d104f404e977ca9b33d93d8b1"
API_URL = "https://api.deepseek.com/v1/chat/completions"
MONGO_URI = "mongodb://172.17.0.1:27017"
DB_NAME = "lightingmetal"
COLLECTION = "page_content"

TARGET_LANGS = ["en", "ru", "ar", "es", "fr"]
PROCESS_PAGES = ["cnc-machining", "casting", "forging", "mim-parts"]

# 不需要翻译的字段（URL、颜色、技术参数等）
SKIP_PATTERNS = [
    "Link", "Url", "link", "url", "path", "Path", "href", "Href",
    "img", "Img", "icon", "Icon", "Icon", "Image", "image",
    "processIcon", "processCategory", "processPagePath", "step",
    "parentPath", "relatedExclude", "processBadge",
    "compCurrentProcess", "h", "col", "Col",
    "cmp1ValA", "cmp1ValB", "cmp1ValC", "cmp2ValA", "cmp2ValB", "cmp2ValC",
    "cmp3ValA", "cmp3ValB", "cmp3ValC", "cmp4ValA", "cmp4ValB", "cmp4ValC",
    "cmp5ValA", "cmp5ValB", "cmp5ValC", "cmp6ValA", "cmp6ValB", "cmp6ValC",
    "compCncBatch", "compMimBatch", "compStampingBatch",
    "compCncMaterial", "compMimMaterial", "compStampingMaterial",
    "compCncShape", "compMimShape", "compStampingShape",
    "compCncMold", "compMimMold", "compStampingMold",
    "compCncUnitCost", "compMimUnitCost", "compStampingUnitCost",
    "compCncLeadTime", "compMimLeadTime", "compStampingLeadTime",
    "equip1Qty", "equip2Qty", "equip3Qty", "equip4Qty", "equip5Qty",
    "equip6Qty", "equip7Qty", "equip8Qty",
    "eq1Qty", "eq2Qty", "eq3Qty", "eq4Qty", "eq5Qty",
    "cap1Value", "cap2Value", "cap3Value", "cap4Value", "cap5Value", "cap6Value",
    "param1Value", "param2Value", "param3Value", "param4Value",
    "param5Value", "param6Value", "param7Value", "param8Value",
    "heroStat1", "heroStat2", "heroStat3",
    "eq1Spec", "eq2Spec", "eq3Spec", "eq4Spec", "eq5Spec",
    "equip1Cap", "equip2Cap", "equip3Cap", "equip4Cap",
    "equip5Cap", "equip6Cap", "equip7Cap", "equip8Cap",
    "cap1Icon", "cap2Icon", "cap3Icon", "cap4Icon", "cap5Icon", "cap6Icon", "cap7Icon", "cap8Icon",
    "heroStat1Label", "heroStat2Label", "heroStat3Label",
    "rel1Icon", "rel2Icon", "rel3Icon", "rel4Icon",
    "ind1Icon", "ind2Icon", "ind3Icon", "ind4Icon", "ind5Icon",
    "qc1Name", "qc2Name", "qc3Name", "qc4Name", "qc5Name",
    "qcItem1Name", "qcItem2Name", "qcItem3Name", "qcItem4Name", "qcItem5Name",
    "qcItem6Name", "qcItem7Name",
    "equip1Device", "equip2Device", "equip3Device", "equip4Device",
    "equip5Device", "equip6Device", "equip7Device", "equip8Device",
    "eq1Name", "eq2Name", "eq3Name", "eq4Name", "eq5Name",
    "eq1Use", "eq2Use", "eq3Use", "eq4Use", "eq5Use",
    "eq1Material", "eq2Material", "eq3Material", "eq4Material", "eq5Material",
    "cmp1Dim", "cmp2Dim", "cmp3Dim", "cmp4Dim", "cmp5Dim", "cmp6Dim",
    "compRow0", "compRow1", "compRow2", "compRow3", "compRow4", "compRow5", "compRow6",
    "cmH1", "cmH2", "cmH3", "cmH4",
    "caseCol1", "caseCol2", "caseCol3",
    "equipCol1", "equipCol2", "equipCol3",
    "breadcrumbHome", "breadcrumbCapability",
]

def should_translate(key):
    for pattern in SKIP_PATTERNS:
        if pattern in key or key in SKIP_PATTERNS:
            return False
    # Skip empty values
    return True

def translate_batch(fields_dict, source_lang, target_lang):
    """Translate a batch of fields using DeepSeek"""
    # Build prompt with all fields
    field_text = ""
    field_keys = list(fields_dict.keys())
    for key in field_keys:
        val = fields_dict[key]
        if val and val.strip():
            field_text += f'"{key}": "{val}"\n'

    if not field_text.strip():
        return fields_dict

    prompt = f"""Translate each field value from Chinese to {target_lang} (ISO 639-1: {target_lang}).
Rules:
1. Keep exact same structure - one line per field with "key": "translated_value"
2. Preserve all numbers, symbols, units (±0.005mm, 200+, ISO 9001, etc.)
3. Keep brand names unchanged (ZEISS, Hexagon, Mitutoyo, SGS, TUV, Intertek)
4. Keep product/industry category names in English
5. Translate descriptions naturally, not word-for-word
6. Output ONLY the translated lines, no explanations

{field_text}"""

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 8000
    }

    try:
        r = requests.post(API_URL, json=data, headers=headers, timeout=60)
        r.raise_for_status()
        result = r.json()
        response_text = result["choices"][0]["message"]["content"]

        # Parse response back into dict
        result_dict = {}
        for line in response_text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # Extract key: value
            if '":' in line or '": ' in line:
                try:
                    # Find the key between first " and ":
                    start = line.index('"') + 1
                    mid = line.index('":', start)
                    key = line[start:mid].strip()
                    # Value after ":
                    val_start = line.index('"', mid + 2) + 1
                    val_end = line.rindex('"')
                    val = line[val_start:val_end]
                    if key in fields_dict:
                        result_dict[key] = val
                except (ValueError, IndexError):
                    continue

        return result_dict
    except Exception as e:
        print(f"  [ERR] translate_batch({target_lang}): {e}", file=sys.stderr)
        return fields_dict


def main():
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    print("MongoDB connected")

    db = client[DB_NAME]
    col = db[COLLECTION]

    for page_slug in PROCESS_PAGES:
        print(f"\n{'='*60}")
        print(f"Page: {page_slug}")
        print(f"{'='*60}")

        # Get Chinese source
        zh_doc = col.find_one({"path": {"$regex": page_slug}, "lang": "zh", "pageType": "process"})
        if not zh_doc:
            print(f"  No Chinese doc found for {page_slug}")
            continue

        zh_content = zh_doc.get("content", {}).get(page_slug, {})
        if not zh_content:
            print(f"  No Chinese content for {page_slug}")
            continue

        print(f"  Chinese fields: {len(zh_content)}")

        # Filter fields that need translation
        fields_to_translate = {}
        skipped = 0
        for key, val in zh_content.items():
            if not should_translate(key):
                skipped += 1
                continue
            if isinstance(val, str) and val.strip() and any('\u4e00' <= c <= '\u9fff' for c in val):
                fields_to_translate[key] = val

        print(f"  Need translation: {len(fields_to_translate)} fields (skipped {skipped} non-translatable)")

        if not fields_to_translate:
            continue

        # Translate to each target language
        for target_lang in TARGET_LANGS:
            # Get target doc
            target_doc = col.find_one({"path": {"$regex": page_slug}, "lang": target_lang, "pageType": "process"})
            if not target_doc:
                print(f"  [{target_lang}] No doc found, skipping")
                continue

            # Check existing translation coverage
            target_content = target_doc.get("content", {}).get(page_slug, {})
            existing_cn = sum(1 for v in target_content.values() if isinstance(v, str) and any('\u4e00' <= c <= '\u9fff' for c in v))
            print(f"  [{target_lang}] Existing Chinese chars: {existing_cn}/{len(target_content)} fields")

            # Translate in batches of 40 fields
            field_items = list(fields_to_translate.items())
            translated = {}
            batch_size = 40

            for i in range(0, len(field_items), batch_size):
                batch = dict(field_items[i:i+batch_size])
                result = translate_batch(batch, "zh", target_lang)
                translated.update(result)
                time.sleep(0.5)  # Rate limit
                if (i + batch_size) % 80 == 0:
                    print(f"    Progress: {min(i+batch_size, len(field_items))}/{len(field_items)}")

            print(f"    Translated: {len(translated)} fields")

            # Merge translations into target document
            if translated:
                target_content.update(translated)
                col.update_one(
                    {"_id": target_doc["_id"]},
                    {"$set": {f"content.{page_slug}": target_content, "updatedAt": __import__("datetime").datetime.utcnow()}}
                )
                print(f"    [{target_lang}] Saved to MongoDB ({len(translated)} translated)")

    client.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
