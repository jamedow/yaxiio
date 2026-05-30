"""
Yaxiio 资源回收器 (Garbage Collector)
======================================
自动清理过期/积压的资源，防止内存泄漏和磁盘占满。

清理项:
  1. Stream Pending 消息 (XACK 超过 N 分钟未处理的)
  2. 过期任务 key (DONE 超过 N 天)
  3. 过期进度 key
  4. 旧 trace log
  5. 僵尸 Neuron 进程

用法:
  gc = YaxiioGC(redis_client)
  gc.collect()  # 手动触发
  gc.start()    # 后台定时 (每5分钟)
"""

import os, time, json, threading

# ═══════════════════════════════════════════════
# 配置 (可环境变量覆盖)
# ═══════════════════════════════════════════════

GC_CONFIG = {
    "interval_sec": int(os.environ.get("YAXIIO_GC_INTERVAL", "300")),     # 5分钟
    "stream_pending_max_age_sec": int(os.environ.get("YAXIIO_GC_PENDING_AGE", "300")),  # 5分钟
    "task_done_max_age_sec": int(os.environ.get("YAXIIO_GC_TASK_AGE", "86400")),        # 1天
    "trace_max_age_sec": int(os.environ.get("YAXIIO_GC_TRACE_AGE", "604800")),          # 7天
    "progress_max_age_sec": int(os.environ.get("YAXIIO_GC_PROGRESS_AGE", "86400")),     # 1天
    "max_pending_per_stream": int(os.environ.get("YAXIIO_GC_MAX_PENDING", "1000")),
    "dry_run": os.environ.get("YAXIIO_GC_DRY_RUN", "false").lower() == "true",
}


