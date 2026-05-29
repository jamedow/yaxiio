#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
SQLiteStore — 统一的本地持久化存储
===================================
替代 MongoDB，使用 SQLite 作为 Yaxiio 的持久化层。

数据分层:
  🔥 Redis  — 热数据 (Agent 状态、心跳、任务队列、能力卡片)
  ❄️ SQLite — 冷数据 (任务归档、审计日志、数据快照、进化记录)

设计原则:
  1. 零外部依赖 — SQLite 是 Python 标准库
  2. 与 data_cleanup.py 共享同一数据库文件
  3. 兼容 AuditLogger 等现有组件接口
"""

import json
import os
import sqlite3
import time
from threading import Lock
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("YAXIIO_DB", "/opt/commander/data/yaxiio.db")


class SQLiteStore:
    """Yaxiio 持久化存储。

    用法:
      store = SQLiteStore()
      store.log_audit(task_id="task-001", event="task_completed", data={...})
      store.archive_task(tid="task-001", status="DONE", result={...})
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self._lock = Lock()
        self._init_schema()

    def _init_schema(self):
        """初始化表结构（幂等）。"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    event TEXT,
                    agent TEXT,
                    data TEXT,
                    created_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_archive (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE,
                    status TEXT,
                    action TEXT,
                    result TEXT,
                    l5_score REAL,
                    created_at REAL,
                    archived_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evolution_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id TEXT,
                    strategy TEXT,
                    decision TEXT,
                    rate_a REAL,
                    rate_b REAL,
                    created_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS data_snapshot (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_type TEXT,
                    key_count INTEGER,
                    data_json TEXT,
                    created_at REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collection TEXT,
                    data TEXT,
                    created_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_log(task_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_task ON task_archive(task_id)")
            conn.commit()
            conn.close()

    # ── 审计日志 ─────────────────────────────────────────

    def log_audit(self, task_id: str = "", event: str = "",
                  agent: str = "", data: dict = None):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO audit_log (task_id, event, agent, data, created_at) VALUES (?,?,?,?,?)",
                (task_id, event, agent, json.dumps(data or {}, ensure_ascii=False,
                                                   default=str), time.time())
            )
            conn.commit()
            conn.close()

    def query_audit(self, task_id: str = "", limit: int = 50) -> List[dict]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            if task_id:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE task_id=? ORDER BY created_at DESC LIMIT ?",
                    (task_id, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            conn.close()
            return [{
                "id": r[0], "task_id": r[1], "event": r[2],
                "agent": r[3], "data": json.loads(r[4]) if r[4] else {},
                "created_at": r[5],
            } for r in rows]

    # ── 任务归档 ─────────────────────────────────────────

    def archive_task(self, task_id: str, status: str = "", action: str = "",
                     result: dict = None, l5_score: float = 0):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR REPLACE INTO task_archive (task_id, status, action, result, l5_score, created_at, archived_at) VALUES (?,?,?,?,?,?,?)",
                (task_id, status, action,
                 json.dumps(result or {}, ensure_ascii=False, default=str)[:5000],
                 l5_score, time.time(), time.time())
            )
            conn.commit()
            conn.close()

    def get_archived_task(self, task_id: str) -> Optional[dict]:
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT * FROM task_archive WHERE task_id=?", (task_id,)
            ).fetchone()
            conn.close()
            if row:
                return {
                    "task_id": row[1], "status": row[2], "action": row[3],
                    "result": json.loads(row[4]) if row[4] else {},
                    "l5_score": row[5], "archived_at": row[7],
                }
            return None

    # ── 进化记录 ─────────────────────────────────────────

    def log_evolution(self, test_id: str, strategy: str, decision: str,
                      rate_a: float, rate_b: float):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO evolution_log (test_id, strategy, decision, rate_a, rate_b, created_at) VALUES (?,?,?,?,?,?)",
                (test_id, strategy, decision, rate_a, rate_b, time.time())
            )
            conn.commit()
            conn.close()

    # ── 通用事件 ─────────────────────────────────────────

    def log_event(self, collection: str, data: dict):
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO events (collection, data, created_at) VALUES (?,?,?)",
                (collection, json.dumps(data, ensure_ascii=False, default=str),
                 time.time())
            )
            conn.commit()
            conn.close()

    # ── 健康检查 ─────────────────────────────────────────

    def healthy(self) -> bool:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("SELECT 1")
            conn.close()
            return True
        except Exception:
            return False

    def stats(self) -> dict:
        try:
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                return {
                    "audit_log": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                    "task_archive": conn.execute("SELECT COUNT(*) FROM task_archive").fetchone()[0],
                    "evolution_log": conn.execute("SELECT COUNT(*) FROM evolution_log").fetchone()[0],
                    "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                    "db_size_kb": os.path.getsize(self.db_path) // 1024 if os.path.exists(self.db_path) else 0,
                }
        except Exception:
            return {}
        finally:
            try: conn.close()
            except: pass


# ═══════════════════════════════════════════════════════════════
# 兼容层: 模拟 MongoClient 接口
# ═══════════════════════════════════════════════════════════════

class FakeMongoCollection:
    """模拟 MongoDB collection 接口，底层存 SQLite events 表。"""
    def __init__(self, store: SQLiteStore, name: str):
        self._store = store
        self._name = name

    def insert_one(self, doc: dict):
        self._store.log_event(self._name, doc)

    def find(self, query: dict = None):
        return FakeCursor(self._store, self._name, query)

    def count_documents(self, query: dict = None) -> int:
        return 0  # 简化实现


class FakeCursor:
    def __init__(self, store, collection, query):
        self._items = []
        self._pos = 0

    def sort(self, *args, **kwargs):
        return self

    def skip(self, n):
        self._pos = n
        return self

    def limit(self, n):
        return self

    def __iter__(self):
        return iter([])  # events 表不支持复杂查询

    def __next__(self):
        raise StopIteration


class FakeMongoDB:
    """模拟 pymongo.MongoClient，实际指向 SQLite。"""
    def __init__(self, store: SQLiteStore):
        self._store = store

    def __getattr__(self, name):
        return FakeMongoCollection(self._store, name)

    def admin(self):
        return self

    def command(self, cmd):
        return {"ok": 1}
