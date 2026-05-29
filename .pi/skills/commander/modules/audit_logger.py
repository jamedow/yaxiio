
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Audit Logger v3.0 — JSON Lines 审计日志
========================================
记录每次 LLM 调用、工具执行、状态变更。
写入 MongoDB audit_logs 集合，JSON Lines 格式。

日志级别: DEBUG | INFO | WARN | ERROR
日志类型: llm_call | tool_exec | state_change | session_event | failure

配置:
  AUDIT_ENABLED=true        是否启用审计
  AUDIT_LOG_LEVEL=INFO      最低记录级别
  AUDIT_BATCH_SIZE=50       批量写入大小
"""

import json
import os
import time
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

AUDIT_ENABLED = os.environ.get("AUDIT_ENABLED", "true").lower() == "true"
AUDIT_LOG_LEVEL = os.environ.get("AUDIT_LOG_LEVEL", "INFO")
AUDIT_BATCH_SIZE = int(os.environ.get("AUDIT_BATCH_SIZE", "50"))

LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


class AuditLogger:
    """JSON Lines 审计日志。"""

    def __init__(self, mongo_db=None):
        self.mongo = mongo_db
        self._buffer: List[dict] = []
        self._lock = threading.Lock()
        self._min_level = LOG_LEVELS.get(AUDIT_LOG_LEVEL.upper(), 20)
        self._flush_timer = None

        if AUDIT_ENABLED and self.mongo is not None:
            self._start_flush_timer()

    def _start_flush_timer(self):
        """每 10 秒自动刷新缓冲。"""
        def _flush_periodic():
            self.flush()
            if AUDIT_ENABLED:
                self._flush_timer = threading.Timer(10, _flush_periodic)
                self._flush_timer.daemon = True
                self._flush_timer.start()
        t = threading.Timer(10, _flush_periodic)
        t.daemon = True
        t.start()

    def log(self, level: str, event_type: str, session_token: str = "",
            agent_id: str = "", task_id: str = "", detail: Any = None,
            metadata: dict = None):
        """记录一条审计日志。

        Args:
            level: DEBUG|INFO|WARN|ERROR
            event_type: llm_call|tool_exec|state_change|session_event|failure
            session_token: 会话令牌
            agent_id: Agent 标识
            task_id: 任务 ID
            detail: 事件详情 (dict 或 str)
            metadata: 附加元数据
        """
        if LOG_LEVELS.get(level.upper(), 0) < self._min_level:
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level.upper(),
            "event_type": event_type,
            "session_token": session_token,
            "agent_id": agent_id,
            "task_id": task_id,
            "detail": detail if isinstance(detail, dict) else {"message": str(detail)},
            "metadata": metadata or {},
        }

        with self._lock:
            self._buffer.append(entry)
            if len(self._buffer) >= AUDIT_BATCH_SIZE:
                self._flush()

    def log_llm_call(self, prompt: str, response: str, model: str = "",
                     token_count: int = 0, latency_ms: float = 0, **kwargs):
        """记录 LLM 调用。"""
        self.log("INFO", "llm_call", detail={
            "prompt": prompt[:500],
            "response": response[:500],
            "model": model,
            "token_count": token_count,
            "latency_ms": latency_ms,
        }, **kwargs)

    def log_tool_exec(self, tool_name: str, arguments: dict, result: Any,
                      success: bool = True, latency_ms: float = 0, **kwargs):
        """记录工具执行。"""
        self.log("INFO" if success else "ERROR", "tool_exec", detail={
            "tool": tool_name,
            "arguments": json.dumps(arguments, ensure_ascii=False)[:500],
            "result": str(result)[:500],
            "success": success,
            "latency_ms": latency_ms,
        }, **kwargs)

    def log_state_change(self, entity: str, from_state: str, to_state: str,
                          reason: str = "", **kwargs):
        """记录状态变更。"""
        self.log("INFO", "state_change", detail={
            "entity": entity,
            "from_state": from_state,
            "to_state": to_state,
            "reason": reason,
        }, **kwargs)

    def log_failure(self, error: str, agent_id: str = "", task_id: str = "",
                     strategy: str = "", **kwargs):
        """记录失败事件。"""
        self.log("ERROR", "failure", detail={
            "error": error,
            "recovery_strategy": strategy,
        }, agent_id=agent_id, task_id=task_id, **kwargs)

    def _flush(self):
        """将缓冲区写入 MongoDB。"""
        if not self._buffer or not self.mongo:
            return
        try:
            batch = self._buffer[:]
            self._buffer.clear()
            self.mongo["audit_logs"].insert_many(batch, ordered=False)
        except Exception as e:
            # 写入失败时保留 buffer（下次重试）
            self._buffer = batch + self._buffer
            print(f"[AuditLogger] 批量写入失败: {e}")

    def flush(self):
        """手动刷新。"""
        with self._lock:
            self._flush()

    def query(self, session_token: str = None, agent_id: str = None,
              event_type: str = None, limit: int = 100) -> List[dict]:
        """查询审计日志。"""
        if self.mongo is None:
            return []
        query = {}
        if session_token:
            query["session_token"] = session_token
        if agent_id:
            query["agent_id"] = agent_id
        if event_type:
            query["event_type"] = event_type
        try:
            return list(self.mongo["audit_logs"].find(
                query, {"_id": 0}
            ).sort("timestamp", -1).limit(limit))
        except Exception:
            return []

    def get_stats(self) -> dict:
        """审计统计。"""
        if self.mongo is None:
            return {}
        try:
            total = self.mongo["audit_logs"].count_documents({})
            errors = self.mongo["audit_logs"].count_documents({"level": "ERROR"})
            return {"total_logs": total, "error_count": errors}
        except Exception:
            return {}
