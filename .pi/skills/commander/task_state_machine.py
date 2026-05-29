#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_SM_ROOT = 0x6c6a9026

"""
雅溪 任务状态机 v2.0 — 五层里程碑 + 事件溯源 + 断点恢复
=========================================================
设计原则:
  - 1 个任务 → 1 个状态机 (不是 5 个独立 FSM)
  - 5 层各有 milestone, 由各层自行更新
  - timeline 不可变追加, 完整审计轨迹
  - 子任务独立追踪 layer/agent/status
  - 大产出存独立 key, hash 引用防膨胀

Redis Key 布局:
  yaxiio:task:{task_id}         — 任务状态 + 元数据
  yaxiio:output:{task_id}:{sid} — 子任务完整产出 (大文本)
  yaxiio:task:active            — 活跃任务索引 (Set)

状态转换:
  IDLE → ANALYZING(L1) → PLANNING(L2) → DISPATCHING(L3) → EXECUTING(L4) → SCORING(L5) → DONE
                                                              ↓
                                                           FAILED → RETRYING → EXECUTING
"""

import json, time, hashlib
from typing import Optional, Dict, List
import redis

# ── 状态定义 ──
STATES = ["IDLE", "ANALYZING", "PLANNING", "DISPATCHING", "EXECUTING", "SCORING", "DONE", "FAILED", "RETRYING", "CANCELLED"]

TRANSITIONS = {
    "IDLE":         ["ANALYZING", "CANCELLED"],
    "ANALYZING":    ["PLANNING", "FAILED", "CANCELLED"],       # L1 完成 → 进入 L2
    "PLANNING":     ["DISPATCHING", "FAILED", "CANCELLED"],    # L2 完成 → 进入 L3
    "DISPATCHING":  ["EXECUTING", "FAILED", "CANCELLED"],      # L3 完成 → 进入 L4
    "EXECUTING":    ["SCORING", "FAILED", "CANCELLED"],        # L4 完成 → 进入 L5
    "SCORING":      ["DONE", "FAILED", "CANCELLED"],           # L5 完成
    "FAILED":       ["RETRYING", "CANCELLED"],
    "RETRYING":     ["EXECUTING", "FAILED", "CANCELLED"],
    "DONE":         [],
    "CANCELLED":    [],
}

# ── 子任务状态 ──
SUBSTATES = ["PENDING", "QUEUED", "RUNNING", "DONE", "FAILED", "TIMEOUT"]

# ── TTL ──
TASK_TTL = 86400      # 任务状态 24h
OUTPUT_TTL = 604800   # 产出 7d


def _hash(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:12]


def _now() -> float:
    return time.time()


