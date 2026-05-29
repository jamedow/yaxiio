#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Pi Guardian — Commander 守护进程 v1.1
=====================================
守护 Commander 全生命周期：启动/监控/进化重启/故障自愈/CLI控制

功能:
  1. 进程监护 — 监控全栈进程存活，崩溃自动重启 (指数退避)
  2. 进化重启 — Redis Pub/Sub + evolution_status.json 双通道监听
  3. 健康检查 — 进程/Redis/API/Heartbeat 四重探活
  4. 故障自愈 — 已知错误模式匹配 + 自动修复脚本执行
  5. CLI 控制 — Unix Socket 命令行接口
  6. PM2 保活 — 可选注册到 PM2 实现外层守护

架构 (双层守护):
  外层: PM2 (可选) → 守护 Pi Guardian 进程
  内层: Pi Guardian → 守护 Commander 全栈进程
    ├── Supervisor: 进程管理 (fork/restart/kill)
    ├── HealthChecker: 定时探活
    ├── AutoFixer: 已知错误检测 + 自动修复
    ├── EvolutionListener: 进化信号双通道监听
    └── CommandServer: Unix Socket CLI

使用:
  python3 pi_guardian.py                        # 前台运行
  python3 pi_guardian.py --daemon               # 后台守护模式
  python3 pi_guardian.py status                 # CLI 手动控制
  python3 pi_guardian.py restart                # CLI 手动重启

CLI 控制 (进入容器后):
  echo "status"   | nc -U /tmp/pi-guardian.sock  # 查看状态
  echo "health"   | nc -U /tmp/pi-guardian.sock  # 完整健康检查
  echo "restart"  | nc -U /tmp/pi-guardian.sock  # 重启 Commander
  echo "reload"   | nc -U /tmp/pi-guardian.sock  # 热重载 (进化后)
  echo "evolve"   | nc -U /tmp/pi-guardian.sock  # 触发进化重启
  echo "stop"     | nc -U /tmp/pi-guardian.sock  # 停止 Commander
  echo "start"    | nc -U /tmp/pi-guardian.sock  # 启动 Commander
  echo "fix"      | nc -U /tmp/pi-guardian.sock  # 扫描并修复已知错误
  echo "logs 50"  | nc -U /tmp/pi-guardian.sock  # 查看最近 N 行日志
  echo "logs 50"  | nc -U /tmp/pi-guardian.sock  # 查看最近50行日志
  echo "health"   | nc -U /tmp/pi-guardian.sock  # 健康检查
  echo "help"     | nc -U /tmp/pi-guardian.sock  # 帮助

Constitution:
  R1 — 不直接操作 Redis/MongoDB 数据，只读健康状态
  R2 — 重启前等待 cleanup 完成（最多 30s）
  R3 — 连续崩溃 5 次进入冷却期（60s），避免无限重启循环
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# ── 可选依赖 ──
try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

SOCKET_PATH = "/tmp/pi-guardian.sock"
COMMANDER_SCRIPT = "/app/.pi/skills/commander/commander.py"
DASHBOARD_SCRIPT = "/app/.pi/agents/runtime/dashboard_v2.py"
HEARTBEAT_SCRIPT = "/app/.pi/skills/commander/heartbeat_manager.py"
WS_BRIDGE_SCRIPT = "/app/.pi/skills/commander/ws_bridge.py"

