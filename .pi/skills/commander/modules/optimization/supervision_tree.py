
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Supervision Tree v3.0 — 吸收自 Wactorz
=======================================
轻量级监督树管理 Commander 的子 Agent。

策略:
  ONE_FOR_ONE  — 子Agent崩溃后自动重启，不触发 Commander Guard
  ONE_FOR_ALL  — 一个子Agent崩溃，重启所有兄弟Agent
  REST_FOR_ONE — 崩溃后重启该Agent及其后续依赖的Agent

结构:
  Commander (Supervisor)
    ├── 翻译官 (Worker)
    ├── 商务经理 (Worker)
    │   ├── 售前助理 (Child Worker)
    │   └── 报价助手 (Child Worker)
    └── 售前经理 (Worker)

配置:
  SUPERVISION_STRATEGY=one_for_one
  MAX_RESTARTS_PER_PERIOD=5
  RESTART_PERIOD_SECONDS=60
"""

import os
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

SUPERVISION_STRATEGY = os.environ.get("SUPERVISION_STRATEGY", "one_for_one")
MAX_RESTARTS = int(os.environ.get("MAX_RESTARTS_PER_PERIOD", "5"))
RESTART_PERIOD = int(os.environ.get("RESTART_PERIOD_SECONDS", "60"))


class Strategy(Enum):
    ONE_FOR_ONE = "one_for_one"
    ONE_FOR_ALL = "one_for_all"
    REST_FOR_ONE = "rest_for_one"


@dataclass
class ChildSpec:
    """子Agent规格。"""
    agent_id: str
    agent_name: str
    start_command: str
    dependencies: List[str] = field(default_factory=list)  # 依赖的其他Agent ID
    restart_count: int = 0
    last_restart: float = 0
    status: str = "stopped"  # running | stopped | crashed
    children: List[str] = field(default_factory=list)  # 子Agent的子Agent


class SupervisionTree:
    """轻量级监督树。"""

    def __init__(self, commander_guard=None):
        self.strategy = Strategy(SUPERVISION_STRATEGY)
        self.max_restarts = MAX_RESTARTS
        self.restart_period = RESTART_PERIOD
        self.commander_guard = commander_guard  # Pi Guardian 引用

        self._children: Dict[str, ChildSpec] = {}
        self._parent_map: Dict[str, str] = {}  # child_id → parent_id
        self._lock = threading.Lock()

    def register(self, parent_id: str, child: ChildSpec):
        """注册子Agent到监督树。"""
        with self._lock:
            self._children[child.agent_id] = child
            self._parent_map[child.agent_id] = parent_id

            # 更新父Agent的子列表
            if parent_id in self._children:
                self._children[parent_id].children.append(child.agent_id)

    def unregister(self, agent_id: str):
        """从监督树移除。"""
        with self._lock:
            self._children.pop(agent_id, None)
            self._parent_map.pop(agent_id, None)

    def report_crash(self, agent_id: str, error: str = "") -> dict:
        """子Agent崩溃后触发重启策略。

        Returns:
            {"action": "restart"|"escalate", "agents_to_restart": [...], "reason": "..."}
        """
        with self._lock:
            child = self._children.get(agent_id)
            if not child:
                return {"action": "escalate", "reason": "unknown_agent"}

            child.status = "crashed"

            # 检查重启限制
            now = time.time()
            if now - child.last_restart < self.restart_period:
                child.restart_count += 1
            else:
                child.restart_count = 1
            child.last_restart = now

            if child.restart_count > self.max_restarts:
                return {
                    "action": "escalate",
                    "reason": f"exceeded_max_restarts ({child.restart_count})",
                    "agent_id": agent_id,
                }

            # 按策略决定重启范围
            if self.strategy == Strategy.ONE_FOR_ONE:
                agents = [agent_id]
            elif self.strategy == Strategy.ONE_FOR_ALL:
                parent = self._parent_map.get(agent_id)
                agents = [agent_id] + [
                    cid for cid, pid in self._parent_map.items()
                    if pid == parent and cid != agent_id
                ]
            elif self.strategy == Strategy.REST_FOR_ONE:
                agents = self._get_dependents(agent_id)
            else:
                agents = [agent_id]

            return {
                "action": "restart",
                "agents_to_restart": agents,
                "strategy": self.strategy.value,
                "reason": f"agent {agent_id} crashed: {error[:100]}",
            }

    def _get_dependents(self, agent_id: str) -> List[str]:
        """获取依赖此Agent的所有下游Agent（REST_FOR_ONE策略）。"""
        result = [agent_id]
        for cid, child in self._children.items():
            if agent_id in child.dependencies:
                result.append(cid)
                result.extend(self._get_dependents(cid))
        return list(set(result))

    def get_tree(self) -> dict:
        """获取监督树结构。"""
        with self._lock:
            nodes = {}
            for aid, child in self._children.items():
                nodes[aid] = {
                    "name": child.agent_name,
                    "status": child.status,
                    "restarts": child.restart_count,
                    "children": child.children,
                    "dependencies": child.dependencies,
                }
            return {"strategy": self.strategy.value, "nodes": nodes}

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "total_children": len(self._children),
                "running": sum(1 for c in self._children.values() if c.status == "running"),
                "crashed": sum(1 for c in self._children.values() if c.status == "crashed"),
                "strategy": self.strategy.value,
            }
