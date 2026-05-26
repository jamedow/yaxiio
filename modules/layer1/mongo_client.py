"""数据存储 — SQLite (Public Domain, AGPLv3兼容)"""
import sqlite3, json, os, time, threading

DB_PATH = os.environ.get("YAXIIO_DB", "/opt/commander/data/yaxiio.db")

class MongoClient:  # 接口兼容，内部已切换SQLite
    _lock = threading.Lock()
    
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, collection TEXT, data TEXT, created_at REAL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_collection ON events(collection)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON events(created_at)")
            conn.commit()
            conn.close()

    def insert_one(self, collection: str, doc: dict):
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO events (collection, data, created_at) VALUES (?, ?, ?)",
                        (collection, json.dumps(doc, ensure_ascii=False), time.time()))
            conn.commit()
            conn.close()

    def find(self, collection: str, query: dict = None, limit: int = 100, offset: int = 0):
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT data FROM events WHERE collection=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              (collection, limit, offset)).fetchall()
            conn.close()
            return [json.loads(r["data"]) for r in rows]
    
    def count(self, collection: str) -> int:
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            c = conn.execute("SELECT COUNT(*) FROM events WHERE collection=?", (collection,)).fetchone()[0]
            conn.close()
            return c
    
    def delete_old(self, collection: str, days: int = 30):
        cutoff = time.time() - days * 86400
        with self._lock:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM events WHERE collection=? AND created_at<?", (collection, cutoff))
            conn.commit()
            conn.close()
