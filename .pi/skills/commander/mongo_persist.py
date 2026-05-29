
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
MongoDB Persistence — 统一数据持久化层 v3.3
=============================================
所有 Commander 进化产物统一存储到 MongoDB，容器无状态。

集合:
  skills            — 自动生成的 Skill 文件
  experience        — 经验模式 patterns
  evolution         — 进化引擎产出 (GEPA)
  blackboard        — 审计报告/任务记录
  config            — 进化后的配置快照
  agent_optimization_log — Agent 优化日志 (已有)
  audit_logs        — 审计日志 (已有)
  workflow_snapshots — 工作流快照 (已有)

使用:
  from mongo_persist import MongoPersistence
  persist = MongoPersistence()
  persist.save_skill("agent-1", "translate", {...})
  skills = persist.list_skills()
"""

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import pymongo
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False


class MongoPersistence:
    """MongoDB 统一持久化。无文件依赖，容器完全无状态。"""

    def __init__(self, store_path: str = None, db_name: str = None):
        import os
        self.uri = store_path or os.environ.get("STORE_PATH", "/opt/commander/data/yaxiio.db")
        self.db_name = db_name or os.environ.get("MONGO_DATABASE", "example_db")
        self._db = None
        self._connect()

    def _connect(self):
        if not HAS_MONGO:
            return
        try:
            client = pymongo.FakeMongoDB(self.uri, serverSelectionTimeoutMS=3000)
            client.server_info()
            self._db = client[self.db_name]
            # 确保集合和索引存在
            self._db["skills"].create_index("name", unique=True, sparse=True)
            self._db["experience"].create_index("version")
            self._db["evolution"].create_index("timestamp")
            self._db["config"].create_index("key", unique=True, sparse=True)
        except Exception as e:
            print(f"[MongoPersistence] 连接失败: {e}")

    @property
    def available(self) -> bool:
        return self._db is not None

    # ── Skills ─────────────────────────────────────────

    def save_skill(self, name: str, agent_id: str, task: str,
                   content: str, score: int = 0) -> dict:
        """保存 Skill 到 MongoDB。"""
        if not self.available:
            return {"status": "mongo_unavailable"}

        doc = {
            "name": name,
            "agent_id": agent_id,
            "task": task[:500],
            "content": content,
            "size": len(content),
            "score": score,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        try:
            self._db["skills"].update_one(
                {"name": name}, {"$set": doc}, upsert=True
            )
            return {"status": "saved", "name": name}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def get_skill(self, name: str) -> Optional[dict]:
        """获取单个 Skill。"""
        if not self.available:
            return None
        return self._db["skills"].find_one({"name": name}, {"_id": 0})

    def list_skills(self, limit: int = 100) -> List[dict]:
        """列出所有 Skills。"""
        if not self.available:
            return []
        return list(self._db["skills"].find(
            {}, {"_id": 0, "name": 1, "agent_id": 1, "score": 1, "size": 1, "created_at": 1}
        ).sort("created_at", -1).limit(limit))

    def count_skills(self) -> int:
        if not self.available:
            return 0
        return self._db["skills"].count_documents({})

    # ── Experience Patterns ────────────────────────────

    def save_experience(self, version: int, patterns: list,
                         capabilities: dict, modules: dict) -> dict:
        """保存经验模式。"""
        if not self.available:
            return {"status": "mongo_unavailable"}

        doc = {
            "version": version,
            "patterns": patterns,
            "capabilities": capabilities,
            "modules": modules,
            "timestamp": datetime.now(),
        }
        self._db["experience"].update_one(
            {"version": version}, {"$set": doc}, upsert=True
        )
        return {"status": "saved", "version": version}

    def get_latest_experience(self) -> Optional[dict]:
        if not self.available:
            return None
        return self._db["experience"].find_one(
            {}, {"_id": 0}, sort=[("version", -1)]
        )

    # ── Evolution ──────────────────────────────────────

    def save_evolution(self, evolution_type: str, data: dict) -> dict:
        """保存进化产物 (prompt优化/GEPA结果/A/B测试)。"""
        if not self.available:
            return {}

        doc = {
            "type": evolution_type,
            "data": data,
            "timestamp": datetime.now(),
        }
        self._db["evolution"].insert_one(doc)
        return {"id": str(doc.get("_id", "")), "type": evolution_type}

    def get_evolution_history(self, evolution_type: str = None,
                               limit: int = 50) -> List[dict]:
        if not self.available:
            return []
        query = {"type": evolution_type} if evolution_type else {}
        return list(self._db["evolution"].find(
            query, {"_id": 0}
        ).sort("timestamp", -1).limit(limit))

    # ── Config ─────────────────────────────────────────

    def save_config(self, key: str, value: Any) -> dict:
        """保存配置项（进化后的配置变更）。"""
        if not self.available:
            return {}
        self._db["config"].update_one(
            {"key": key},
            {"$set": {"key": key, "value": value, "updated_at": datetime.now()}},
            upsert=True,
        )
        return {"key": key, "saved": True}

    def get_config(self, key: str, default: Any = None) -> Any:
        if not self.available:
            return default
        doc = self._db["config"].find_one({"key": key})
        return doc["value"] if doc else default

    def get_all_config(self) -> dict:
        if not self.available:
            return {}
        docs = self._db["config"].find({}, {"_id": 0, "key": 1, "value": 1})
        return {d["key"]: d["value"] for d in docs}

    # ── 统计 ───────────────────────────────────────────

    def stats(self) -> dict:
        if not self.available:
            return {"status": "unavailable"}
        return {
            "skills": self.count_skills(),
            "experience_versions": self._db["experience"].count_documents({}),
            "evolution_records": self._db["evolution"].count_documents({}),
            "config_keys": self._db["config"].count_documents({}),
            "audit_logs": self._db["audit_logs"].count_documents({}) if "audit_logs" in self._db.list_collection_names() else 0,
            "status": "connected",
        }


# 全局单例
_persist_instance: Optional[MongoPersistence] = None

def get_persist() -> MongoPersistence:
    global _persist_instance
    if _persist_instance is None:
        _persist_instance = MongoPersistence()
    return _persist_instance
