"""数据存储 — JSONL文件 (AGPLv3兼容，无SSPLv1依赖)"""
import json, os
class MongoClient:
    def __init__(self): self._log = os.environ.get("LOG_DIR", "/opt/commander/logs")
    def insert_one(self, collection: str, doc: dict):
        os.makedirs(self._log, exist_ok=True)
        with open(f"{self._log}/{collection}.jsonl","a") as f: f.write(json.dumps(doc,ensure_ascii=False)+"\n")
    def find(self, collection: str, query: dict = None, limit: int = 100):
        path = f"{self._log}/{collection}.jsonl"
        if not os.path.exists(path): return []
        results = []
        with open(path) as f:
            for line in f:
                if len(results) >= limit: break
                results.append(json.loads(line))
        return results
