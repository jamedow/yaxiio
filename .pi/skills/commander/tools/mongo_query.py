#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""Agent Tool: Query SQLite data store (read-only). Replaces mongo_query.py."""
import json, sys, os, argparse, sqlite3

DB_PATH = os.environ.get("YAXIO_DB", "/opt/commander/data/yaxiio.db")


def query(args):
    """Query SQLite events table (collection field = 'page_content' or args.collection)."""
    collection = getattr(args, 'collection', 'page_content')
    conn = sqlite3.connect(DB_PATH)
    
    rows = conn.execute(
        "SELECT data FROM events WHERE collection=? ORDER BY id DESC LIMIT ?",
        (collection, args.limit)
    ).fetchall()
    
    docs = []
    for (raw,) in rows:
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        
        # Filter
        path = doc.get("path", "")
        lang = doc.get("lang", "")
        
        if args.path and args.path not in path:
            continue
        if args.industry and f"/industries/{args.industry}/" not in path:
            continue
        if args.lang and lang != args.lang:
            continue
        
        # Projection
        if args.fields:
            doc = {k: v for k, v in doc.items() if k in args.fields}
        else:
            doc = {k: v for k, v in doc.items() if k in ("path", "lang", "pageType", "modules")}
        
        docs.append(doc)
    
    conn.close()
    
    result = {
        "count": len(docs),
        "query": {"collection": collection, "limit": args.limit},
        "docs": docs,
    }
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SQLite data query")
    parser.add_argument("--path", help="Path pattern (substring match)")
    parser.add_argument("--industry", help="Industry name")
    parser.add_argument("--lang", help="Language code")
    parser.add_argument("--fields", help="Comma-separated field names")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--collection", default="page_content", help="Event collection name")
    args = parser.parse_args()
    query(args)
