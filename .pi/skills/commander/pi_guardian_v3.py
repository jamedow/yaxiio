#!/usr/bin/env python3
"""
雅溪 Yaxiio — Commander Guard v3.0 (AI守护者)
================================================
设计原则:
  - PM2 只守护 Guard 进程本身
  - Guard 守护 Commander 进程 (spawn/healthcheck/诊断/修复/重启)
  - 不守护 Agent 子进程
  - 不监控系统资源 (CPU/内存/磁盘)
  - 不处理网络问题

职责:
  1. 每30秒健康检查: 进程存活 + Redis可连 + API响应
  2. 故障诊断: 读日志 → 分类 (Redis/models.json/API Key/未知)
  3. 自动修复: 按故障类型执行对应修复脚本
  4. 重启限制: 2分钟内最多3次 → 超限暂停等待人工
  5. 日志记录: 所有操作写入 /opt/commander/guard.log

配置环境变量:
  REDIS_HOST, REDIS_PORT, REDIS_PASSWORD
  DEEPSEEK_API_KEY, LLM_BASE_URL, LLM_MODEL
  HEALTH_PORT (默认 3003)
  GUARD_LOG_DIR (默认 /opt/commander)
"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "$REDIS_PASSWORD")
LLM_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "3401"))  # MCP L1 端口

# Architecture note: Guardian uses subprocess directly because
# process lifecycle management (spawn/kill/restart) is a system-level
# capability, not a business operation. This does NOT violate L1-L5
# layer isolation — the Guardian is infrastructure, not orchestration.

LOG_DIR = Path(os.environ.get("GUARD_LOG_DIR", "/opt/commander"))
LOG_FILE = LOG_DIR / "guard.log"

COMMANDER_SCRIPT = os.environ.get("COMMANDER_SCRIPT", os.path.join(os.environ.get("YAXIIO_HOME", "/opt/commander"), "yaxiio.py"))
COMMANDER_PID_FILE = os.environ.get("COMMANDER_PID_FILE", "/tmp/yaxiio-commander.pid")
COMMANDER_ERROR_LOG = os.environ.get("COMMANDER_ERROR_LOG", "/root/.pm2/logs/yaxiio-core-error.log")

# 备份文件路径
MODELS_BACKUP = "/app/.pi/agent/models.json.backup"
MODELS_FILE = "/app/.pi/agent/models.json"

# 速率限制
MAX_RESTARTS = 3          # 最大重启次数
RATE_WINDOW = 120         # 时间窗口 (秒)
HEALTH_INTERVAL = 30      # 健康检查间隔 (秒)

# ═══════════════════════════════════════════════════════════════
# 日志
# ═══════════════════════════════════════════════════════════════

def log(msg: str, level: str = "INFO"):
    """写入日志文件 + stdout"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[Guard] 日志写入失败: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════
# 速率限制器
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """跟踪 Commander 重启次数，2分钟内超过3次则暂停"""

    def __init__(self, max_restarts: int = MAX_RESTARTS, window: int = RATE_WINDOW):
        self.max_restarts = max_restarts
        self.window = window
        self.restart_times: list = []
        self.paused = False

    def record_restart(self) -> bool:
        """记录一次重启，返回是否允许"""
        now = time.time()
        self.restart_times = [t for t in self.restart_times if now - t < self.window]
        self.restart_times.append(now)

        if len(self.restart_times) > self.max_restarts:
            if not self.paused:
                self.paused = True
                log(f"⛔ 速率限制触发: {self.window}s 内重启 {len(self.restart_times)} 次，暂停自动修复，等待人工介入", "CRITICAL")
            return False
        return True

    def reset(self):
        if self.paused:
            log("🔄 速率限制已重置")
        self.restart_times = []
        self.paused = False


# ═══════════════════════════════════════════════════════════════
# 健康检查
# ═══════════════════════════════════════════════════════════════

