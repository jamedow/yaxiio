
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Zero-Token Router + Workflow Snapshots v3.0
============================================
吸收自 Conductor — 混合路由 + 工作流快照

B10: 零Token高频路由
  - 高频任务 (翻译、简单查询) → 规则路由，零Token
  - 低频/复杂任务 (询盘处理、方案生成) → LLM 路由
  - 混合路由模式，降低运营成本

B11: 工作流快照
  - Commander 拆解任务时，拆解结果存为 JSON 快照
  - 快照存入 MongoDB workflow_snapshots
  - 支持版本对比、回滚、审计
"""

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# B10 配置
HIGH_FREQ_PATTERNS = {
    "translate": ["翻译", "translate", "翻訳", "перевод", "ترجمة"],
    "simple_query": ["what is", "什么是", "查询", "search", "find", "list"],
    "status_check": ["status", "状态", "health", "健康"],
}

LLM_THRESHOLD_TOKEN = int(os.environ.get("LLM_THRESHOLD_TOKEN", "500"))
ROUTING_STATS_TTL = int(os.environ.get("ROUTING_STATS_TTL", "3600"))


class ZeroTokenRouter:
    """混合路由器 — 高频任务零Token，复杂任务走LLM。"""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._stats = {"rule_hits": 0, "llm_hits": 0, "total": 0}

    def route(self, task: str) -> dict:
        """分析任务并返回路由决策。

        Returns:
            {"method": "rule"|"llm", "agent": "...", "token_cost": 0, "confidence": 0.95}
        """
        self._stats["total"] += 1
        task_lower = task.lower()

        # 高频模式匹配 → 零Token
        for category, patterns in HIGH_FREQ_PATTERNS.items():
            for pattern in patterns:
                if pattern in task_lower:
                    agent = self._map_category_to_agent(category)
                    self._stats["rule_hits"] += 1
                    return {
                        "method": "rule",
                        "agent": agent,
                        "token_cost": 0,
                        "confidence": 0.95,
                        "category": category,
                        "matched_pattern": pattern,
                    }

        # 复杂任务 → LLM 路由
        self._stats["llm_hits"] += 1
        return {
            "method": "llm",
            "agent": None,  # 需要 LLM 决定
            "token_cost": LLM_THRESHOLD_TOKEN,
            "confidence": 0.7,
            "category": "complex",
        }

    def _map_category_to_agent(self, category: str) -> str:
        mapping = {
            "translate": "翻译官",
            "simple_query": "商务经理",
            "status_check": "Commander",
        }
        return mapping.get(category, "通用Agent")

    def should_use_llm(self, task: str) -> bool:
        """判断任务是否需要 LLM。"""
        return self.route(task)["method"] == "llm"

    def get_stats(self) -> dict:
        total = max(self._stats["total"], 1)
        return {
            "rule_hits": self._stats["rule_hits"],
            "llm_hits": self._stats["llm_hits"],
            "total": self._stats["total"],
            "rule_pct": round(self._stats["rule_hits"] / total * 100, 1),
            "tokens_saved": self._stats["rule_hits"] * LLM_THRESHOLD_TOKEN,
        }


class WorkflowSnapshotManager:
    """工作流快照管理 — 版本对比、回滚、审计。"""

    def __init__(self, mongo_db=None):
        self.mongo = mongo_db
        self._ensure_indexes()

    def _ensure_indexes(self):
        if self.mongo is None:
            return
        try:
            self.mongo["workflow_snapshots"].create_index(
                [("task_type", 1), ("timestamp", -1)]
            )
        except Exception:
            pass

    def snapshot(self, task_description: str, plan: dict,
                  task_type: str = "general") -> str:
        """创建任务拆解快照。"""
        snapshot_id = f"ws-{uuid.uuid4().hex[:8]}"
        doc = {
            "snapshot_id": snapshot_id,
            "task_description": task_description[:500],
            "task_type": task_type,
            "plan": plan,
            "subtask_count": len(plan.get("subtasks", [])),
            "timestamp": datetime.now(),
            "version": self._get_next_version(task_type),
        }

        if self.mongo is not None:
            try:
                self.mongo["workflow_snapshots"].insert_one(doc)
            except Exception:
                pass

        return snapshot_id

    def _get_next_version(self, task_type: str) -> int:
        if self.mongo is None:
            return 1
        try:
            last = self.mongo["workflow_snapshots"].find_one(
                {"task_type": task_type},
                sort=[("timestamp", -1)]
            )
            return (last.get("version", 0) + 1) if last else 1
        except Exception:
            return 1

    def get_latest(self, task_type: str) -> Optional[dict]:
        """获取最新快照。"""
        if self.mongo is None:
            return None
        try:
            doc = self.mongo["workflow_snapshots"].find_one(
                {"task_type": task_type},
                sort=[("timestamp", -1)],
                projection={"_id": 0}
            )
            return doc
        except Exception:
            return None

    def list_versions(self, task_type: str, limit: int = 10) -> List[dict]:
        """列出版本历史。"""
        if self.mongo is None:
            return []
        try:
            return list(self.mongo["workflow_snapshots"].find(
                {"task_type": task_type},
                projection={"_id": 0, "snapshot_id": 1, "version": 1,
                            "subtask_count": 1, "timestamp": 1}
            ).sort("timestamp", -1).limit(limit))
        except Exception:
            return []

    def compare(self, task_type: str, v1: int, v2: int) -> dict:
        """对比两个版本的快照。"""
        if self.mongo is None:
            return {}

        try:
            snap1 = self.mongo["workflow_snapshots"].find_one(
                {"task_type": task_type, "version": v1}
            )
            snap2 = self.mongo["workflow_snapshots"].find_one(
                {"task_type": task_type, "version": v2}
            )

            if not snap1 or not snap2:
                return {"error": "version_not_found"}

            return {
                "v1": {"version": v1, "subtasks": snap1.get("subtask_count")},
                "v2": {"version": v2, "subtasks": snap2.get("subtask_count")},
                "diff_subtasks": (snap2.get("subtask_count", 0) -
                                   snap1.get("subtask_count", 0)),
            }
        except Exception:
            return {}

    def rollback(self, task_type: str, target_version: int) -> Optional[dict]:
        """回滚到指定版本的快照。"""
        if self.mongo is None:
            return None
        try:
            doc = self.mongo["workflow_snapshots"].find_one(
                {"task_type": task_type, "version": target_version}
            )
            return doc["plan"] if doc else None
        except Exception:
            return None

    def get_stats(self) -> dict:
        if self.mongo is None:
            return {"total_snapshots": 0}
        try:
            total = self.mongo["workflow_snapshots"].count_documents({})
            types = self.mongo["workflow_snapshots"].distinct("task_type")
            return {"total_snapshots": total, "task_types": len(types)}
        except Exception:
            return {"total_snapshots": 0}
