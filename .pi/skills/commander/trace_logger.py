#!/usr/bin/env python3
"""
Yaxiio 结构化日志系统 v1.0
==========================
格式: [时间] [级别] [trace] [模块] [方法] 操作 | key=value ...

使用:
    from trace_logger import TraceLogger
    log = TraceLogger("Commander")

    log.info("handle_task", "宪法审查", action="audit", verdict="DELEGATED")
    log.warn("spawn_neuron", "启动超时", agent="审计官", timeout=30)
    log.error("run", "Redis连接失败", error=str(e))

输出:
    [22:30:00.123] INFO  [a1b2c3d4] [Commander] [handle_task] 宪法审查 | action=audit verdict=DELEGATED
    [22:30:01.456] WARN  [a1b2c3d4] [Commander] [spawn_neuron] 启动超时 | agent=审计官 timeout=30
    [22:30:02.789] ERROR [a1b2c3d4] [Commander] [run] Redis连接失败 | error=Connection refused

特性:
    - 单行格式，方便 grep / ELK 解析
    - trace_id 可选，不传自动用空
    - 同时写 stdout + Redis (key: trace:{trace_id}:log, TTL 7d)
    - 级别: DEBUG/INFO/WARN/ERROR
"""

import json
import os
import sys
import time
import threading
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
DEFAULT_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_TO_REDIS = os.environ.get("LOG_TO_REDIS", "1") == "1"
LOG_TO_STDOUT = os.environ.get("LOG_TO_STDOUT", "1") == "1"
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "Yaxiio2026")
REDIS_LOG_TTL = 86400 * 7  # 7天


# ═══════════════════════════════════════════════════════════════
# TraceLogger
# ═══════════════════════════════════════════════════════════════

class TraceLogger:
    """结构化日志器 — 每个模块实例化一个，携带模块名上下文。

    用法:
        log = TraceLogger("WorkflowEngine")
        log.info("process", "L1感知完成", intent="audit", confidence=0.85, duration_ms=120)
    """

    def __init__(self, module: str):
        self.module = module
        self._redis = None
        self._redis_lock = threading.Lock()
        self._redis_failed = False

    # ── 公共方法 ──────────────────────────────────────────

    def debug(self, method: str, operation: str, trace_id: str = "", **kwargs):
        self._log("DEBUG", method, operation, trace_id, **kwargs)

    def info(self, method: str, operation: str, trace_id: str = "", **kwargs):
        self._log("INFO", method, operation, trace_id, **kwargs)

    def warn(self, method: str, operation: str, trace_id: str = "", **kwargs):
        self._log("WARN", method, operation, trace_id, **kwargs)

    def error(self, method: str, operation: str, trace_id: str = "", **kwargs):
        self._log("ERROR", method, operation, trace_id, **kwargs)

    # ── 内部实现 ──────────────────────────────────────────

    def _log(self, level: str, method: str, operation: str,
             trace_id: str = "", **kwargs):
        """格式化并输出日志。"""
        if LEVELS.get(level, 0) < LEVELS.get(DEFAULT_LEVEL, 20):
            return

        ts = datetime.now().strftime("%H:%M:%S.") + f"{int(time.time() * 1000) % 1000:03d}"
        tid = trace_id[:12] if trace_id else "-" * 12

        # 构建参数字符串
        params = " ".join(f"{k}={self._fmt_val(v)}" for k, v in kwargs.items())

        # 格式: [时间] 级别 [trace] [模块] [方法] 操作 | params
        line = f"[{ts}] {level:<5} [{tid}] [{self.module}] [{method}] {operation}"
        if params:
            line += " | " + params

        # 输出到 stdout
        if LOG_TO_STDOUT:
            print(line, flush=True)

        # 输出到 Redis
        if LOG_TO_REDIS and tid != "-" * 12:
            self._to_redis(tid, level, ts, method, operation, kwargs)

    def _fmt_val(self, v) -> str:
        """格式化参数值。"""
        if isinstance(v, str):
            s = v.replace(" ", "_")[:80]
            return s if " " not in v else f"\"{s}\""
        if isinstance(v, float):
            return f"{v:.2f}"
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)[:80]

    def _to_redis(self, trace_id: str, level: str, ts: str,
                  method: str, operation: str, kwargs: dict):
        """写入 Redis trace 日志。"""
        if self._redis_failed:
            return

        try:
            with self._redis_lock:
                if self._redis is None:
                    import redis
                    self._redis = redis.Redis(
                        host=REDIS_HOST, port=REDIS_PORT,
                        password=REDIS_PASS, protocol=2,
                        decode_responses=True,
                        socket_connect_timeout=2,
                    )

            entry = json.dumps({
                "ts": ts, "level": level, "module": self.module,
                "method": method, "operation": operation,
                "trace_id": trace_id, "params": kwargs,
            }, ensure_ascii=False, default=str)

            key = f"trace:{trace_id}:log"
            pipe = self._redis.pipeline()
            pipe.rpush(key, entry)
            pipe.ltrim(key, -200, -1)  # 只保留最近 200 条
            pipe.expire(key, REDIS_LOG_TTL)
            pipe.execute()

        except Exception:
            self._redis_failed = True


# ═══════════════════════════════════════════════════════════════
# 工具函数 — 查询日志
# ═══════════════════════════════════════════════════════════════

def query_trace_logs(trace_id: str, limit: int = 50) -> list:
    """按 trace_id 查询全链路日志。"""
    try:
        import redis
        r = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT,
            password=REDIS_PASS, protocol=2,
            decode_responses=True, socket_connect_timeout=2,
        )
        key = f"trace:{trace_id}:log"
        entries = r.lrange(key, -limit, -1)
        return [json.loads(e) for e in entries]
    except Exception as e:
        return [{"error": str(e)}]


def query_recent_errors(limit: int = 20) -> list:
    """查询最近错误（扫描 trace:* 中以 ERROR 开头的）。简化实现。"""
    return []  # 生产环境建议用 Redis Search 或 ELK


# ═══════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    log = TraceLogger("TestModule")
    log.info("test_method", "测试操作", trace_id="abc123",
             action="test", count=42, success=True, duration_ms=15.5)
    log.warn("test_method", "测试警告", trace_id="abc123",
             reason="模拟警告", threshold=100)
    log.error("test_method", "测试错误", trace_id="abc123",
             error="模拟异常", code=500)

    # 查询
    import time
    time.sleep(0.5)
    logs = query_trace_logs("abc123")
    print(f"\n=== 查询到 {len(logs)} 条日志 ===")
    for entry in logs:
        print(f"  [{entry[ts]}] {entry[level]} [{entry[module]}] [{entry[method]}] {entry[operation]}")
