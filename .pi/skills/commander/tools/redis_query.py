#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""Agent Tool: Query Redis page cache"""
import json, sys, os, argparse
import redis

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "$REDIS_PASSWORD")

def query(args):
    r = redis.Redis(protocol=2, host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)
    
    pattern = args.key or "page:*"
    limit = args.limit or 50
    
    results = []
    cursor = 0
    count = 0
    while count < limit:
        cursor, keys = r.scan(cursor=cursor, match=pattern, count=min(100, limit))
        for key in keys:
            if count >= limit:
                break
            key_type = r.type(key)
            entry = {"key": key, "type": key_type}
            try:
                if key_type == "string":
                    val = r.get(key)
                    entry["value"] = val[:200] if val else ""
                elif key_type == "hash":
                    entry["value"] = r.hgetall(key)
                elif key_type == "list":
                    entry["value"] = r.lrange(key, 0, 9)
            except:
                entry["value"] = "(error)"
            results.append(entry)
            count += 1
        if cursor == 0:
            break
    
    # Summary
    total = r.dbsize()
    page_count = sum(1 for k in r.scan_iter("page:*", count=1000))
    
    print(json.dumps({
        "total_keys": total,
        "page_keys_approx": page_count,
        "pattern": pattern,
        "results": results,
        "count": len(results)
    }, ensure_ascii=False, default=str))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", help="Key pattern")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()
    try:
        query(args)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
