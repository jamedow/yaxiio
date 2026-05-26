"""分层记忆 — 工作记忆(Redis)+短期(SQLite)+长期(向量DB)"""
import json, time, sqlite3, os
from typing import Dict, List, Optional

class AgentMemory:
    """三层记忆架构"""
    def __init__(self, redis_client=None, sqlite_db="/opt/commander/data/memory.db", vector_store=None):
        self.redis = redis_client
        self.vector = vector_store
        self.db_path = sqlite_db
        os.makedirs(os.path.dirname(sqlite_db), exist_ok=True)
        conn = sqlite3.connect(sqlite_db)
        conn.execute("CREATE TABLE IF NOT EXISTS memory (agent_id TEXT, key TEXT, value TEXT, created_at REAL, ttl REAL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_agent ON memory(agent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_ttl ON memory(ttl)")
        conn.commit(); conn.close()

    # ── 工作记忆 (Redis, TTL 5分钟) ──
    def working_set(self, agent_id: str, key: str, value: str):
        if self.redis: self.redis.setex(f"mem:work:{agent_id}:{key}", 300, value)

    def working_get(self, agent_id: str, key: str) -> Optional[str]:
        return self.redis.get(f"mem:work:{agent_id}:{key}") if self.redis else None

    # ── 短期记忆 (SQLite, TTL 7天) ──
    def short_remember(self, agent_id: str, key: str, value: str, ttl: int = 604800):
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO memory VALUES (?,?,?,?,?)", (agent_id, key, json.dumps(value,ensure_ascii=False), time.time(), time.time()+ttl))
        conn.commit(); conn.close()

    def short_recall(self, agent_id: str, limit: int = 10) -> List[dict]:
        conn = sqlite3.connect(self.db_path); conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key,value,created_at FROM memory WHERE agent_id=? AND ttl>? ORDER BY created_at DESC LIMIT ?", (agent_id, time.time(), limit)).fetchall()
        conn.close()
        return [{"key":r["key"],"value":json.loads(r["value"]),"time":r["created_at"]} for r in rows]

    # ── 长期记忆 (向量DB) ──
    def long_remember(self, agent_id: str, text: str, meta: dict = None):
        if self.vector:
            self.vector.add(f"mem:long:{agent_id}:{int(time.time())}", text, meta or {})

    def long_recall(self, agent_id: str, query: str, top_k: int = 5) -> List[dict]:
        return self.vector.search(f"agent:{agent_id} {query}", top_k) if self.vector else []

    # ── 摘要压缩 ──
    def compress(self, agent_id: str, llm_client=None) -> str:
        """将短期记忆压缩为长期摘要"""
        memories = self.short_recall(agent_id, 20)
        if not memories or not llm_client:
            return ""
        texts = "\n".join(f"- {m['key']}: {str(m['value'])[:200]}" for m in memories)
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            summary = loop.run_until_complete(llm_client.chat(f"Summarize agent memories:\n{texts}\nOne paragraph summary."))
            loop.close()
            self.long_remember(agent_id, summary, {"type":"summary","count":len(memories)})
            return summary
        except: return ""

    def cleanup(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM memory WHERE ttl<?", (time.time(),))
        conn.commit(); conn.close()
