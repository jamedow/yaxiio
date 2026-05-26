"""全链路追踪 — 记录每次LLM调用、工具执行、Agent决策"""
import json, time, threading, os
from typing import Dict, List, Optional

class TraceSpan:
    def __init__(self, name: str, span_type: str = "llm_call"):
        self.name = name
        self.type = span_type
        self.start = time.time()
        self.end = None
        self.duration_ms = 0
        self.input = {}
        self.output = {}
        self.tokens = {"input": 0, "output": 0}
        self.error = None
        self.meta = {}

    def finish(self, output: dict = None, tokens: dict = None, error: str = None):
        self.end = time.time()
        self.duration_ms = int((self.end - self.start) * 1000)
        if output: self.output = output
        if tokens: self.tokens = tokens
        if error: self.error = error

    def to_dict(self) -> dict:
        return {
            "name": self.name, "type": self.type,
            "duration_ms": self.duration_ms,
            "tokens": self.tokens, "error": self.error,
            "meta": self.meta
        }


class TraceCollector:
    """全链路追踪收集器"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.traces: Dict[str, List[TraceSpan]] = {}
        self._task_meta: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._db_path = os.environ.get("TRACE_DB", "/opt/commander/data/traces.jsonl")

    def start_task(self, task_id: str, meta: dict = None):
        with self._lock:
            self.traces[task_id] = []
            self._task_meta[task_id] = meta or {}

    def span(self, task_id: str, name: str, span_type: str = "llm_call") -> TraceSpan:
        span = TraceSpan(name, span_type)
        with self._lock:
            if task_id not in self.traces:
                self.traces[task_id] = []
            self.traces[task_id].append(span)
        return span

    def finish_task(self, task_id: str, status: str = "completed"):
        with self._lock:
            if task_id not in self.traces: return
            meta = self._task_meta.get(task_id, {})
            spans_data = [s.to_dict() for s in self.traces[task_id] if s.end]
            total_tokens = sum(s.tokens["input"] + s.tokens["output"] for s in self.traces[task_id] if s.end)
            total_ms = sum(s.duration_ms for s in self.traces[task_id] if s.end)
            summary = {
                "task_id": task_id, "status": status,
                "spans": len(spans_data), "total_tokens": total_tokens,
                "total_ms": total_ms, "spans_detail": spans_data,
                "meta": meta, "finished_at": time.time()
            }
            # 写入 JSONL
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with open(self._db_path, "a") as f:
                f.write(json.dumps(summary, ensure_ascii=False) + "\n")
            # 清理
            del self.traces[task_id]
            return summary

    def recent(self, limit: int = 20) -> List[dict]:
        if not os.path.exists(self._db_path): return []
        results = []
        with open(self._db_path) as f:
            for line in f:
                results.append(json.loads(line))
        return results[-limit:]

    def stats(self, hours: int = 24) -> dict:
        cutoff = time.time() - hours * 3600
        records = self.recent(1000)
        recent = [r for r in records if r.get("finished_at", 0) > cutoff]
        if not recent: return {}
        total_tokens = sum(r["total_tokens"] for r in recent)
        total_tasks = len(recent)
        avg_ms = sum(r["total_ms"] for r in recent) / total_tasks if total_tasks else 0
        return {
            "tasks": total_tasks, "total_tokens": total_tokens,
            "avg_duration_ms": round(avg_ms), "period_hours": hours
        }