class TaskStateMachine:
    """统一任务状态机 — 五层里程碑 + 事件溯源"""

    def __init__(self, redis_host="127.0.0.1", redis_port=6379, redis_pass=None):
        if redis_pass is None:
            redis_pass = os.environ.get("REDIS_PASSWORD", "")
        self.r = redis.Redis(host=redis_host, port=redis_port,
                             password=redis_pass, decode_responses=True)

    # ═══════════════════════════════════════════════
    # 任务生命周期
    # ═══════════════════════════════════════════════

    def create(self, task_id: str, action: str, payload_snippet: str = "") -> dict:
        """创建新任务, 状态 IDLE"""
        state = {
            "task_id": task_id,
            "action": action,
            "status": "IDLE",
            "current_layer": None,
            "progress_pct": 0,
            "created_at": _now(),
            "updated_at": _now(),
            "retries": 0,
            "error": "",

            # ── 五层里程碑 ──
            "milestones": {
                "L1_perception":    {"status": "pending"},
                "L2_planning":      {"status": "pending"},
                "L3_dispatch":      {"status": "pending"},
                "L4_execution":     {"status": "pending"},
                "L5_evaluation":    {"status": "pending"},
            },

            # ── 子任务追踪 ──
            "subtasks": {},

            # ── 事件溯源时间线 ──
            "timeline": [],

            # ── 聚合结果 ──
            "result": {},
        }

        self._save(task_id, state)
        self.r.sadd("yaxiio:task:active", task_id)
        self._event(task_id, state, "TASK_CREATED", f"action={action} snippet={payload_snippet[:80]}")
        return state

    def start_layer(self, task_id: str, layer: str) -> dict:
        """标记进入某一层 (L1→ANALYZING, L2→PLANNING, ...)"""
        layer_to_status = {
            "L1_perception":  "ANALYZING",
            "L2_planning":    "PLANNING",
            "L3_dispatch":    "DISPATCHING",
            "L4_execution":   "EXECUTING",
            "L5_evaluation":  "SCORING",
        }
        new_status = layer_to_status.get(layer)
        if not new_status:
            return {"error": f"unknown layer: {layer}"}

        return self.transition(task_id, new_status, layer=layer,
                               milestone_update={"status": "running", "started_at": _now()})

    def complete_layer(self, task_id: str, layer: str, result: dict = None) -> dict:
        """标记某一层完成"""
        return self.transition(task_id, None, layer=layer,
                               milestone_update={"status": "done", "completed_at": _now()},
                               result=result)

    def fail_layer(self, task_id: str, layer: str, error: str) -> dict:
        """标记某一层失败"""
        return self.transition(task_id, "FAILED", layer=layer,
                               milestone_update={"status": "failed", "error": error[:200]},
                               error=error)

    # ═══════════════════════════════════════════════
    # 子任务管理
    # ═══════════════════════════════════════════════

    def subtask_start(self, task_id: str, sid: str, agent: str, action: str, prompt: str = "") -> dict:
        """子任务开始"""
        state = self._load(task_id)
        if not state:
            return {"error": "task not found"}

        state["subtasks"][sid] = {
            "id": sid,
            "agent": agent,
            "action": action,
            "status": "RUNNING",
            "started_at": _now(),
            "prompt_hash": _hash(prompt) if prompt else "",
        }
        self._save(task_id, state)
        self._event(task_id, state, "SUBTASK_START", f"{sid} agent={agent} action={action}")
        return state

    def subtask_done(self, task_id: str, sid: str, output: str = "",
                     duration_ms: int = 0, error: str = "") -> dict:
        """子任务完成 (写入产出到独立 key, 主状态只存 hash)"""
        state = self._load(task_id)
        if not state:
            return {"error": "task not found"}

        ok = not error
        output_hash = _hash(output) if output else ""
        status = "DONE" if ok else "FAILED"

        # 存储完整产出到独立 key
        if output:
            output_key = f"yaxiio:output:{task_id}:{sid}"
            self.r.setex(output_key, OUTPUT_TTL, output[:5000])

        # 更新子任务状态
        subtask = state["subtasks"].get(sid, {})
        subtask.update({
            "status": status,
            "completed_at": _now(),
            "duration_ms": duration_ms,
            "output_hash": output_hash,
            "output_len": len(output) if output else 0,
            "error": error[:200] if error else "",
        })
        state["subtasks"][sid] = subtask

        # 更新进度
        total = len(state["subtasks"])
        done = sum(1 for s in state["subtasks"].values() if s["status"] in ("DONE", "FAILED"))
        state["progress_pct"] = int(done / max(1, total) * 100)

        self._save(task_id, state)
        event = "SUBTASK_DONE" if ok else "SUBTASK_FAILED"
        self._event(task_id, state, event,
                    f"{sid} {'✅' if ok else '❌'} {duration_ms}ms output={output_hash}")
        return state

    def subtask_timeout(self, task_id: str, sid: str) -> dict:
        """子任务超时"""
        state = self._load(task_id)
        if not state:
            return {"error": "task not found"}
        if sid in state["subtasks"]:
            state["subtasks"][sid]["status"] = "TIMEOUT"
            state["subtasks"][sid]["completed_at"] = _now()
        self._save(task_id, state)
        self._event(task_id, state, "SUBTASK_TIMEOUT", sid)
        return state

    # ═══════════════════════════════════════════════
    # 产出查询
    # ═══════════════════════════════════════════════

    def get_output(self, task_id: str, sid: str) -> Optional[str]:
        """获取子任务完整产出"""
        return self.r.get(f"yaxiio:output:{task_id}:{sid}")

    def get_all_outputs(self, task_id: str) -> Dict[str, str]:
        """获取任务所有子任务产出"""
        keys = self.r.keys(f"yaxiio:output:{task_id}:*")
        result = {}
        for key in keys:
            sid = key.decode() if isinstance(key, bytes) else key
            sid = sid.split(":")[-1]
            val = self.r.get(key)
            if val:
                result[sid] = val
        return result

    # ═══════════════════════════════════════════════
    # 恢复
    # ═══════════════════════════════════════════════

    def list_inflight(self) -> List[dict]:
        """列出所有未完成的任务 (用于断点恢复)"""
        task_ids = self.r.smembers("yaxiio:task:active")
        tasks = []
        for tid in task_ids:
            state = self._load(tid)
            if state and state["status"] not in ("DONE", "CANCELLED", "FAILED"):
                tasks.append(state)
        return tasks

    def get_recoverable_subtasks(self, task_id: str) -> List[dict]:
        """获取需要恢复的子任务 (status=RUNNING 或 QUEUED)"""
        state = self._load(task_id)
        if not state:
            return []
        return [
            s for s in state.get("subtasks", {}).values()
            if s.get("status") in ("RUNNING", "QUEUED", "PENDING")
        ]

    # ═══════════════════════════════════════════════
    # Dashboard 查询
    # ═══════════════════════════════════════════════

    def dashboard_snapshot(self) -> dict:
        """Dashboard 用快照: 所有任务的关键信息"""
        task_ids = list(self.r.smembers("yaxiio:task:active"))[-50:]
        tasks = []
        for tid in task_ids:
            state = self._load(tid)
            if state:
                tasks.append({
                    "task_id": tid,
                    "action": state.get("action", "?"),
                    "status": state.get("status", "?"),
                    "current_layer": state.get("current_layer"),
                    "progress_pct": state.get("progress_pct", 0),
                    "subtask_count": len(state.get("subtasks", {})),
                    "created_at": state.get("created_at"),
                    "updated_at": state.get("updated_at"),
                })
        return {
            "total": len(tasks),
            "running": sum(1 for t in tasks if t["status"] not in ("DONE", "CANCELLED", "FAILED")),
            "tasks": sorted(tasks, key=lambda t: t.get("updated_at", 0), reverse=True),
        }

    # ═══════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════

    def transition(self, task_id: str, new_status: str = None,
                   layer: str = None, milestone_update: dict = None,
                   result: dict = None, error: str = "") -> dict:
        """核心: 状态转换 (含合法性校验)"""
        state = self._load(task_id)
        if not state:
            return {"error": f"task not found: {task_id}"}

        old_status = state["status"]

        if new_status:
            if old_status not in TRANSITIONS:
                return {"error": f"unknown state: {old_status}"}
            if new_status not in TRANSITIONS.get(old_status, []):
                return {"error": f"invalid: {old_status}→{new_status}"}
            state["status"] = new_status

        # 更新层里程碑
        if layer and milestone_update:
            if layer in state["milestones"]:
                state["milestones"][layer].update(milestone_update)
            state["current_layer"] = layer

        if result:
            state["result"].update(result)
        if error:
            state["error"] = error

        state["updated_at"] = _now()
        self._save(task_id, state)

        # 清理活跃索引
        if state["status"] in ("DONE", "CANCELLED"):
            self.r.srem("yaxiio:task:active", task_id)

        return state

    def _event(self, task_id: str, state: dict, event: str, detail: str = ""):
        """追加不可变事件到时间线"""
        entry = {"ts": _now(), "event": event, "detail": detail}
        state["timeline"].append(entry)
        # 限制时间线长度
        if len(state["timeline"]) > 200:
            state["timeline"] = state["timeline"][-150:]
        self._save(task_id, state)

    def _load(self, task_id: str) -> Optional[dict]:
        raw = self.r.get(f"yaxiio:task:{task_id}")
        return json.loads(raw) if raw else None

    def _save(self, task_id: str, state: dict):
        self.r.setex(f"yaxiio:task:{task_id}", TASK_TTL, json.dumps(state, ensure_ascii=False))

    # ── 兼容旧接口 ──
    def get(self, task_id: str):
        return self._load(task_id)

    def cancel(self, task_id: str):
        return self.transition(task_id, "CANCELLED")


