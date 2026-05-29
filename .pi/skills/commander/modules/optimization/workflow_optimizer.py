
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Workflow Topology Optimizer v3.0 — 吸收自 EvoAgentX
=====================================================
Commander 自动发现更优的 Agent 协作工作流，对比新旧效率，自动固化更优拓扑。

流程:
  1. 记录当前工作流执行数据 (时间/成本/成功率)
  2. 评估层对比新旧拓扑效率
  3. Top-K 选择最优拓扑
  4. 自动固化到 Redis，下次任务直接使用

存储:
  Redis: workflow:topology:{task_type} → JSON 拓扑
  MongoDB: workflow_snapshots → 历史快照
"""

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple


class TopologyRecord:
    """单次工作流执行记录。"""

    def __init__(self, task_type: str, topology: dict, metrics: dict):
        self.id = uuid.uuid4().hex[:8]
        self.task_type = task_type
        self.topology = topology  # {"agents": [...], "edges": [...], "parallel": bool}
        self.metrics = metrics    # {"elapsed_ms": 5000, "token_cost": 1500, "success": True, "score": 8}
        self.timestamp = time.time()


class WorkflowTopologyOptimizer:
    """工作流拓扑优化器。"""

    def __init__(self, redis_client=None, mongo_db=None):
        self.redis = redis_client
        self.mongo = mongo_db
        self._records: List[TopologyRecord] = []
        self._topologies: Dict[str, List[TopologyRecord]] = {}  # task_type → records

    def record(self, task_type: str, topology: dict, metrics: dict) -> str:
        """记录一次工作流执行。"""
        record = TopologyRecord(task_type, topology, metrics)
        self._records.append(record)

        if task_type not in self._topologies:
            self._topologies[task_type] = []
        self._topologies[task_type].append(record)

        # 只保留最近 100 条
        if len(self._topologies[task_type]) > 100:
            self._topologies[task_type] = self._topologies[task_type][-100:]

        # 快照写入 MongoDB
        if self.mongo is not None:
            try:
                self.mongo["workflow_snapshots"].insert_one({
                    "snapshot_id": record.id,
                    "task_type": task_type,
                    "topology": topology,
                    "metrics": metrics,
                    "timestamp": record.timestamp,
                })
            except Exception:
                pass

        return record.id

    def evaluate(self, task_type: str, top_k: int = 3) -> dict:
        """评估并返回 Top-K 最优拓扑。

        评分公式: efficiency = 0.4 * (1/elapsed_ms_norm) + 0.3 * success_rate + 0.3 * avg_score
        """
        records = self._topologies.get(task_type, [])
        if not records:
            return {"task_type": task_type, "topologies": [], "recommendation": None}

        # 按拓扑分组计算平均指标
        grouped = self._group_by_topology(records)
        scored = []

        for topo_sig, group in grouped.items():
            successes = sum(1 for r in group if r.metrics.get("success", False))
            avg_score = sum(r.metrics.get("score", 5) for r in group) / len(group)
            avg_elapsed = sum(r.metrics.get("elapsed_ms", 1000) for r in group) / len(group)
            avg_tokens = sum(r.metrics.get("token_cost", 0) for r in group) / len(group)

            # 综合评分
            efficiency = (
                0.3 * (successes / len(group)) +
                0.3 * (avg_score / 10) +
                0.2 * (1 / max(avg_elapsed / 10000, 0.1)) +
                0.2 * (1 / max(avg_tokens / 2000, 0.1))
            )

            scored.append({
                "topology": group[0].topology,
                "signature": topo_sig[:40],
                "efficiency": round(efficiency, 4),
                "count": len(group),
                "success_rate": round(successes / len(group), 2),
                "avg_score": round(avg_score, 2),
                "avg_elapsed_ms": int(avg_elapsed),
                "avg_tokens": int(avg_tokens),
            })

        # 排序: 高效优先，样本数多的优先
        scored.sort(key=lambda x: (x["efficiency"], x["count"]), reverse=True)
        best = scored[:top_k]

        # 自动固化最优拓扑到 Redis
        if best and self.redis:
            self.redis.setex(
                f"workflow:topology:{task_type}",
                86400 * 7,  # 7天TTL
                json.dumps(best[0]["topology"], ensure_ascii=False)
            )

        return {
            "task_type": task_type,
            "topologies": best,
            "recommendation": best[0] if best else None,
            "total_records": len(records),
        }

    def get_best_topology(self, task_type: str) -> Optional[dict]:
        """获取当前最优拓扑（从 Redis 缓存）。"""
        if self.redis:
            cached = self.redis.get(f"workflow:topology:{task_type}")
            if cached:
                return json.loads(cached)

        result = self.evaluate(task_type, top_k=1)
        if result["topologies"]:
            return result["topologies"][0]["topology"]
        return None

    def compare(self, task_type: str, old_topo: dict, new_topo: dict) -> dict:
        """对比新旧拓扑效率。"""
        old_records = [r for r in self._topologies.get(task_type, [])
                       if str(r.topology) == str(old_topo)]
        new_records = [r for r in self._topologies.get(task_type, [])
                       if str(r.topology) == str(new_topo)]

        if not new_records:
            return {"status": "insufficient_data", "note": "新拓扑数据不足"}

        old_avg = (sum(r.metrics.get("elapsed_ms", 1000) for r in old_records) /
                   len(old_records)) if old_records else 0
        new_avg = sum(r.metrics.get("elapsed_ms", 1000) for r in new_records) / len(new_records)

        improvement = (old_avg - new_avg) / old_avg if old_avg > 0 else 0

        return {
            "old_avg_ms": int(old_avg),
            "new_avg_ms": int(new_avg),
            "improvement_pct": round(improvement * 100, 1),
            "should_migrate": improvement > 0.1,  # 提升 > 10% 则切换
        }

    def _group_by_topology(self, records: List[TopologyRecord]) -> Dict[str, list]:
        """按拓扑签名分组。"""
        groups = {}
        for r in records:
            sig = json.dumps(r.topology, sort_keys=True)
            if sig not in groups:
                groups[sig] = []
            groups[sig].append(r)
        return groups

    def get_stats(self) -> dict:
        return {
            "task_types": len(self._topologies),
            "total_records": len(self._records),
            "topologies_evaluated": sum(len(v) for v in self._topologies.values()),
        }