REDIS_HOST = os.environ.get("ENV_REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("ENV_REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("ENV_REDIS_PASSWORD", os.environ.get("REDIS_PASSWORD", ""))
MONGO_URI = os.environ.get("ENV_MONGO_URI", "mongodb://127.0.0.1:27017/")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_V2_PORT", "3003"))
DATA_DIR = os.environ.get("DATA_DIR", "/app/data")

HEALTH_CHECK_INTERVAL = 10       # 健康检查间隔 (秒)
RESTART_COOLDOWN = 60            # 连续崩溃冷却期 (秒)
MAX_CRASH_BEFORE_COOLDOWN = 5    # 进入冷却期的崩溃次数
RESTART_GRACE_PERIOD = 30        # 重启前等待 cleanup 的时间 (秒)
EVOLUTION_CHANNEL = "commander:evolution"
GUARDIAN_LOG = "/app/logs/pi-guardian.log"
EVOLUTION_STATUS_FILE = "/root/.pi/agent/evolution_status.json"
GUARDIAN_HEALTH_FILE = f"{DATA_DIR}/guardian-health.json"

# 已知错误模式 + 自动修复脚本 (姐妹方案精华)
KNOWN_ERROR_PATTERNS = [
    {
        "pattern": "ECONNREFUSED 127.0.0.1:6379",
        "fix_cmd": "redis-server /tmp/redis-commander.conf --daemonize yes 2>/dev/null",
        "description": "Redis 连接被拒绝 — 重启 Redis",
        "needs_restart": True,
    },
    {
        "pattern": "No API key found",
        "fix_cmd": "",  # 从环境变量自动注入，不需要额外命令
        "description": "API Key 丢失 — 从环境变量重新注入",
        "needs_restart": True,
    },
    {
        "pattern": "Cannot convert argument to a ByteString",
        "fix_cmd": "rm -f /root/.pi/agent/models.json",
        "description": "models.json 编码损坏 — 删除后重建",
        "needs_restart": True,
    },
    {
        "pattern": "ModuleNotFoundError",
        "fix_cmd": "pip3 install --break-system-packages {module} 2>/dev/null",
        "description": "Python 模块缺失 — 自动安装",
        "needs_restart": True,
    },
    {
        "pattern": "Address already in use",
        "fix_cmd": "fuser -k {port}/tcp 2>/dev/null",
        "description": "端口被占用 — 释放端口",
        "needs_restart": True,
    },
]


# ═══════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════

class Logger:
    def __init__(self, log_file=GUARDIAN_LOG):
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        self._file = open(log_file, "a", buffering=1)

    def log(self, level, msg):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        print(line, flush=True)
        try:
            self._file.write(line + "\n")
            self._file.flush()
        except Exception:
            pass

    def info(self, msg):   self.log("INFO", msg)
    def warn(self, msg):   self.log("WARN", msg)
    def error(self, msg):  self.log("ERROR", msg)
    def debug(self, msg):  self.log("DEBUG", msg)


logger = Logger()


# ═══════════════════════════════════════════════════════════════
# 进化文件持久化
# ═══════════════════════════════════════════════════════════════

def persist_evolution_files():
    """将容器内的进化文件同步到持久化目录。"""
    pairs = [
        ("/app/.pi/skills/commander/experience", f"{DATA_DIR}/experience"),
        ("/app/.pi/skills/commander_evolution", f"{DATA_DIR}/evolution"),
        ("/app/.pi/blackboard", f"{DATA_DIR}/blackboard"),
        ("/app/logs", f"{DATA_DIR}/logs"),
    ]
    for src, dst in pairs:
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            os.system(f"cp -r {src}/* {dst}/ 2>/dev/null")


def restore_evolution_files():
    """从持久化目录恢复进化文件到容器内。"""
    pairs = [
        (f"{DATA_DIR}/experience", "/app/.pi/skills/commander/experience"),
        (f"{DATA_DIR}/evolution", "/app/.pi/skills/commander_evolution"),
        (f"{DATA_DIR}/blackboard", "/app/.pi/blackboard"),
    ]
    for src, dst in pairs:
        if os.path.isdir(src) and os.listdir(src):
            os.makedirs(dst, exist_ok=True)
            os.system(f"cp -rn {src}/* {dst}/ 2>/dev/null")


# ═══════════════════════════════════════════════════════════════
# AutoFixer — 已知错误自动修复 (姐妹方案精华)
# ═══════════════════════════════════════════════════════════════

class AutoFixer:
    """检测日志中的已知错误模式并执行自动修复。"""

    def __init__(self, supervisor=None):
        self.supervisor = supervisor
        self.fix_history = []  # 记录修复历史，避免重复修复同一错误

    def scan_and_fix(self) -> list:
        """扫描日志，检测已知错误，执行修复。返回修复记录列表。"""
        results = []
        commander_logs = self._get_commander_logs()

        for error_def in KNOWN_ERROR_PATTERNS:
            pattern = error_def["pattern"]
            if pattern not in commander_logs:
                continue

            # 检查是否最近已修复过（1小时内不重复修复）
            if self._recently_fixed(pattern, 3600):
                continue

            logger.warn(f"🔍 检测到已知错误: {error_def['description']}")

            # 解析动态参数
            fix_cmd = error_def["fix_cmd"]
            if "{module}" in fix_cmd and "ModuleNotFoundError" in pattern:
                module = self._extract_missing_module(commander_logs)
                if module:
                    fix_cmd = fix_cmd.replace("{module}", module)
                else:
                    continue

            if "{port}" in fix_cmd and "Address already in use" in pattern:
                port = self._extract_port(commander_logs)
                if port:
                    fix_cmd = fix_cmd.replace("{port}", port)
                else:
                    continue

            # 执行修复
            if fix_cmd:
                logger.info(f"🔧 执行修复: {fix_cmd}")
                _, stderr, code = self._run(fix_cmd)
                if code == 0:
                    logger.info(f"✅ 修复成功: {error_def['description']}")
                else:
                    logger.warn(f"⚠️ 修复可能失败: {stderr[:100] if stderr else 'no error'}")

            self.fix_history.append({
                "pattern": pattern,
                "description": error_def["description"],
                "time": time.time(),
            })
            results.append(error_def)

            # 需要重启
            if error_def.get("needs_restart") and self.supervisor:
                logger.info("🔄 修复后重启 Commander...")
                time.sleep(2)
                self.supervisor.restart_commander_only()

        return results

    def _get_commander_logs(self) -> str:
        """获取 Commander 最近日志。"""
        try:
            # 尝试从 PM2 获取
            result = subprocess.run(
                "pm2 logs lightingmetal-pi --lines 20 --nostream 2>/dev/null",
                shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout + result.stderr
        except Exception:
            pass

        # Fallback: guardian 自身日志
        try:
            with open(GUARDIAN_LOG, "r") as f:
                lines = f.readlines()
                return "".join(lines[-50:])
        except Exception:
            return ""

    def _recently_fixed(self, pattern, seconds) -> bool:
        cutoff = time.time() - seconds
        return any(f["pattern"] == pattern and f["time"] > cutoff
                   for f in self.fix_history)

    def _extract_missing_module(self, logs) -> str:
        import re
        m = re.search(r"No module named '(\w+)'", logs)
        return m.group(1) if m else None

    def _extract_port(self, logs) -> str:
        import re
        m = re.search(r"Address already in use.*?:::(\d+)", logs)
        if not m:
            m = re.search(r"port[=:\s]*(\d+)", logs)
        return m.group(1) if m else None

    def _run(self, cmd):
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return result.stdout.strip(), result.stderr.strip(), result.returncode


# ═══════════════════════════════════════════════════════════════
# Supervisor — 进程管理
# ═══════════════════════════════════════════════════════════════

class Supervisor:
    """管理 Commander 子进程的启动/停止/重启。"""

    def __init__(self):
        self.commander_proc = None
        self.dashboard_proc = None
        self.heartbeat_proc = None
        self.ws_bridge_proc = None
        self.restart_count = 0
        self.last_restart_time = 0
        self.total_restarts = 0
        self.running = False

    def start_all(self):
        """启动所有服务进程。"""
        logger.info("🚀 启动 Commander 全栈服务...")

        restore_evolution_files()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        # Commander 主进程
        self.commander_proc = subprocess.Popen(
            ["python3", "-u", COMMANDER_SCRIPT],
            cwd="/app/.pi/skills/commander",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"   Commander PID={self.commander_proc.pid}")

        # Dashboard
        self.dashboard_proc = subprocess.Popen(
            ["python3", "-u", DASHBOARD_SCRIPT, MONGO_URI, str(DASHBOARD_PORT)],
            cwd="/app/.pi/agents/runtime",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"   Dashboard PID={self.dashboard_proc.pid} (port {DASHBOARD_PORT})")

        # Heartbeat HTTP 服务
        self.heartbeat_proc = subprocess.Popen(
            ["python3", "-u", HEARTBEAT_SCRIPT, "serve",
             "--redis-host", REDIS_HOST, "--redis-port", str(REDIS_PORT),
             "--redis-password", REDIS_PASSWORD, "--port", "3399"],
            cwd="/app/.pi/skills/commander",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"   Heartbeat PID={self.heartbeat_proc.pid} (port 3399)")

        # WebSocket 桥接
        self.ws_bridge_proc = subprocess.Popen(
            ["python3", "-u", WS_BRIDGE_SCRIPT],
            cwd="/app/.pi/skills/commander",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(f"   WS Bridge PID={self.ws_bridge_proc.pid} (port 3398)")

        self.running = True
        logger.info("✅ 全栈服务已启动")

    def stop_all(self, graceful=True):
        """停止所有服务进程。"""
        if not self.running:
            return

        logger.info("🛑 停止 Commander 全栈服务...")

        procs = [
            ("Commander", self.commander_proc),
            ("Dashboard", self.dashboard_proc),
            ("Heartbeat", self.heartbeat_proc),
            ("WS Bridge", self.ws_bridge_proc),
        ]

        if graceful:
            # 先 SIGTERM
            for name, proc in procs:
                if proc and proc.poll() is None:
                    logger.info(f"   SIGTERM → {name} (PID={proc.pid})")
                    proc.terminate()

            # 等待最多 RESTART_GRACE_PERIOD 秒
            deadline = time.time() + RESTART_GRACE_PERIOD
            for name, proc in procs:
                if proc and proc.poll() is None:
                    remaining = max(0, deadline - time.time())
                    try:
                        proc.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        logger.warn(f"   {name} 未响应 SIGTERM, 强制 SIGKILL")
                        proc.kill()
        else:
            for name, proc in procs:
                if proc and proc.poll() is None:
                    proc.kill()

        # 持久化进化文件
        persist_evolution_files()

        self.commander_proc = None
        self.dashboard_proc = None
        self.heartbeat_proc = None
        self.ws_bridge_proc = None
        self.running = False

        logger.info("✅ 全栈服务已停止")

    def restart_commander_only(self):
        """仅重启 Commander 主进程（Dashboard/Heartbeat/WS 保持运行）。"""
        logger.info("🔄 进化重启: 仅重启 Commander 主进程...")

        if self.commander_proc and self.commander_proc.poll() is None:
            self.commander_proc.terminate()
            try:
                self.commander_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.commander_proc.kill()

        persist_evolution_files()
        restore_evolution_files()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        self.commander_proc = subprocess.Popen(
            ["python3", "-u", COMMANDER_SCRIPT],
            cwd="/app/.pi/skills/commander",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        self.restart_count = 0
        self.last_restart_time = time.time()
        self.total_restarts += 1
        logger.info(f"✅ Commander 已重启 (PID={self.commander_proc.pid}, 总重启#{self.total_restarts})")

    def restart_all(self):
        """完全重启所有服务。"""
        self.stop_all(graceful=True)
        time.sleep(2)
        self.start_all()
        self.restart_count = 0
        self.last_restart_time = time.time()
        self.total_restarts += 1
        logger.info(f"✅ 全栈服务已重启 (总重启#{self.total_restarts})")

    def auto_recover(self):
        """Commander 崩溃后的自动恢复。"""
        self.restart_count += 1
        self.total_restarts += 1

        if self.restart_count >= MAX_CRASH_BEFORE_COOLDOWN:
            cooldown = RESTART_COOLDOWN
            logger.error(f"⚠️ Commander 连续崩溃 {self.restart_count} 次，进入 {cooldown}s 冷却期")
            time.sleep(cooldown)
            self.restart_count = 0

        logger.warn(f"🔄 自动恢复: 重启 Commander (崩溃#{self.restart_count}, 总#{self.total_restarts})")
        self.stop_all(graceful=False)
        time.sleep(3)
        self.start_all()

    def is_commander_alive(self):
        """检查 Commander 主进程是否存活。"""
        return self.commander_proc is not None and self.commander_proc.poll() is None

    def is_healthy(self):
        """检查所有进程是否存活。"""
        for proc in [self.commander_proc, self.dashboard_proc,
                      self.heartbeat_proc, self.ws_bridge_proc]:
            if proc is None or proc.poll() is not None:
                return False
        return True

    def get_status(self):
        """获取进程状态。"""
        def _status(proc):
            if proc is None:
                return "stopped"
            poll = proc.poll()
            if poll is None:
                return "running"
            return f"exited({poll})"

        return {
            "commander": {"pid": self.commander_proc.pid if self.commander_proc else None,
                          "status": _status(self.commander_proc)},
            "dashboard": {"pid": self.dashboard_proc.pid if self.dashboard_proc else None,
                          "status": _status(self.dashboard_proc)},
            "heartbeat": {"pid": self.heartbeat_proc.pid if self.heartbeat_proc else None,
                          "status": _status(self.heartbeat_proc)},
            "ws_bridge": {"pid": self.ws_bridge_proc.pid if self.ws_bridge_proc else None,
                          "status": _status(self.ws_bridge_proc)},
            "total_restarts": self.total_restarts,
            "crash_count": self.restart_count,
        }


# ═══════════════════════════════════════════════════════════════
# HealthChecker — 健康探活
# ═══════════════════════════════════════════════════════════════

class HealthChecker:
    """定期检查 Commander 各组件健康状态。"""

    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor

    def check(self) -> dict:
        """执行一次完整健康检查。"""
        result = {
            "timestamp": datetime.now().isoformat(),
            "healthy": True,
            "checks": {},
        }

        # 1. 进程存活
        proc_alive = self.supervisor.is_commander_alive()
        result["checks"]["process"] = proc_alive
        if not proc_alive:
            result["healthy"] = False

        # 2. Redis 连通
        redis_ok = self._check_redis()
        result["checks"]["redis"] = redis_ok

        # 3. Dashboard HTTP
        dash_ok = self._check_http(f"http://127.0.0.1:{DASHBOARD_PORT}/dashboard")
        result["checks"]["dashboard"] = dash_ok

        # 4. Heartbeat API
        hb_ok = self._check_http("http://127.0.0.1:3399/heartbeat/status")
        result["checks"]["heartbeat_api"] = hb_ok

        return result

    def _check_redis(self) -> bool:
        if not HAS_REDIS:
            return None  # 未安装，跳过
        try:
            r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                password=REDIS_PASSWORD,
                                socket_connect_timeout=3)
            r.ping()
            r.close()
            return True
        except Exception:
            return False

    def _check_http(self, url) -> bool:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status in (200, 302, 301)
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════
# EvolutionListener — 进化信号监听
# ═══════════════════════════════════════════════════════════════

class EvolutionListener:
    """双通道监听进化信号：Redis Pub/Sub + evolution_status.json 文件。"""

    def __init__(self, supervisor: Supervisor):
        self.supervisor = supervisor
        self._stop_event = threading.Event()
        self._last_file_check = 0

    def start(self):
        """启动后台监听线程。"""
        if HAS_REDIS:
            t = threading.Thread(target=self._listen_redis, daemon=True)
            t.start()
            logger.info(f"👂 进化监听已启动 (Redis: {EVOLUTION_CHANNEL})")
        else:
            logger.warn("Redis 不可用，仅文件监听模式")
        t = threading.Thread(target=self._listen_file, daemon=True)
        t.start()
        logger.info(f"👂 进化监听已启动 (File: {EVOLUTION_STATUS_FILE})")

    def stop(self):
        self._stop_event.set()

    def _listen_redis(self):
        while not self._stop_event.is_set():
            try:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                    password=REDIS_PASSWORD,
                                    socket_connect_timeout=5,
                                    socket_keepalive=True)
                pubsub = r.pubsub()
                pubsub.subscribe(EVOLUTION_CHANNEL)

                while not self._stop_event.is_set():
                    msg = pubsub.get_message(timeout=1.0)
                    if msg and msg.get("type") == "message":
                        self._handle_evolution_signal(msg["data"])
            except Exception as e:
                logger.warn(f"Redis进化监听重连: {e}")
                time.sleep(5)

    def _listen_file(self):
        while not self._stop_event.is_set():
            time.sleep(30)
            try:
                if os.path.exists(EVOLUTION_STATUS_FILE):
                    mtime = os.path.getmtime(EVOLUTION_STATUS_FILE)
                    if mtime > self._last_file_check:
                        self._last_file_check = mtime
                        with open(EVOLUTION_STATUS_FILE, "r") as f:
                            data = json.load(f)
                        if data.get("restart_required", False):
                            logger.info(f"🧬 文件信号: 进化完成需重启")
                            time.sleep(2)
                            self.supervisor.restart_commander_only()
                            os.remove(EVOLUTION_STATUS_FILE)
                            logger.info("✅ 进化重启完成，已清除信号文件")
            except Exception as e:
                pass

    def _handle_evolution_signal(self, data_str):
        """处理进化信号。"""
        try:
            signal_data = json.loads(data_str)
        except json.JSONDecodeError:
            signal_data = {"raw": data_str}

        action = signal_data.get("action", "")
        agent = signal_data.get("from", signal_data.get("agent_id", "unknown"))

        logger.info(f"🧬 收到进化信号: action={action} from={agent}")

        if action in ("evolve", "evolution_complete", "restart_required"):
            logger.info(f"🔄 触发进化重启 (原因: {action})...")
            # 短暂延迟，确保进化数据已写入
            time.sleep(2)
            self.supervisor.restart_commander_only()
            logger.info("✅ 进化重启完成")

        elif action == "full_restart":
            logger.info("🔄 触发完整重启...")
            time.sleep(2)
            self.supervisor.restart_all()
            logger.info("✅ 完整重启完成")

        else:
            logger.info(f"   未识别的进化信号: {action}, 忽略")


# ═══════════════════════════════════════════════════════════════
# CommandServer — Unix Socket CLI
# ═══════════════════════════════════════════════════════════════

class CommandServer:
    """Unix Socket 命令行接口，用于容器内控制 Guardian/Commander。"""

    def __init__(self, supervisor: Supervisor, health_checker: HealthChecker,
                 auto_fixer: AutoFixer = None):
        self.supervisor = supervisor
        self.health_checker = health_checker
        self.auto_fixer = auto_fixer

    def start(self):
        """启动后台监听线程。"""
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        t = threading.Thread(target=self._serve, daemon=True)
        t.start()
        logger.info(f"💬 CLI 已启动: nc -U {SOCKET_PATH}")

    def _serve(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(SOCKET_PATH)
        sock.listen(5)
        os.chmod(SOCKET_PATH, 0o666)

        while True:
            try:
                conn, _ = sock.accept()
                threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()
            except Exception as e:
                logger.error(f"CLI accept 异常: {e}")

    def _handle_client(self, conn):
        try:
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break

            cmd_line = data.decode("utf-8", errors="replace").strip()
            if not cmd_line:
                conn.close()
                return

            parts = cmd_line.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            logger.info(f"💬 CLI 命令: {cmd} {arg}")
            response = self._execute(cmd, arg)
            conn.sendall((response + "\n").encode("utf-8"))
        except Exception as e:
            try:
                conn.sendall(f"ERROR: {e}\n".encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def _execute(self, cmd, arg):
        if cmd == "status":
            return self._cmd_status()

        elif cmd == "health":
            return self._cmd_health()

        elif cmd == "restart":
            return self._cmd_restart(arg)

        elif cmd == "evolve":
            return self._cmd_evolve()

        elif cmd == "stop":
            return self._cmd_stop()

        elif cmd == "start":
            return self._cmd_start()

        elif cmd == "logs":
            return self._cmd_logs(arg)

        elif cmd == "help":
            return self._cmd_help()

        elif cmd == "reload":
            return self._cmd_reload()

        elif cmd == "fix":
            return self._cmd_fix()

        elif cmd == "ping":
            return "PONG"

        else:
            return f"未知命令: {cmd}\n输入 'help' 查看可用命令"

    def _cmd_status(self):
        status = self.supervisor.get_status()
        return json.dumps(status, indent=2, ensure_ascii=False)

    def _cmd_health(self):
        result = self.health_checker.check()
        return json.dumps(result, indent=2, ensure_ascii=False)

    def _cmd_restart(self, arg):
        if arg == "all" or arg == "":
            threading.Thread(target=self.supervisor.restart_all, daemon=True).start()
        else:
            threading.Thread(target=self.supervisor.restart_commander_only, daemon=True).start()
        return "已触发重启，请稍后检查状态"

    def _cmd_evolve(self):
        # 触发 Commander 的进化流程
        if HAS_REDIS:
            try:
                r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                    password=REDIS_PASSWORD,
                                    socket_connect_timeout=3)
                r.publish(EVOLUTION_CHANNEL, json.dumps({
                    "action": "evolve",
                    "from": "pi-guardian",
                    "reason": "manual_trigger",
                    "timestamp": time.time(),
                }))
                r.close()
                return "已发送进化信号，Commander 收到后将执行进化并重启"
            except Exception as e:
                return f"发送进化信号失败: {e}"
        return "Redis 不可用"

    def _cmd_stop(self):
        threading.Thread(target=self.supervisor.stop_all, daemon=True).start()
        return "已停止所有服务"

    def _cmd_start(self):
        if self.supervisor.running:
            return "服务已在运行中"
        threading.Thread(target=self.supervisor.start_all, daemon=True).start()
        return "已启动所有服务"

    def _cmd_reload(self):
        """仅重启 Commander 主进程（保留 Dashboard/Heartbeat/WS）"""
        threading.Thread(target=self.supervisor.restart_commander_only, daemon=True).start()
        return "已触发 Commander 热重载"

    def _cmd_logs(self, arg):
        lines = 20
        if arg and arg.isdigit():
            lines = int(arg)
        try:
            with open(GUARDIAN_LOG, "r") as f:
                all_lines = f.readlines()
                return "".join(all_lines[-lines:])
        except Exception as e:
            return f"读取日志失败: {e}"

    def _cmd_fix(self):
        if not self.auto_fixer:
            return "AutoFixer 未初始化"
        fixes = self.auto_fixer.scan_and_fix()
        if fixes:
            return "已修复以下错误:\n" + "\n".join(
                f"  - {f['description']}" for f in fixes)
        return "未发现已知错误"

    def _cmd_help(self):
        return """
╔══════════════════════════════════════════════════════════╗
║       Pi Guardian v1.1 — Commander 守护进程 CLI        ║
╠══════════════════════════════════════════════════════════╣
║  status      查看所有服务进程状态                        ║
║  health      完整健康检查 (进程/Redis/API/Heartbeat)    ║
║  restart     热重载 Commander 主进程                     ║
║  restart all 重启全部服务                                ║
║  reload      热重载 (进化后使用)                         ║
║  evolve      手动触发进化重启                            ║
║  fix          扫描日志并自动修复已知错误                  ║
║  stop        停止所有服务                                ║
║  start       启动所有服务                                ║
║  logs [N]    查看最近 N 行日志                           ║
║  ping        连通性测试                                  ║
║  help        显示此帮助                                  ║
╠══════════════════════════════════════════════════════════╣
║  使用: echo "status" | nc -U /tmp/pi-guardian.sock     ║
╚══════════════════════════════════════════════════════════╝
"""


# ═══════════════════════════════════════════════════════════════
# Guardian 主循环
# ═══════════════════════════════════════════════════════════════

class PiGuardian:
    """守护进程主控制器。"""

    def __init__(self):
        self.supervisor = Supervisor()
        self.health_checker = HealthChecker(self.supervisor)
        self.auto_fixer = AutoFixer(self.supervisor)
        self.evolution_listener = EvolutionListener(self.supervisor)
        self.command_server = CommandServer(self.supervisor, self.health_checker, self.auto_fixer)
        self._stop_event = threading.Event()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"收到信号 {signum}，准备关闭...")
        self._stop_event.set()

    def run(self):
        """启动守护进程主循环。"""
        logger.info("╔═══════════════════════════════════════════════╗")
        logger.info("║  🛡️  Pi Guardian v1.0 — Commander 守护进程  ║")
        logger.info("╚═══════════════════════════════════════════════╝")
        logger.info(f"   Socket: {SOCKET_PATH}")
        logger.info(f"   日志:   {GUARDIAN_LOG}")

        # 1. 启动所有服务
        self.supervisor.start_all()

        # 2. 启动进化监听
        self.evolution_listener.start()

        # 3. 启动 CLI
        self.command_server.start()

        # 4. 主循环: 健康检查 + 故障恢复
        last_health_log = 0
        while not self._stop_event.is_set():
            time.sleep(HEALTH_CHECK_INTERVAL)

            # 健康检查
            health = self.health_checker.check()

            # 定期输出健康日志（每5次即50秒一次）
            if time.time() - last_health_log > 50:
                checks = health["checks"]
                logger.info(f"💚 健康: process={checks['process']} redis={checks['redis']} "
                            f"dashboard={checks['dashboard']} heartbeat={checks['heartbeat_api']}")
                last_health_log = time.time()

            # 故障恢复: Commander 进程挂了
            if not health["checks"]["process"]:
                logger.error("❌ Commander 进程已终止，触发自动恢复...")
                self.supervisor.auto_recover()

            # 自动修复: 扫描日志中的已知错误
            if not health["healthy"]:
                fixes = self.auto_fixer.scan_and_fix()
                for fix in fixes:
                    logger.info(f"🔧 自动修复: {fix['description']}")

        # 关闭
        logger.info("Pi Guardian 关闭中...")
        self.evolution_listener.stop()
        self.supervisor.stop_all(graceful=True)
        logger.info("🛡️  Pi Guardian 已退出")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    daemon_mode = "--daemon" in sys.argv

    if daemon_mode:
        # 后台守护模式
        pid = os.fork()
        if pid > 0:
            print(f"Pi Guardian 已后台启动 (PID={pid})")
            sys.exit(0)
        os.setsid()
        # 重定向标准流
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")

    guardian = PiGuardian()
    try:
        guardian.run()
    except Exception as e:
        logger.error(f"Guardian 异常退出: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