# ═══════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    sm = TaskStateMachine()

    # 创建
    sm.create("demo-1", "redesign", "ExampleCorp UI/UX 重设计")
    sm.start_layer("demo-1", "L1_perception")
    sm.complete_layer("demo-1", "L1_perception", {"intent": "redesign", "confidence": 0.99})
    sm.start_layer("demo-1", "L2_planning")
    sm.complete_layer("demo-1", "L2_planning", {"subtask_count": 7})

    # 子任务
    sm.subtask_start("demo-1", "s1", "UI/UX设计师", "启发式评估")
    sm.subtask_start("demo-1", "s2", "品牌策略师", "竞品分析")
    sm.subtask_done("demo-1", "s1", "评估完成: 发现5个改进点", 28000)
    sm.subtask_done("demo-1", "s2", "竞品分析: 3个可借鉴模式", 24000)

    sm.start_layer("demo-1", "L5_evaluation")
    sm.complete_layer("demo-1", "L5_evaluation", {"overall": 8})

    sm.transition("demo-1", "DONE")

    # 查看
    task = sm.get("demo-1")
    print(f"Status: {task['status']}")
    print(f"Progress: {task['progress_pct']}%")
    print(f"Milestones:")
    for name, ms in task["milestones"].items():
        print(f"  {name}: {ms['status']}")
    print(f"Subtasks: {len(task['subtasks'])}")
    print(f"Timeline ({len(task['timeline'])} events):")
    for e in task["timeline"]:
        print(f"  {e['event']}: {e['detail'][:60]}")
    print(f"\nDashboard: {json.dumps(sm.dashboard_snapshot(), indent=2)}")
    print(f"\nOutput s1: {sm.get_output('demo-1', 's1')}")