class YaxiioGC:
    """Yaxiio 垃圾回收器"""

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.config = GC_CONFIG
        self._running = False
        self._thread = None
        self.stats = {"stream_acked": 0, "tasks_deleted": 0, "traces_deleted": 0,
                      "progress_deleted": 0, "last_run": 0, "runs": 0}

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            return None

    def collect_streams(self) -> int:
        """清理超时的 Stream pending 消息"""
        acked = 0
        try:
            import redis as _r
            r = _r.Redis(host="127.0.0.1", port=6379,
                        password=os.environ.get("REDIS_PASSWORD", ""),
                        decode_responses=True, socket_connect_timeout=3)

            # 已知的 Stream keys
            streams = ["yaxiio:stream:L4", "yaxiio:stream:L4_response", "yaxiio:stream:task_incoming"]
            groups = {"yaxiio:stream:L4": "agents-L4",
                      "yaxiio:stream:L4_response": "commander-response",
                      "yaxiio:stream:task_incoming": "commander-main"}

            for stream in streams:
                group = groups.get(stream)
                if not group:
                    continue
                try:
                    # 获取 pending 总数
                    pending_info = r.xpending(stream, group)
                    total_pending = pending_info.get("pending", 0) if isinstance(pending_info, dict) else 0

                    # 如果积压超过阈值，批量清理
                    if total_pending > self.config["max_pending_per_stream"]:
                        print(f"[GC] {stream} pending={total_pending} > {self.config['max_pending_per_stream']}, 清理旧消息...", flush=True)
                        # 获取超时的 pending 消息
                        pending_msgs = r.xpending_range(stream, group, min="-", max="+", count=100)
                        for entry in pending_msgs:
                            msg_id = entry.get("message_id", "")
                            idle_ms = entry.get("time_since_delivered", 0)
                            if msg_id and idle_ms > self.config["stream_pending_max_age_sec"] * 1000:
                                if not self.config["dry_run"]:
                                    r.xack(stream, group, msg_id)
                                acked += 1
                except Exception:
                    pass

            # 裁剪 Stream 长度
            for stream in streams:
                try:
                    length = r.xlen(stream)
                    if length > 5000:
                        r.xtrim(stream, maxlen=1000, approximate=True)
                except Exception:
                    pass

        except Exception as e:
            print(f"[GC] Stream清理异常: {e}", flush=True)

        return acked

    def collect_tasks(self) -> int:
        """清理过期的 DONE 任务 key"""
        deleted = 0
        try:
            import redis as _r
            r = _r.Redis(host="127.0.0.1", port=6379,
                        password=os.environ.get("REDIS_PASSWORD", ""),
                        decode_responses=True, socket_connect_timeout=3)

            for key in r.keys("yaxiio:task:*"):
                if key.endswith(":active"):
                    continue
                try:
                    data = r.get(key)
                    if data:
                        d = json.loads(data)
                        status = d.get("status", "")
                        updated = d.get("updated_at", 0)
                        if status == "DONE" and time.time() - updated > self.config["task_done_max_age_sec"]:
                            if not self.config["dry_run"]:
                                r.delete(key)
                                r.srem("yaxiio:task:active", key.replace("yaxiio:task:", ""))
                            deleted += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted

    def collect_traces(self) -> int:
        """清理过期的 trace log"""
        deleted = 0
        try:
            import redis as _r
            r = _r.Redis(host="127.0.0.1", port=6379,
                        password=os.environ.get("REDIS_PASSWORD", ""),
                        decode_responses=True, socket_connect_timeout=3)

            for key in r.keys("trace:*:log"):
                try:
                    last_entry = r.lindex(key, -1)
                    if last_entry:
                        entry = json.loads(last_entry)
                        ts_str = entry.get("ts", "00:00:00.000")
                        # 简单判断: 如果key没有新的写入,算过期
                        ttl = r.ttl(key)
                        if ttl < 0:  # 没有设置过期时间
                            r.expire(key, self.config["trace_max_age_sec"])
                            deleted += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted

    def collect_progress(self) -> int:
        """清理过期的 Neuron 进度 key"""
        deleted = 0
        try:
            import redis as _r
            r = _r.Redis(host="127.0.0.1", port=6379,
                        password=os.environ.get("REDIS_PASSWORD", ""),
                        decode_responses=True, socket_connect_timeout=3)

            for key in r.keys("agent:*:*:state"):
                try:
                    data = json.loads(r.get(key) or "{}")
                    ts = data.get("ts", 0)
                    if time.time() - ts > self.config["progress_max_age_sec"]:
                        if not self.config["dry_run"]:
                            r.delete(key)
                        deleted += 1
                except Exception:
                    pass
        except Exception:
            pass
        return deleted

    def collect_zombies(self):
        """清理僵尸进程"""
        try:
            import subprocess
            result = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=5)
            for line in result.stdout.split("\n"):
                if "defunct" in line and ("neuron" in line or "yaxiio" in line):
                    pid = line.split()[1]
                    try:
                        os.kill(int(pid), 9)
                    except Exception:
                        pass
        except Exception:
            pass

    def collect(self) -> dict:
        """执行一次完整 GC"""
        acked = self.collect_streams()
        tasks = self.collect_tasks()
        traces = self.collect_traces()
        progress = self.collect_progress()
        self.collect_zombies()

        self.stats["stream_acked"] += acked
        self.stats["tasks_deleted"] += tasks
        self.stats["traces_deleted"] += traces
        self.stats["progress_deleted"] += progress
        self.stats["last_run"] = time.time()
        self.stats["runs"] += 1

        # 写入 Redis 供 Dashboard 读取
        try:
            self.redis.set("yaxiio:gc:stats", json.dumps(self.stats, ensure_ascii=False))
        except Exception:
            pass

        if acked or tasks or traces or progress:
            print(f"[GC] 清理: stream={acked} tasks={tasks} traces={traces} progress={progress}", flush=True)

        return dict(self.stats)

    def _run_loop(self):
        """后台 GC 线程"""
        while self._running:
            try:
                self.collect()
            except Exception as e:
                print(f"[GC] 异常: {e}", flush=True)
            time.sleep(self.config["interval_sec"])

    def start(self):
        """启动后台 GC 线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print(f"[GC] 启动, 间隔={self.config['interval_sec']}s", flush=True)

    def stop(self):
        self._running = False