class HealthChecker:
    """三层健康检查: 进程 + Redis + API"""

    @staticmethod
    def check_process() -> Tuple[bool, str]:
        """检查 Commander 进程是否存活"""
        # 方式1: pgrep (优先，排除僵尸和pm2命令行)
        try:
            result = subprocess.run(
                ["pgrep", "-f", "yaxiio.py"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line.strip() and "pm2 start" not in line:
                        pid = line.strip()
                        # 确认不是僵尸
                        try:
                            with open(f"/proc/{pid}/status") as sf:
                                if "State:\tZ" not in sf.read():
                                    return True, f"pgrep PID {pid} running"
                        except:
                            pass
        except Exception:
            pass

        # 方式2: PID 文件 (辅助)
        try:
            if Path(COMMANDER_PID_FILE).exists():
                pid = int(Path(COMMANDER_PID_FILE).read_text().strip())
                os.kill(pid, 0)
                # 僵尸检测
                try:
                    with open(f"/proc/{pid}/status") as sf:
                        if "State:\tZ" in sf.read():
                            log(f"PID {pid} is zombie, cleaning up", "WARN")
                            Path(COMMANDER_PID_FILE).unlink(missing_ok=True)
                            return False, "process is zombie"
                except:
                    pass
                return True, f"PID file {pid} alive"
        except (OSError, ValueError):
            Path(COMMANDER_PID_FILE).unlink(missing_ok=True)

        # 方式2: pgrep yaxiio
        try:
            result = subprocess.run(
                ["pgrep", "-f", "yaxiio.py"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                pid = result.stdout.strip().split("\n")[0]
                return True, f"pgrep found PID {pid}"
        except Exception:
            pass

        return False, "process not found"

    @staticmethod
    def check_redis() -> Tuple[bool, str]:
        """检查 Redis 是否可连接"""
        try:
            import redis as redis_lib
            r = redis_lib.Redis(protocol=2, 
                host=REDIS_HOST, port=REDIS_PORT,
                password=REDIS_PASS or None,
                decode_responses=True, socket_connect_timeout=3
            )
            r.ping()
            r.close()
            return True, "PONG"
        except Exception as e:
            return False, str(e)[:100]

    @staticmethod
    def check_api() -> Tuple[bool, str]:
        """检查 MCP L1 Perception 服务是否响应"""
        try:
            result = subprocess.run(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"http://127.0.0.1:{HEALTH_PORT}/",
                 "--connect-timeout", "5", "--max-time", "5"],
                capture_output=True, text=True, timeout=10
            )
            code = result.stdout.strip()
            if code in ("200", "404", "405"):  # MCP 服务响应即正常
                return True, f"MCP L1 HTTP {code}"
            return False, f"HTTP {code}"
        except Exception as e:
            return False, str(e)[:100]

    @classmethod
    def full_check(cls) -> dict:
        """执行完整健康检查，返回结果字典"""
        proc_ok, proc_msg = cls.check_process()
        redis_ok, redis_msg = cls.check_redis()
        api_ok, api_msg = cls.check_api()

        all_ok = proc_ok and redis_ok and api_ok

        result = {
            "healthy": all_ok,
            "checks": {
                "process": {"ok": proc_ok, "detail": proc_msg},
                "redis": {"ok": redis_ok, "detail": redis_msg},
                "api": {"ok": api_ok, "detail": api_msg},
            }
        }
        return result


# ═══════════════════════════════════════════════════════════════
# 故障诊断
# ═══════════════════════════════════════════════════════════════

class FaultDiagnoser:
    """读取日志，分类故障原因"""

    FAULT_REDIS = "redis_disconnected"
    FAULT_MODELS = "models_corrupted"
    FAULT_APIKEY = "api_key_missing"
    FAULT_UNKNOWN = "unknown"

    @staticmethod
    def _read_error_log(lines: int = 50) -> str:
        """读取 Commander 的错误日志"""
        try:
            path = Path(COMMANDER_ERROR_LOG)
            if path.exists():
                content = path.read_text()
                recent = content.split("\n")[-lines:]
                return "\n".join(recent)
        except Exception:
            pass
        return ""

    @classmethod
    def diagnose(cls, health: dict) -> Tuple[str, str]:
        """
        根据健康检查结果 + 错误日志，诊断故障类型
        返回: (fault_type, detail)
        """
        # 收集症状
        proc_ok = health["checks"]["process"]["ok"]
        redis_ok = health["checks"]["redis"]["ok"]
        api_ok = health["checks"]["api"]["ok"]
        error_log = cls._read_error_log()

        # ── Redis 断连 ──
        if not redis_ok:
            return cls.FAULT_REDIS, (
                f"Redis连接失败: {health['checks']['redis']['detail']}"
            )

        # ── 进程不在 ──
        if not proc_ok and redis_ok:
            # 分析日志找具体原因
            if "models.json" in error_log.lower() or "models" in error_log.lower():
                return cls.FAULT_MODELS, "检测到 models.json 相关错误，可能文件损坏"
            if "api_key" in error_log.lower() or "unauthorized" in error_log.lower() or "401" in error_log:
                return cls.FAULT_APIKEY, "检测到 API Key 相关错误"
            if error_log:
                return cls.FAULT_UNKNOWN, f"进程退出，错误日志: {error_log[:200]}"
            return cls.FAULT_UNKNOWN, "进程不在但无明显错误日志"

        # ── 进程在但 API 无响应 ──
        if proc_ok and not api_ok and redis_ok:
            return cls.FAULT_UNKNOWN, (
                f"进程存活但API无响应: {health['checks']['api']['detail']}"
            )

        # ── 全好？不应该到这里 ──
        return cls.FAULT_UNKNOWN, "健康检查失败但无法定位具体原因"


# ═══════════════════════════════════════════════════════════════
# 自动修复
# ═══════════════════════════════════════════════════════════════

class AutoRepair:
    """按故障类型执行对应修复"""

    @staticmethod
    def repair_redis() -> bool:
        """修复 Redis: 尝试启动"""
        log("🔧 [修复] 尝试重启 Redis", "ACTION")
        try:
            subprocess.run(
                ["redis-server", "--daemonize", "yes",
                 "--bind", "127.0.0.1", "--dir", "/data",
                 "--requirepass", REDIS_PASS,
                 "--maxmemory", "256mb"],
                capture_output=True, timeout=10
            )
            time.sleep(2)
            # 验证
            import redis as redis_lib
            r = redis_lib.Redis(protocol=2, 
                host=REDIS_HOST, port=REDIS_PORT,
                password=REDIS_PASS or None,
                decode_responses=True, socket_connect_timeout=3
            )
            r.ping()
            r.close()
            log("✅ Redis 已恢复", "ACTION")
            return True
        except Exception as e:
            log(f"❌ Redis 修复失败: {e}", "ERROR")
            return False

    @staticmethod
    def repair_models() -> bool:
        """修复 models.json: 从备份恢复"""
        log("🔧 [修复] 尝试恢复 models.json", "ACTION")
        backup = Path(MODELS_BACKUP)
        target = Path(MODELS_FILE)

        if not backup.exists():
            log("❌ models.json 备份不存在", "ERROR")
            # 尝试创建默认配置
            default = json.dumps({
                "models": [{
                    "name": LLM_MODEL,
                    "provider": "deepseek",
                    "api_key": LLM_KEY,
                    "base_url": LLM_URL
                }]
            }, indent=2)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(default)
                log("✅ models.json 已从默认值重建", "ACTION")
                return True
            except Exception as e:
                log(f"❌ models.json 重建失败: {e}", "ERROR")
                return False

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(backup.read_text())
            log("✅ models.json 已从备份恢复", "ACTION")
            return True
        except Exception as e:
            log(f"❌ models.json 恢复失败: {e}", "ERROR")
            return False

    @staticmethod
    def repair_apikey() -> bool:
        """修复 API Key: 从环境变量重新注入"""
        log("🔧 [修复] 重新注入 API Key", "ACTION")
        if not LLM_KEY:
            log("❌ 环境变量 DEEPSEEK_API_KEY 为空，无法修复", "ERROR")
            return False

        # 更新 models.json 中的 API Key
        try:
            target = Path(MODELS_FILE)
            if target.exists():
                config = json.loads(target.read_text())
                for model in config.get("models", []):
                    model["api_key"] = LLM_KEY
                target.write_text(json.dumps(config, indent=2))
                log("✅ API Key 已重新注入到 models.json", "ACTION")
                return True
        except Exception as e:
            log(f"❌ API Key 注入失败: {e}", "ERROR")
            return False

    @classmethod
    def repair(cls, fault_type: str) -> bool:
        """根据故障类型执行修复"""
        repair_map = {
            FaultDiagnoser.FAULT_REDIS: cls.repair_redis,
            FaultDiagnoser.FAULT_MODELS: cls.repair_models,
            FaultDiagnoser.FAULT_APIKEY: cls.repair_apikey,
        }

        repair_fn = repair_map.get(fault_type)
        if repair_fn:
            return repair_fn()

        # 未知故障: 不自动修复
        log(f"⚠️ 未知故障类型 '{fault_type}'，不执行自动修复", "WARN")
        return False


# ═══════════════════════════════════════════════════════════════
# Commander 进程管理
# ═══════════════════════════════════════════════════════════════

class CommanderManager:
    """管理 Commander 进程的启动和停止"""

    comm_proc: Optional[subprocess.Popen] = None

    COMMANDER_BACKUP = os.environ.get("COMMANDER_BACKUP", "/tmp/yaxiio.py.bak")

    @classmethod
    def _launch(cls, script_path: str) -> bool:
        """启动指定脚本作为 Commander"""
        cls.comm_proc = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
            cwd=os.path.dirname(script_path),
        )
        def _read_commander_output():
            for line in iter(cls.comm_proc.stdout.readline, b""):
                text = line.decode(errors="replace").rstrip()
                if text:
                    log(f"[Commander] {text}", "CMD")
        import threading
        t = threading.Thread(target=_read_commander_output, daemon=True)
        t.start()
        Path(COMMANDER_PID_FILE).write_text(str(cls.comm_proc.pid))
        return True

    @classmethod
    def start(cls) -> bool:
        """启动 Commander: 现有文件 → 修复 → 备份文件"""
        if cls.comm_proc and cls.comm_proc.poll() is None:
            log("Commander 已在运行中")
            return True

        # Step 1: 用现有文件启动
        log("🚀 启动 Commander (现有文件)...", "ACTION")
        try:
            if cls._launch(COMMANDER_SCRIPT):
                time.sleep(3)
                if cls.comm_proc.poll() is None:
                    log(f"✅ Commander 已启动 (PID: {cls.comm_proc.pid})", "ACTION")
                    return True
                else:
                    exit_code = cls.comm_proc.returncode
                    log(f"⚠️ Commander 启动后立即退出 (exit={exit_code})，尝试修复...", "WARN")
        except Exception as e:
            log(f"⚠️ 现有文件启动失败: {e}，尝试修复...", "WARN")

        # Step 2: 尝试修复
        cls.stop()
        log("🔧 尝试自动修复 Commander...", "ACTION")
        # 检查是否有语法错误
        try:
            with open(COMMANDER_SCRIPT) as f:
                compile(f.read(), COMMANDER_SCRIPT, "exec")
            log("  Python语法检查通过")
        except SyntaxError as se:
            log(f"  ⚠️ 语法错误: {se}", "ERROR")
        
        # 再试一次现有文件
        try:
            if cls._launch(COMMANDER_SCRIPT):
                time.sleep(3)
                if cls.comm_proc.poll() is None:
                    log("✅ Commander 修复后启动成功", "ACTION")
                    return True
        except Exception:
            pass

        # Step 3: 用备份文件兜底
        cls.stop()
        if os.path.exists(cls.COMMANDER_BACKUP):
            log("🔄 现有文件无法启动，使用备份文件...", "WARN")
            try:
                if cls._launch(cls.COMMANDER_BACKUP):
                    time.sleep(3)
                    if cls.comm_proc.poll() is None:
                        log("✅ Commander 已从备份启动 (请检查现有文件)", "ACTION")
                        return True
            except Exception as e:
                log(f"❌ 备份文件也无法启动: {e}", "ERROR")
        else:
            log("❌ 备份文件不存在，无法恢复", "ERROR")

        return False

    @classmethod
    def stop(cls):
        """停止 Commander 进程"""
        if cls.comm_proc:
            log("🛑 停止 Commander...", "ACTION")
            try:
                cls.comm_proc.terminate()
                cls.comm_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.comm_proc.kill()
            except Exception as e:
                log(f"Commander 停止异常: {e}", "WARN")
            cls.comm_proc = None

        # 清理 PID 文件
        try:
            Path(COMMANDER_PID_FILE).unlink(missing_ok=True)
        except Exception:
            pass

    @classmethod
    def restart(cls) -> bool:
        """重启 Commander"""
        cls.stop()
        time.sleep(2)
        return cls.start()


# ═══════════════════════════════════════════════════════════════
# 主循环
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# Commander 评分器 (Guardian 对 Commander 的元监督)
# ═══════════════════════════════════════════════════════════════

class CommanderScorer:
    """Guardian 定期对 Commander 的调度质量打分，低分触发进化"""

    EVAL_INTERVAL = 10  # 每 10 次健康检查后评估一次
    MIN_SAMPLE = 5      # 最少需要 5 个任务样本
    LOW_SCORE = 5       # 低于此分触发修复
    CRITICAL_SCORE = 3  # 低于此分触发回滚

    def __init__(self):
        self.cycle = 0
        self.low_score_streak = 0

    def evaluate(self) -> dict:
        """从 Redis 拉取最近任务的 L5 评分，计算 Commander 调度质量"""
        self.cycle += 1
        if self.cycle % self.EVAL_INTERVAL != 0:
            return {"evaluated": False}

        try:
            import redis as _r
            r = _r.Redis(protocol=2, host=REDIS_HOST, port=REDIS_PORT,
                        password=REDIS_PASS, decode_responses=True,
                        socket_connect_timeout=3)

            task_keys = r.smembers("yaxiio:task:active") or []
            tasks = []
            for key in list(task_keys)[:30]:
                raw = r.get(f"yaxiio:task:{key}")
                if raw:
                    try:
                        tasks.append(json.loads(raw))
                    except:
                        pass

            if len(tasks) < self.MIN_SAMPLE:
                return {"evaluated": True, "score": None, "reason": "insufficient samples"}

            # 计算四维分数
            scores = []
            agent_usage = set()
            for task in tasks:
                l5 = task.get("milestones", {}).get("L5_evaluation", {})
                if l5.get("status") == "done":
                    result = task.get("result", {})
                    if isinstance(result, dict):
                        l5_result = result.get("l5_result", {})
                        if isinstance(l5_result, dict):
                            scores.append(l5_result.get("overall", 5))
                for st in task.get("subtasks", {}).values():
                    agent_usage.add(st.get("agent", ""))

            if not scores:
                return {"evaluated": True, "score": 5, "reason": "no L5 scores available"}

            avg_score = sum(scores) / len(scores)
            utilization = len(agent_usage) / max(1, len([a for a in ["翻译官","审计官","UI/UX设计师","品牌策略师","前端工程师","售前经理","商务经理"] if a in str(tasks)]))

            result = {
                "evaluated": True,
                "overall": round(avg_score, 1),
                "samples": len(scores),
                "agent_utilization": round(utilization, 2),
                "routing_quality": round(avg_score, 1),
            }

            # ── 评分过低触发渐进式修复 ──
            if avg_score < self.CRITICAL_SCORE:
                self.low_score_streak += 1
                result["action"] = "critical"
                result["action_detail"] = f"连续 {self.low_score_streak} 次低分, 建议回滚"
            elif avg_score < self.LOW_SCORE:
                self.low_score_streak += 1
                result["action"] = "evolve_prompt"
                result["action_detail"] = f"连续 {self.low_score_streak} 次低分, 建议优化 Commander Prompt"
            else:
                self.low_score_streak = max(0, self.low_score_streak - 1)
                result["action"] = "none"

            return result
        except Exception as e:
            return {"evaluated": True, "error": str(e)[:100]}

    def get_streak(self) -> int:
        return self.low_score_streak


# ═══════════════════════════════════════════════════════════════
# 双守护: 第二个 Guardian 互相守护
# ═══════════════════════════════════════════════════════════════

class DualGuard:
    """双守护互保: primary 管 Commander, secondary 待命接管"""

    HEARTBEAT_KEY = "yaxiio:guardian:1:heartbeat"
    ROLE_KEY = "yaxiio:guardian:role"
    LEADER_KEY = "yaxiio:guardian:leader"

    def __init__(self, role: str = "primary"):
        self.role = role
        self.is_leader = (role == "primary")
        self.r = None
        try:
            import redis as _r
            self.r = _r.Redis(protocol=2, host=REDIS_HOST, port=REDIS_PORT,
                            password=REDIS_PASS, decode_responses=True,
                            socket_connect_timeout=3)
        except:
            pass

    def register(self):
        """注册并竞选 leader"""
        if not self.r:
            return
        self.r.setex(self.ROLE_KEY, 120, self.role)
        self.heartbeat()
        # 尝试获取 leader 锁
        acquired = self.r.setnx(self.LEADER_KEY, self.role)
        if acquired:
            self.is_leader = True
            self.r.expire(self.LEADER_KEY, 120)
        else:
            current_leader = self.r.get(self.LEADER_KEY)
            self.is_leader = (current_leader == self.role)

    def heartbeat(self):
        if self.r:
            self.r.setex(self.HEARTBEAT_KEY, 120, str(time.time()))
            # leader 续期
            if self.is_leader:
                current = self.r.get(self.LEADER_KEY)
                if current == self.role:
                    self.r.expire(self.LEADER_KEY, 120)

    def check_buddy(self) -> bool:
        """检查对方是否存活"""
        if not self.r:
            return True
        hb = self.r.get(self.HEARTBEAT_KEY)
        if not hb:
            return False
        try:
            return (time.time() - float(hb)) < 90
        except:
            return False

    def try_become_leader(self):
        """尝试成为 leader (对方挂了时)"""
        if not self.r:
            return
        acquired = self.r.setnx(self.LEADER_KEY, self.role)
        if acquired:
            self.is_leader = True
            self.r.expire(self.LEADER_KEY, 120)
            log(f"🛡️ {self.role} 已成为 Leader", "WARN")

    def take_over(self):
        """接管: 启动 Commander"""
        log(f"🛡️ {self.role} 接管: 启动 Commander", "ACTION")
        CommanderManager.start()


def main():
    log("═" * 60)
    log("🛡️ 雅溪 Yaxiio Commander Guard v3.0 启动")
    log(f"   健康端口: {HEALTH_PORT}")
    log(f"   检查间隔: {HEALTH_INTERVAL}s")
    log(f"   速率限制: {MAX_RESTARTS}次/{RATE_WINDOW}s")
    log(f"   日志文件: {LOG_FILE}")
    log("═" * 60)

    # 信号处理
    def shutdown(signum, frame):
        log("收到终止信号，关闭...")
        CommanderManager.stop()
        sys.exit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    rate_limiter = RateLimiter()
    commander_scorer = CommanderScorer()

    # ── 双守护: 领袖选举 ──
    guard_role = os.environ.get("GUARD_ROLE", "primary")
    dual_guard = DualGuard(role=guard_role)
    dual_guard.register()
    log(f"🛡️ 双守护角色: {guard_role}, Leader: {dual_guard.is_leader}")

    # 只有 Leader 启动 Commander
    if dual_guard.is_leader:
        CommanderManager.start()
        log("🛡️ Leader: Commander 由我管理", "INFO")
    else:
        log("🛡️ Secondary: 待命, 监控 Leader 状态", "INFO")

    consecutive_failures = 0

    while True:
        time.sleep(HEALTH_INTERVAL)

        # ── 1. 健康检查 ──
        health = HealthChecker.full_check()
        checks = health["checks"]

        status_str = (
            f"进程={'✓' if checks['process']['ok'] else '✗'} "
            f"Redis={'✓' if checks['redis']['ok'] else '✗'} "
            f"API={'✓' if checks['api']['ok'] else '✗'}"
        )

        if health["healthy"]:
            log(f"💚 健康检查通过 | {status_str}", "DEBUG")
            consecutive_failures = 0
            if consecutive_failures == 0:
                rate_limiter.reset()

            # ── Commander 元评分 (每 10 次) ──
            eval_result = commander_scorer.evaluate()
            if eval_result.get("evaluated") and eval_result.get("score") is not None:
                action = eval_result.get("action", "none")
                log(f"📊 Commander 评分: {eval_result['overall']}/10 "
                    f"(samples={eval_result['samples']}, action={action})", "DEBUG")
                if action == "critical":
                    log(f"⛔ Commander 严重低分! streak={commander_scorer.get_streak()}", "CRITICAL")
                elif action == "evolve_prompt":
                    log(f"⚠️ Commander 低分, streak={commander_scorer.get_streak()}", "WARN")

            # ── 双守护心跳 ──
            dual_guard.heartbeat()
            if not dual_guard.is_leader and not dual_guard.check_buddy():
                log("⚠️ 双守护: Leader 失联! 尝试接管...", "WARN")
                dual_guard.try_become_leader()
                if dual_guard.is_leader:
                    dual_guard.take_over()

            continue

        # ── 2. 不健康 ──
        consecutive_failures += 1
        log(f"💔 健康检查失败 ({consecutive_failures}次连续) | {status_str}", "WARN")
        for name, check in checks.items():
            if not check["ok"]:
                log(f"   └─ {name}: {check['detail']}", "WARN")

        # ── 3. 故障诊断 ──
        fault_type, detail = FaultDiagnoser.diagnose(health)
        log(f"🔍 诊断结果: {fault_type} — {detail}", "DIAG")

        # ── 4. 自动修复 ──
        # 只有 Commander 进程真的不在时才修复重启
        # API 500 是 Dashboard 的问题，不影响 Commander 核心功能
        if not checks["process"]["ok"]:
            if fault_type != FaultDiagnoser.FAULT_UNKNOWN:
                repaired = AutoRepair.repair(fault_type)
                if repaired:
                    log("✅ 修复完成，准备重启 Commander", "ACTION")
                else:
                    log(f"❌ 修复失败 ({fault_type})", "ERROR")
            else:
                log(f"⚠️ 未知故障，跳过自动修复", "WARN")

            # ── 5. 速率限制检查 ──
            if not rate_limiter.record_restart():
                log("⛔ 超过速率限制，等待人工介入", "CRITICAL")
                continue

            # ── 6. 重启 Commander ──
            log("🔄 尝试重启 Commander...", "ACTION")
            if CommanderManager.restart():
                log("✅ Commander 已重启", "ACTION")
            else:
                log("❌ Commander 重启失败", "ERROR")
        else:
            # Commander 进程在，只是API/Redis有问题，不重启
            if consecutive_failures % 10 == 0:
                log(f"⚠️ Commander在线但附属服务异常 ({consecutive_failures}次)，跳过重启", "WARN")


if __name__ == "__main__":
    main()
