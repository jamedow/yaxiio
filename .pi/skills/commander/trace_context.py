#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
TraceContext — 分布式追踪上下文
=================================
为 Yaxiio 系统提供轻量级的请求追踪能力。

设计原则：
  1. 零外部依赖，纯标准库实现
  2. W3C TraceContext 兼容格式: {version}-{trace_id}-{span_id}-{flags}
  3. 线程安全：使用 threading.local() 存储当前上下文
  4. 自动注入到 Redis Pub/Sub 消息中

使用方式：
  from trace_context import TraceContext, trace_span

  # Commander 入口：生成根 trace
  with trace_span("handle_task", task_description="审计俄语页面"):
      # 所有下游操作自动继承此 trace_id

  # 发布消息时自动注入
  msg = commander.build_message(target, payload)
  msg["metadata"]["traceparent"] = TraceContext.current_traceparent()

  # Agent 收到消息时恢复上下文
  TraceContext.restore_from_message(msg)
"""

import json
import os
import threading
import time
import uuid
from typing import Optional


# ═══════════════════════════════════════════════════════════════
# TraceContext
# ═══════════════════════════════════════════════════════════════

class TraceContext:
    """线程本地的追踪上下文。

    每个请求/任务从 Commander 入口处生成一个 trace_id，
    通过 Pub/Sub 消息携带到所有下游 Agent 和 MCP Server。
    """

    _local = threading.local()
    VERSION = "00"  # W3C tracecontext version

    # ── 生成 ─────────────────────────────────────────────

    @staticmethod
    def generate_trace_id() -> str:
        """生成 128 位 trace_id（32 hex chars）。"""
        return uuid.uuid4().hex

    @staticmethod
    def generate_span_id() -> str:
        """生成 64 位 span_id（16 hex chars）。"""
        return uuid.uuid4().hex[:16]

    # ── 设值 ─────────────────────────────────────────────

    @staticmethod
    def start_trace(trace_id: str = None, parent_span_id: str = None) -> str:
        """开始一个新的 trace。

        Args:
            trace_id: 追踪 ID（默认自动生成）
            parent_span_id: 父 span（默认无）

        Returns:
            trace_id
        """
        trace_id = trace_id or TraceContext.generate_trace_id()
        span_id = TraceContext.generate_span_id()

        TraceContext._local.trace_id = trace_id
        TraceContext._local.span_id = span_id
        TraceContext._local.parent_span_id = parent_span_id
        TraceContext._local.start_time = time.time()
        TraceContext._local.extra = {}

        return trace_id

    @staticmethod
    def child_span(operation: str = "") -> str:
        """在现有 trace 下创建子 span。

        Returns:
            新的 span_id
        """
        ctx = TraceContext._local
        if not hasattr(ctx, "trace_id"):
            # 没有父 trace，自动创建根 trace
            TraceContext.start_trace()
            return ctx.span_id

        ctx.parent_span_id = ctx.span_id
        ctx.span_id = TraceContext.generate_span_id()
        ctx.start_time = time.time()
        return ctx.span_id

    # ── 取值 ─────────────────────────────────────────────

    @staticmethod
    def get_trace_id() -> Optional[str]:
        return getattr(TraceContext._local, "trace_id", None)

    @staticmethod
    def get_span_id() -> Optional[str]:
        return getattr(TraceContext._local, "span_id", None)

    @staticmethod
    def current_traceparent() -> str:
        """返回 W3C traceparent 格式字符串。

        格式: {version}-{trace_id}-{span_id}-{flags}
        """
        ctx = TraceContext._local
        tid = getattr(ctx, "trace_id", None) or TraceContext.generate_trace_id()
        sid = getattr(ctx, "span_id", None) or TraceContext.generate_span_id()
        flags = "01"  # sampled
        return f"{TraceContext.VERSION}-{tid}-{sid}-{flags}"

    @staticmethod
    def restore_from_message(msg: dict):
        """从 Pub/Sub 消息中恢复 trace 上下文。

        Args:
            msg: Redis Pub/Sub 消息 dict
        """
        metadata = msg.get("metadata", {})
        traceparent = metadata.get("traceparent", "")
        if traceparent and len(traceparent.split("-")) == 4:
            parts = traceparent.split("-")
            tid = parts[1] if len(parts) > 1 else None
            sid = parts[2] if len(parts) > 2 else None
            if tid and len(tid) == 32:
                TraceContext._local.trace_id = tid
                TraceContext._local.span_id = TraceContext.generate_span_id()
                TraceContext._local.parent_span_id = sid
                TraceContext._local.start_time = time.time()

    @staticmethod
    def to_dict() -> dict:
        """导出当前 trace 上下文为 dict（用于注入消息）。"""
        ctx = TraceContext._local
        return {
            "trace_id": getattr(ctx, "trace_id", None),
            "span_id": getattr(ctx, "span_id", None),
            "traceparent": TraceContext.current_traceparent(),
        }

    @staticmethod
    def set_tag(key: str, value):
        """设置自定义标签（如 task_id, agent_name）。"""
        if not hasattr(TraceContext._local, "extra"):
            TraceContext._local.extra = {}
        TraceContext._local.extra[key] = value

    @staticmethod
    def elapsed() -> float:
        """返回当前 span 的耗时（秒）。"""
        start = getattr(TraceContext._local, "start_time", time.time())
        return time.time() - start

    @staticmethod
    def clear():
        """清除当前线程的 trace 上下文。"""
        TraceContext._local.__dict__.clear()


# ═══════════════════════════════════════════════════════════════
# trace_span — 上下文管理器
# ═══════════════════════════════════════════════════════════════

class trace_span:
    """追踪 span 的上下文管理器。

    用法:
      with trace_span("handle_task", task_id="task-001"):
          # 此代码块内的所有操作共享同一个 trace_id
          commander.dispatch_task(...)
    """

    def __init__(self, operation: str = "", **tags):
        self.operation = operation
        self.tags = tags

    def __enter__(self):
        TraceContext.child_span(self.operation)
        for k, v in self.tags.items():
            TraceContext.set_tag(k, v)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = TraceContext.elapsed()
        if exc_type:
            TraceContext.set_tag("error", str(exc_val)[:200])
        TraceContext.set_tag("elapsed_ms", round(elapsed * 1000))
        # 不 clear()，让下游能继续使用


# ═══════════════════════════════════════════════════════════════
# 结构化日志适配器
# ═══════════════════════════════════════════════════════════════

class TraceLogger:
    """带 trace 上下文的日志适配器。

    用法:
      logger = TraceLogger("commander")
      logger.info("task_dispatched", task_id="task-001", agent="翻译官")
      # 输出: [2026-05-29T12:00:00] [commander] [trace=abc123] task_dispatched task_id=task-001 agent=翻译官
    """

    def __init__(self, module: str):
        self.module = module

    def _format(self, level: str, message: str, **kwargs) -> str:
        ctx = TraceContext.to_dict()
        tid = ctx.get("trace_id", "no-trace")[:8]
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in kwargs.items())
        extra_str = f" {extras}" if extras else ""
        return f"[{ts}] [{self.module}] [trace={tid}] {level}: {message}{extra_str}"

    def info(self, message: str, **kwargs):
        print(self._format("INFO", message, **kwargs))

    def warn(self, message: str, **kwargs):
        print(self._format("WARN", message, **kwargs))

    def error(self, message: str, **kwargs):
        print(self._format("ERROR", message, **kwargs))

    def debug(self, message: str, **kwargs):
        if os.environ.get("LOG_LEVEL", "INFO").upper() == "DEBUG":
            print(self._format("DEBUG", message, **kwargs))
