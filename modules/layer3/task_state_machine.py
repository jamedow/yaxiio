"""任务状态机 — 11状态 + Redis持久化 + 自动恢复"""
import json, time, os
from enum import Enum
from typing import Dict, List, Optional

class TaskState(Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    WAITING = "waiting"
    EVALUATING = "evaluating"
    RETRYING = "retrying"
    FALLBACK = "fallback"
    COMPLETED = "completed"
    FAILED = "failed"

# 合法转换
TRANSITIONS = {
    TaskState.QUEUED:     [TaskState.PLANNING],
    TaskState.PLANNING:   [TaskState.DISPATCHING, TaskState.FAILED],
    TaskState.DISPATCHING:[TaskState.RUNNING, TaskState.WAITING, TaskState.FAILED],
    TaskState.RUNNING:    [TaskState.EVALUATING, TaskState.FAILED, TaskState.RETRYING],
    TaskState.WAITING:    [TaskState.RUNNING],
    TaskState.EVALUATING: [TaskState.COMPLETED, TaskState.RETRYING, TaskState.FAILED],
    TaskState.RETRYING:   [TaskState.DISPATCHING, TaskState.FALLBACK],
    TaskState.FALLBACK:   [TaskState.COMPLETED],
}

class TaskStateMachine:
    def __init__(self, redis_client=None):
        self.redis = redis_client
        
    def create(self, task_id: str, action: str, payload: dict = None, max_retries: int = 3, threshold: float = 6.0) -> dict:
        state = {
            "task_id": task_id, "action": action,
            "status": TaskState.QUEUED.value,
            "subtasks": [], "retry_count": 0, "max_retries": max_retries,
            "score_threshold": threshold, "score": None, "error": None,
            "created_at": time.time(), "updated_at": time.time(),
            "agent": None, "result": None
        }
        self._save(task_id, state)
        self._log(task_id, None, TaskState.QUEUED.value)
        return state
    
    def transition(self, task_id: str, to_state: TaskState, **kwargs) -> dict:
        state = self._load(task_id)
        if not state:
            return {"error": "task not found", "task_id": task_id}
        
        current = TaskState(state["status"])
        if to_state not in TRANSITIONS.get(current, []):
            return {"error": f"invalid transition {current.value}→{to_state.value}", "task_id": task_id}
        
        state["status"] = to_state.value
        state["updated_at"] = time.time()
        for k, v in kwargs.items():
            if k in state:
                state[k] = v
        
        self._save(task_id, state)
        self._log(task_id, current.value, to_state.value)
        return state
    
    def get(self, task_id: str) -> Optional[dict]:
        return self._load(task_id)
    
    def list_by_status(self, status: TaskState) -> List[str]:
        if not self.redis:
            return []
        try:
            return list(self.redis.smembers(f"task:index:{status.value}") or [])
        except:
            return []
    
    def fail(self, task_id: str, error: str) -> dict:
        state = self._load(task_id)
        if not state: return {"error": "not found"}
        state["retry_count"] = state.get("retry_count", 0) + 1
        state["error"] = error
        state["updated_at"] = time.time()
        
        if state["retry_count"] < state.get("max_retries", 3):
            state["status"] = TaskState.RETRYING.value
            self._log(task_id, "running", "retrying")
        else:
            state["status"] = TaskState.FALLBACK.value
            self._log(task_id, "retrying", "fallback")
        
        self._save(task_id, state)
        return state
    
    def retry(self, task_id: str) -> dict:
        state = self._load(task_id)
        if not state: return {"error": "not found"}
        state["status"] = TaskState.DISPATCHING.value
        state["updated_at"] = time.time()
        self._save(task_id, state)
        return state
    
    def recover(self) -> List[str]:
        """恢复未完成的任务（Commander重启后调用）"""
        recovered = []
        for status in [TaskState.RUNNING, TaskState.DISPATCHING, TaskState.EVALUATING, TaskState.RETRYING]:
            for tid in self.list_by_status(status):
                state = self._load(tid)
                if state:
                    age = time.time() - state.get("updated_at", 0)
                    if age > 300:  # 超过5分钟，标记为失败可重试
                        self.fail(tid, f"timeout after {int(age)}s")
                        recovered.append(tid)
        return recovered
    
    def stats(self) -> dict:
        if not self.redis: return {}
        stats = {}
        for s in TaskState:
            stats[s.value] = self.redis.scard(f"task:index:{s.value}") or 0
        return stats
    
    def _save(self, task_id: str, state: dict):
        if not self.redis: return
        self.redis.set(f"task:{task_id}", json.dumps(state, ensure_ascii=False))
    
    def _load(self, task_id: str) -> Optional[dict]:
        if not self.redis: return None
        try:
            raw = self.redis.client.get(f"task:{task_id}")
            return json.loads(raw) if raw else None
        except: return None
    
    def _log(self, task_id: str, from_state: str, to_state: str):
        if not self.redis: return
        entry = json.dumps({"from": from_state, "to": to_state, "ts": time.time()})
        self.redis.client.rpush(f"task:log:{task_id}", entry)
        self.redis.client.ltrim(f"task:log:{task_id}", -100, -1)
