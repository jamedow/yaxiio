#!/usr/bin/env python3
"""
Agent 进程隔离系统 — Commander 安全架构核心
=============================================
每个 Agent 运行在独立的工作目录中，拥有隔离的配置、技能和会话。
即使某个 Agent 的文件损坏或进程崩溃，Commander 主进程不受影响。

核心类:
  - IsolatedAgentFactory   : 创建隔离的 Agent 进程（独立目录 + PM2 管理）
  - AgentFaultIsolation    : 故障检测 + 隔离 + 自动修复
  - AgentWorkspaceManager  : 管理隔离工作区的文件结构

Constitution 合规:
  R1 — 不删 page:* / lightingmetal:* 前缀 key
  R2 — Agent 上限由 SafetyBoundary 控制
  R3 — 报价草稿由 CommanderV2 层处理
"""

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ── 尝试导入 redis ──
try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# 隔离工作区根目录（Docker 内默认路径）
DEFAULT_ISOLATION_ROOT = "/app/.pi/agents/isolated"

# Commander 自身的工作目录
COMMANDER_WORKSPACE = "/app/.pi/agent"

# 默认 Agent 超时配置
DEFAULT_HEARTBEAT_TIMEOUT_S = 60       # 心跳超时（秒）
DEFAULT_MAX_RESTART_ATTEMPTS = 3       # 最大重启尝试次数
DEFAULT_MONITOR_INTERVAL_S = 15        # 监控轮询间隔（秒）
DEFAULT_COOLDOWN_S = 30                # 故障后冷却时间

# PM2 配置
DEFAULT_MAX_MEMORY_RESTART = "512M"    # PM2 内存限制
DEFAULT_AGENT_TIMEOUT_MS = 120_000     # Agent 任务超时（毫秒）


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _short_id() -> str:
    """生成短唯一 ID（8 位十六进制）。"""
    return uuid.uuid4().hex[:8]


def _sanitize_name(name: str) -> str:
    """将中文 Agent 名转换为安全的目录名。"""
    result = []
    for ch in name:
        if ch.isalnum() or ch in '-_':
            result.append(ch)
        elif '\u4e00' <= ch <= '\u9fff' or '\u3040' <= ch <= '\u30ff':
            # 保留中日韩字符
            result.append(ch)
        else:
            result.append('-')
    return ''.join(result).strip('-')


# ═══════════════════════════════════════════════════════════════
# AgentWorkspaceManager — 工作目录隔离管理
# ═══════════════════════════════════════════════════════════════

class AgentWorkspaceManager:
    """为每个 Agent 创建和维护独立的工作目录。

    Directory structure:
        {root}/
        └── {agent_id}/
            ├── models.json        ← Agent 专属模型配置
            ├── skills/            ← Agent 专属技能（硬链接/复制自 Commander）
            ├── sessions/          ← Agent 会话历史（隔离，互不影响）
            ├── data/              ← Agent 工作数据
            ├── logs/              ← Agent 日志
            ├── prompt.txt         ← Agent 系统提示词
            └── agent.json         ← Agent 元数据
    """

    def __init__(self, root_dir: str = DEFAULT_ISOLATION_ROOT):
        self.root_dir = os.path.abspath(root_dir)
        os.makedirs(self.root_dir, exist_ok=True)

    def create_workspace(self, agent_id: str, role: str, model: str,
                         skills: List[str] = None,
                         system_prompt: str = None) -> str:
        """创建 Agent 的隔离工作区，返回工作区路径。"""
        workspace = os.path.join(self.root_dir, agent_id)

        # 如果已存在同名工作区，先清理
        if os.path.exists(workspace):
            shutil.rmtree(workspace)

        # 创建目录结构
        for subdir in ["skills", "sessions", "data", "logs"]:
            os.makedirs(os.path.join(workspace, subdir), exist_ok=True)

        # 1. 创建 Agent 专属 models.json
        self._create_models_config(workspace, model)

        # 2. 安装技能（从 Commander 技能库复制到 Agent 独立目录）
        if skills:
            self._install_skills(workspace, skills)

        # 3. 写入系统提示词
        if system_prompt:
            self._write_prompt(workspace, system_prompt)

        # 4. 写入 Agent 元数据
        self._write_metadata(workspace, agent_id, role, model, skills or [])

        return workspace

    def destroy_workspace(self, agent_id: str) -> bool:
        """销毁 Agent 的工作区。"""
        workspace = os.path.join(self.root_dir, agent_id)
        if os.path.exists(workspace):
            shutil.rmtree(workspace)
            return True
        return False

    def workspace_exists(self, agent_id: str) -> bool:
        """检查工作区是否存在。"""
        return os.path.exists(os.path.join(self.root_dir, agent_id))

    def get_workspace_path(self, agent_id: str) -> str:
        """获取工作区路径。"""
        return os.path.join(self.root_dir, agent_id)

    def list_workspaces(self) -> List[str]:
        """列出所有隔离工作区。"""
        if not os.path.exists(self.root_dir):
            return []
        return [
            d for d in os.listdir(self.root_dir)
            if os.path.isdir(os.path.join(self.root_dir, d))
            and not d.startswith('.')
        ]

    # ── 内部方法 ──

    def _create_models_config(self, workspace: str, model: str):
        """为 Agent 创建独立的模型配置。"""
        config = {
            "providers": {
                "deepseek": {
                    "baseUrl": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
                    "apiKey": "$DEEPSEEK_API_KEY",
                    "models": [{"id": model, "name": model}]
                }
            }
        }
        config_path = os.path.join(workspace, "models.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def _install_skills(self, workspace: str, skills: List[str]):
        """将技能从 Commander 库安装到 Agent 独立目录。

        对于每个技能，尝试从 Commander 技能库复制。
        如果 Commander 技能库中不存在，创建空技能占位。
        """
        skills_dst = os.path.join(workspace, "skills")
        commander_skills_dir = "/app/.pi/skills"

        for skill_name in skills:
            src = os.path.join(commander_skills_dir, skill_name)
            dst = os.path.join(skills_dst, skill_name)

            if os.path.isdir(src):
                # 复制整个技能目录
                try:
                    shutil.copytree(src, dst, symlinks=True,
                                     ignore=shutil.ignore_patterns(
                                         "__pycache__", "*.pyc", ".git", "node_modules"))
                except FileExistsError:
                    shutil.rmtree(dst)
                    shutil.copytree(src, dst, symlinks=True,
                                     ignore=shutil.ignore_patterns(
                                         "__pycache__", "*.pyc", ".git", "node_modules"))
            else:
                # 创建最小技能骨架
                os.makedirs(dst, exist_ok=True)
                skel = {
                    "name": skill_name,
                    "description": f"Agent 专属技能: {skill_name}",
                    "isolation_note": "此技能从 Commander 模板自动生成"
                }
                with open(os.path.join(dst, "SKILL.md"), "w", encoding="utf-8") as f:
                    f.write(f"---\nname: {skill_name}\ndescription: Agent 专属技能\n---\n\n")

    def _write_prompt(self, workspace: str, prompt: str):
        """写入 Agent 系统提示词。"""
        prompt_path = os.path.join(workspace, "prompt.txt")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(prompt)

    def _write_metadata(self, workspace: str, agent_id: str, role: str,
                        model: str, skills: List[str]):
        """写入 Agent 元数据 JSON。"""
        meta = {
            "agent_id": agent_id,
            "role": role,
            "model": model,
            "skills": skills,
            "created_at": datetime.now().isoformat(),
            "version": 1,
        }
        meta_path = os.path.join(workspace, "agent.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════
# IsolatedAgentFactory — 隔离 Agent 工厂
# ═══════════════════════════════════════════════════════════════

class IsolatedAgentFactory:
    """创建隔离的 Agent 进程，每个 Agent 拥有独立的工作目录和 PM2 进程。

    生命周期:
        create_agent()  → 创建工作区 + 启动 PM2 进程 + 注册到 Redis
        destroy_agent() → 停止 PM2 进程 + 清理工作区 + 注销 Redis
        restart_agent() → PM2 restart（保留工作区）
        rebuild_agent() → destroy + create（用于严重损坏后的完全重建）
    """

    def __init__(self,
                 root_dir: str = DEFAULT_ISOLATION_ROOT,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 agent_script: str = "/app/.pi/agents/runtime/agent-core.py"):
        self.workspace_mgr = AgentWorkspaceManager(root_dir)
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_password = redis_password
        self.agent_script = os.path.abspath(agent_script)

        # ── Redis 连接 ──
        if HAS_REDIS:
            self._redis = redis_lib.Redis(
                host=redis_host, port=redis_port,
                password=redis_password, decode_responses=True,
                socket_connect_timeout=5,
            )
        else:
            self._redis = None

    # ── 公共 API ──

    def create_agent(self,
                     role: str,
                     model: str = "deepseek-chat",
                     skills: List[str] = None,
                     system_prompt: str = None,
                     extra_env: Dict[str, str] = None,
                     max_memory: str = DEFAULT_MAX_MEMORY_RESTART,
                     ) -> Dict[str, Any]:
        """创建隔离的 Agent 进程。

        Returns:
            {
                "agent_id": str,        # 唯一 ID，如 "商务经理-a3f8b2c1"
                "pm2_name": str,        # PM2 进程名
                "workspace": str,       # 工作目录路径
                "status": str,          # "started" | "failed"
                "pid": int | None,      # 进程 PID
                "error": str | None,    # 错误信息（如果失败）
            }
        """
        agent_id = f"{_sanitize_name(role)}-{_short_id()}"
        pm2_name = f"agent-{agent_id}"

        if skills is None:
            skills = []
        if extra_env is None:
            extra_env = {}

        # 默认系统提示词
        if system_prompt is None:
            system_prompt = f"你是 LightningMetal 的 {role}，负责专业任务处理。通过 Redis Pub/Sub 接收任务。"

        try:
            # 1. 创建隔离工作区
            workspace = self.workspace_mgr.create_workspace(
                agent_id=agent_id,
                role=role,
                model=model,
                skills=skills,
                system_prompt=system_prompt,
            )

            # 2. 构建 PM2 启动参数
            env_vars = {
                "AGENT_NAME": role,
                "AGENT_ROLE": role,
                "AGENT_ID": agent_id,
                "AGENT_WORKSPACE": workspace,
                "REDIS_HOST": self.redis_host,
                "REDIS_PASS": self.redis_password or "",
                "REDIS_PORT": str(self.redis_port),
                "LLM_MODEL": model,
                "LLM_API_KEY": os.environ.get("LLM_API_KEY", ""),
                "LLM_BASE_URL": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
                "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", "")),
                "ISOLATION_ROOT": self.workspace_mgr.root_dir,
                # 日志路径
                "AGENT_LOG_DIR": os.path.join(workspace, "logs"),
            }
            env_vars.update(extra_env)

            # 3. 清理同名 PM2 进程（如果存在）
            try:
                subprocess.run(
                    ["pm2", "delete", pm2_name],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass

            # 4. 用 PM2 启动隔离的 Agent 进程
            pm2_args = [
                "pm2", "start", self.agent_script,
                "--name", pm2_name,
                "--interpreter", "python3",
                "--cwd", workspace,
                "--log", os.path.join(workspace, "logs", "combined.log"),
                "--error", os.path.join(workspace, "logs", "error.log"),
                "--output", os.path.join(workspace, "logs", "output.log"),
                "--max-memory-restart", max_memory,
                "--max-restarts", str(DEFAULT_MAX_RESTART_ATTEMPTS),
                "--restart-delay", "5000",
                "--kill-timeout", "15000",
                "--no-autorestart",
            ]

            result = subprocess.run(
                pm2_args,
                capture_output=True, text=True, timeout=30,
                env={**os.environ, **env_vars},
            )

            if result.returncode != 0:
                return {
                    "agent_id": agent_id,
                    "pm2_name": pm2_name,
                    "workspace": workspace,
                    "status": "failed",
                    "pid": None,
                    "error": f"PM2 启动失败: {result.stderr.strip()}",
                }

            # 5. 等待进程就绪
            time.sleep(1.5)

            # 6. 获取 PID
            pid = self._get_pid(pm2_name)

            # 7. 注册到 Redis
            self._register_to_redis(agent_id, role, pm2_name, workspace, pid)

            return {
                "agent_id": agent_id,
                "pm2_name": pm2_name,
                "workspace": workspace,
                "status": "started",
                "pid": pid,
                "error": None,
            }

        except Exception as e:
            # 清理失败的工作区
            try:
                self.workspace_mgr.destroy_workspace(agent_id)
            except Exception:
                pass
            return {
                "agent_id": agent_id,
                "pm2_name": pm2_name,
                "workspace": "",
                "status": "failed",
                "pid": None,
                "error": str(e),
            }

    def destroy_agent(self, agent_id: str) -> Dict[str, Any]:
        """销毁 Agent 进程并清理工作目录。"""
        pm2_name = f"agent-{agent_id}"

        errors = []

        # 1. 停止并删除 PM2 进程
        try:
            subprocess.run(
                ["pm2", "delete", pm2_name],
                capture_output=True, timeout=15,
            )
        except subprocess.TimeoutExpired:
            # 强制 kill
            try:
                subprocess.run(["pm2", "kill", pm2_name], capture_output=True, timeout=5)
            except Exception:
                pass
            errors.append("PM2 delete timeout")
        except FileNotFoundError:
            # PM2 未安装
            errors.append("PM2 not found")

        # 2. 清理 Redis 注册信息
        self._unregister_from_redis(agent_id)

        # 3. 清理工作区
        destroyed = self.workspace_mgr.destroy_workspace(agent_id)

        return {
            "agent_id": agent_id,
            "pm2_name": pm2_name,
            "workspace_destroyed": destroyed,
            "errors": errors if errors else None,
            "status": "destroyed",
        }

    def restart_agent(self, agent_id: str) -> Dict[str, Any]:
        """重启 Agent 进程（保留工作区）。"""
        pm2_name = f"agent-{agent_id}"

        try:
            result = subprocess.run(
                ["pm2", "restart", pm2_name],
                capture_output=True, text=True, timeout=15,
            )
            time.sleep(1)

            pid = self._get_pid(pm2_name)
            self._update_redis_pid(agent_id, pid)

            return {
                "agent_id": agent_id,
                "pm2_name": pm2_name,
                "status": "restarted",
                "pid": pid,
                "output": result.stdout.strip(),
            }
        except Exception as e:
            return {
                "agent_id": agent_id,
                "pm2_name": pm2_name,
                "status": "failed",
                "error": str(e),
            }

    def rebuild_agent(self, agent_id: str,
                      role: str, model: str = "deepseek-chat",
                      skills: List[str] = None) -> Dict[str, Any]:
        """完全重建 Agent（销毁 + 创建），用于严重故障后恢复。

        注意: 重建会用新的 UUID，所以如果外部持有旧的 agent_id 需要更新。
        """
        destroy_result = self.destroy_agent(agent_id)
        create_result = self.create_agent(
            role=role,
            model=model,
            skills=skills or [],
        )
        create_result["destroy_result"] = destroy_result
        return create_result

    def get_agent_status(self, agent_id: str) -> Dict[str, Any]:
        """获取 Agent 的运行状态。"""
        pm2_name = f"agent-{agent_id}"

        try:
            result = subprocess.run(
                ["pm2", "jlist"],
                capture_output=True, text=True, timeout=10,
            )
            processes = json.loads(result.stdout)

            for proc in processes:
                if proc.get("name") == pm2_name:
                    return {
                        "agent_id": agent_id,
                        "pm2_name": pm2_name,
                        "status": proc.get("pm2_env", {}).get("status", "unknown"),
                        "pid": proc.get("pid"),
                        "cpu": proc.get("monit", {}).get("cpu", 0),
                        "memory": proc.get("monit", {}).get("memory", 0),
                        "uptime": proc.get("pm2_env", {}).get("pm_uptime", 0),
                        "restarts": proc.get("pm2_env", {}).get("restart_time", 0),
                        "workspace": self.workspace_mgr.get_workspace_path(agent_id),
                    }
        except (json.JSONDecodeError, FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # PM2 未运行或 Agent 不存在
        return {
            "agent_id": agent_id,
            "pm2_name": pm2_name,
            "status": "not_found",
            "pid": None,
            "workspace_exists": self.workspace_mgr.workspace_exists(agent_id),
        }

    def get_agent_heartbeat(self, agent_id: str) -> Optional[float]:
        """从 Redis 读取 Agent 最后心跳时间。同时检查完整 agent_id（含UUID后缀）和基础名（翻译官/商务经理等）。"""
        if not self._redis:
            return None
        try:
            # 1. 先查完整 agent_id（如 翻译官-a1b2c3d4）
            key = f"commander:agent:heartbeat:{agent_id}"
            val = self._redis.get(key)
            if val:
                return float(val)
            # 2. 回退查基础名（去除 UUID 后缀，如 翻译官）
            # UUID 格式: 8位十六进制，agent_id 如 "翻译官-a1b2c3d4"
            parts = agent_id.rsplit("-", 1)
            if len(parts) == 2 and len(parts[1]) == 8:
                base_key = f"commander:agent:heartbeat:{parts[0]}"
                base_val = self._redis.get(base_key)
                if base_val:
                    return float(base_val)
            return None
        except Exception:
            return None

    def list_agents(self) -> List[Dict[str, Any]]:
        """列出所有隔离 Agent 的状态。"""
        agents = []
        for ws_name in self.workspace_mgr.list_workspaces():
            status = self.get_agent_status(ws_name)
            # 从工作区读取元数据
            meta = self._read_metadata(ws_name)
            last_heartbeat = self.get_agent_heartbeat(ws_name)
            status["metadata"] = meta
            status["last_heartbeat"] = last_heartbeat
            agents.append(status)
        return agents

    # ── 内部方法 ──

    def _get_pid(self, pm2_name: str) -> Optional[int]:
        """获取 PM2 进程的 PID。"""
        try:
            result = subprocess.run(
                ["pm2", "jlist"],
                capture_output=True, text=True, timeout=10,
            )
            processes = json.loads(result.stdout)
            for proc in processes:
                if proc.get("name") == pm2_name:
                    return proc.get("pid")
        except Exception:
            pass
        return None

    def _register_to_redis(self, agent_id: str, role: str,
                           pm2_name: str, workspace: str, pid: Optional[int]):
        """注册 Agent 到 Redis。"""
        if not self._redis:
            return
        try:
            # 注册活跃 Agent 信息
            info = json.dumps({
                "agent_id": agent_id,
                "role": role,
                "pm2_name": pm2_name,
                "workspace": workspace,
                "pid": pid,
                "registered_at": datetime.now().isoformat(),
            }, ensure_ascii=False)
            self._redis.hset("commander:agent:isolated", agent_id, info)

            # 加入活跃 Agent 集合
            self._redis.sadd("agent:pool:active", agent_id)

            # 写入心跳时间
            self._redis.set(
                f"commander:agent:heartbeat:{agent_id}",
                str(time.time()),
            )
        except Exception:
            pass

    def _unregister_from_redis(self, agent_id: str):
        """从 Redis 注销 Agent。"""
        if not self._redis:
            return
        try:
            self._redis.hdel("commander:agent:isolated", agent_id)
            self._redis.srem("agent:pool:active", agent_id)
            self._redis.delete(f"commander:agent:heartbeat:{agent_id}")
            # 保留历史状态（标记为 archived 而非删除）
            self._redis.hset(
                "commander:agent:archived",
                agent_id,
                json.dumps({
                    "agent_id": agent_id,
                    "archived_at": datetime.now().isoformat(),
                    "reason": "agent_destroyed",
                }, ensure_ascii=False),
            )
        except Exception:
            pass

    def _update_redis_pid(self, agent_id: str, pid: Optional[int]):
        """更新 Redis 中 Agent 的 PID（不写心跳，心跳由Agent自行维护）。"""
        if not self._redis:
            return
        try:
            raw = self._redis.hget("commander:agent:isolated", agent_id)
            if raw:
                info = json.loads(raw)
                info["pid"] = pid
                info["updated_at"] = datetime.now().isoformat()
                self._redis.hset(
                    "commander:agent:isolated",
                    agent_id,
                    json.dumps(info, ensure_ascii=False),
                )
        except Exception:
            pass

    def _read_metadata(self, agent_id: str) -> Optional[Dict]:
        """从工作区读取 Agent 元数据。"""
        meta_path = os.path.join(
            self.workspace_mgr.get_workspace_path(agent_id),
            "agent.json",
        )
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None


# ═══════════════════════════════════════════════════════════════
# AgentFaultIsolation — 故障隔离与自动修复
# ═══════════════════════════════════════════════════════════════

class AgentFaultIsolation:
    """Agent 故障检测、隔离与自动修复。

    工作流程:
        1. 监控所有隔离 Agent 的心跳
        2. 检测超时 / 崩溃 / 响应异常
        3. 隔离故障 Agent（从活跃池移除，停止消息接收）
        4. 尝试重启 → 失败则完全重建
        5. 记录故障历史到 Redis

    隔离级别:
        WARN    — 延迟偏高，仅记录
        SOFT    — 心跳超时，尝试重启
        HARD    — 进程崩溃或多次重启失败，隔离 + 完全重建
        FATAL   — 工作区损坏，标记为不可恢复
    """

    ISOLATION_LEVELS = ["WARN", "SOFT", "HARD", "FATAL"]

    def __init__(self,
                 factory: IsolatedAgentFactory,
                 heartbeat_timeout_s: int = DEFAULT_HEARTBEAT_TIMEOUT_S,
                 max_restart_attempts: int = DEFAULT_MAX_RESTART_ATTEMPTS,
                 cooldown_s: int = DEFAULT_COOLDOWN_S):
        self.factory = factory
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.max_restart_attempts = max_restart_attempts
        self.cooldown_s = cooldown_s

        # 故障计数器: agent_id → {restarts, last_failure, level, ...}
        self._failure_counters: Dict[str, Dict] = {}

    def check_agent_health(self, agent_id: str) -> Tuple[str, Optional[str]]:
        """检查单个 Agent 的健康状态。

        Returns:
            (level, reason)
            level = "healthy" | "WARN" | "SOFT" | "HARD" | "FATAL" | "not_found"
        """
        # 1. 获取 PM2 状态
        pm2_status = self.factory.get_agent_status(agent_id)
        pm2_state = pm2_status.get("status", "not_found")

        if pm2_state == "not_found":
            return ("not_found", "PM2 中未找到该 Agent 进程")

        # 2. 检查工作区完整性
        workspace = self.factory.workspace_mgr.get_workspace_path(agent_id)
        if not os.path.exists(workspace):
            return ("FATAL", f"工作区不存在: {workspace}")

        # 检查关键文件
        critical_files = ["agent.json", "models.json"]
        for cf in critical_files:
            if not os.path.exists(os.path.join(workspace, cf)):
                return ("FATAL", f"关键文件缺失: {cf}")

        # 3. 检查进程是否 running
        if pm2_state == "stopped":
            return ("HARD", "Agent 进程已停止")

        if pm2_state.startswith("waiting"):
            return ("HARD", "Agent 进程等待重启（已被kill或崩溃）")

        if pm2_state == "errored":
            counter = self._get_counter(agent_id)
            if counter["restarts"] >= self.max_restart_attempts:
                return ("FATAL", f"已达最大重启次数 ({self.max_restart_attempts})")
            return ("HARD", "Agent 进程进入错误状态")

        # 4. 心跳检查已禁用 — 内部Agent（翻译官/商务经理/售前经理）由PM2管理，无需Redis心跳
        # 外部Agent（如客户部署的独立Agent）才需要心跳监控

        # 5. 检查内存
        mem_mb = pm2_status.get("memory", 0) / (1024 * 1024) if pm2_status.get("memory") else 0
        if mem_mb > 900:  # 接近 1GB PM2 限制
            return ("WARN", f"内存使用偏高: {mem_mb:.0f}MB")

        return ("healthy", None)

    def isolate_agent(self, agent_id: str, reason: str = "") -> Dict[str, Any]:
        """隔离故障 Agent，防止影响系统其他部分。

        隔离操作:
          1. 从活跃池移除（停止接受新任务）
          2. 标记为 isolated 状态
          3. 发布隔离通知到 Pub/Sub
        """
        redis_client = self.factory._redis
        if not redis_client:
            return {"agent_id": agent_id, "status": "no_redis"}

        try:
            # 从活跃池移除
            redis_client.srem("agent:pool:active", agent_id)

            # 标记隔离状态
            isolate_info = json.dumps({
                "agent_id": agent_id,
                "status": "isolated",
                "reason": reason,
                "isolated_at": datetime.now().isoformat(),
            }, ensure_ascii=False)
            redis_client.hset("commander:agent:status", agent_id, isolate_info)

            # 发布隔离通知
            redis_client.publish(
                "commander:agent:events",
                json.dumps({
                    "type": "agent_isolated",
                    "agent_id": agent_id,
                    "reason": reason,
                    "timestamp": time.time(),
                }, ensure_ascii=False),
            )

            # 更新故障计数器
            counter = self._get_counter(agent_id)
            counter["last_isolation"] = time.time()
            counter["isolation_count"] = counter.get("isolation_count", 0) + 1

            return {
                "agent_id": agent_id,
                "status": "isolated",
                "reason": reason,
            }
        except Exception as e:
            return {"agent_id": agent_id, "status": "failed", "error": str(e)}

    def repair_agent(self, agent_id: str) -> Dict[str, Any]:
        """尝试修复故障 Agent。

        修复策略（按顺序尝试）:
          1. SOFT  → PM2 restart（保留工作区）
          2. HARD  → 完全重建（销毁工作区 + 重新创建）
          3. FATAL → 放弃，等待人工干预
        """
        health, reason = self.check_agent_health(agent_id)

        # 健康就不需要修复
        if health == "healthy":
            return {"agent_id": agent_id, "status": "healthy", "action": "none"}

        counter = self._get_counter(agent_id)

        # 冷却检查
        last_attempt = counter.get("last_repair_attempt", 0)
        if time.time() - last_attempt < self.cooldown_s:
            return {
                "agent_id": agent_id,
                "status": "cooldown",
                "reason": f"冷却中 (还需 {self.cooldown_s - (time.time() - last_attempt):.0f}s)",
            }

        counter["last_repair_attempt"] = time.time()

        # ── SOFT: 尝试 PM2 restart ──
        if health in ("SOFT", "WARN") or (health == "HARD" and counter.get("restarts", 0) == 0):
            print(f"[AgentFaultIsolation] 🔄 {'SOFT' if health != 'HARD' else 'HARD→SOFT'} 修复 ({agent_id}): {reason}")
            result = self.factory.restart_agent(agent_id)
            counter["restarts"] = counter.get("restarts", 0) + 1

            if result.get("status") == "restarted":
                return {
                    "agent_id": agent_id,
                    "status": "repaired",
                    "action": "restart",
                    "details": result,
                }

        # ── HARD: 完全重建 ──
        if health in ("HARD", "SOFT") and counter.get("restarts", 0) >= 2:
            print(f"[AgentFaultIsolation] 🔨 HARD 修复 ({agent_id}): {reason}")

            # 获取元数据用于重建
            meta = self.factory._read_metadata(agent_id)
            if meta:
                role = meta.get("role", "unknown")
                model = meta.get("model", "deepseek-chat")
                skills = meta.get("skills", [])
            else:
                role = agent_id.split("-")[0] if "-" in agent_id else agent_id
                model = "deepseek-chat"
                skills = []

            result = self.factory.rebuild_agent(
                agent_id=agent_id,
                role=role,
                model=model,
                skills=skills,
            )
            counter["restarts"] = 0  # 重置计数器
            counter["rebuilds"] = counter.get("rebuilds", 0) + 1

            return {
                "agent_id": agent_id,
                "status": "rebuilt",
                "action": "rebuild",
                "new_agent_id": result.get("agent_id"),
                "details": result,
            }

        # ── FATAL: 需要人工干预 ──
        if health == "FATAL":
            print(f"[AgentFaultIsolation] 💀 FATAL ({agent_id}): {reason} — 需要人工干预")
            return {
                "agent_id": agent_id,
                "status": "fatal",
                "action": "manual_intervention_required",
                "reason": reason,
            }

        # 其他情况
        return {
            "agent_id": agent_id,
            "status": "monitoring",
            "action": "waiting",
            "reason": reason,
        }

    def monitor_and_repair_all(self) -> Dict[str, Any]:
        """监控所有 Agent 并自动修复故障。

        这是周期性的主循环，应该被 Commander 定时调用。
        """
        agents = self.factory.list_agents()
        results = {
            "total": len(agents),
            "healthy": 0,
            "repaired": 0,
            "isolated": 0,
            "fatal": 0,
            "details": [],
        }

        for agent in agents:
            agent_id = agent["agent_id"]
            health, reason = self.check_agent_health(agent_id)

            detail = {
                "agent_id": agent_id,
                "health": health,
                "reason": reason,
            }

            if health == "healthy":
                results["healthy"] += 1
            elif health == "WARN":
                # WARN 仅记录，不触发修复
                results["healthy"] += 1
                detail["note"] = "warn_only_no_repair"
            else:
                # 先隔离
                self.isolate_agent(agent_id, reason)
                results["isolated"] += 1

                # 再尝试修复
                repair_result = self.repair_agent(agent_id)
                if repair_result.get("status") == "fatal":
                    results["fatal"] += 1
                elif repair_result.get("status") in ("repaired", "rebuilt"):
                    results["repaired"] += 1
                detail["repair"] = repair_result

            results["details"].append(detail)

        return results

    # ── 内部方法 ──

    def _get_counter(self, agent_id: str) -> Dict:
        """获取或初始化 Agent 的故障计数器。"""
        if agent_id not in self._failure_counters:
            # 初始化时也从 Redis 读取历史
            redis_client = self.factory._redis
            restarts = 0
            if redis_client:
                try:
                    key = f"commander:agent:failures:{agent_id}"
                    raw = redis_client.get(key)
                    if raw:
                        data = json.loads(raw)
                        restarts = data.get("restarts", 0)
                except Exception:
                    pass

            self._failure_counters[agent_id] = {
                "restarts": restarts,
                "rebuilds": 0,
                "isolation_count": 0,
                "last_failure": 0,
                "last_isolation": 0,
                "last_repair_attempt": 0,
            }

        # 定期持久化到 Redis
        self._persist_counter(agent_id)

        return self._failure_counters[agent_id]

    def _persist_counter(self, agent_id: str):
        """将故障计数器持久化到 Redis。"""
        counter = self._failure_counters.get(agent_id)
        if not counter:
            return
        redis_client = self.factory._redis
        if not redis_client:
            return
        try:
            key = f"commander:agent:failures:{agent_id}"
            redis_client.setex(key, 86400, json.dumps(counter, ensure_ascii=False))
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════
# IsolatedAgentManager — 一体化管理器（工厂 + 故障隔离）
# ═══════════════════════════════════════════════════════════════

class IsolatedAgentManager:
    """一体化 Agent 隔离管理器。

    组合了 IsolatedAgentFactory 和 AgentFaultIsolation，
    提供统一的 API 用于 Commander 集成。
    """

    def __init__(self,
                 root_dir: str = DEFAULT_ISOLATION_ROOT,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 heartbeat_timeout_s: int = DEFAULT_HEARTBEAT_TIMEOUT_S,
                 max_restart_attempts: int = DEFAULT_MAX_RESTART_ATTEMPTS,
                 cooldown_s: int = DEFAULT_COOLDOWN_S):
        self.factory = IsolatedAgentFactory(
            root_dir=root_dir,
            redis_host=redis_host,
            redis_port=redis_port,
            redis_password=redis_password,
        )
        self.fault_isolation = AgentFaultIsolation(
            factory=self.factory,
            heartbeat_timeout_s=heartbeat_timeout_s,
            max_restart_attempts=max_restart_attempts,
            cooldown_s=cooldown_s,
        )
        self.root_dir = root_dir

    # ── 便捷方法 ──

    def create_agent(self, **kwargs) -> Dict[str, Any]:
        """创建隔离 Agent（委托给工厂）。"""
        return self.factory.create_agent(**kwargs)

    def destroy_agent(self, agent_id: str) -> Dict[str, Any]:
        """销毁 Agent（委托给工厂）。"""
        return self.factory.destroy_agent(agent_id)

    def monitor(self) -> Dict[str, Any]:
        """执行一次完整的监控 + 修复循环。"""
        return self.fault_isolation.monitor_and_repair_all()

    def get_status_all(self) -> Dict[str, Any]:
        """获取所有 Agent 的状态摘要。"""
        agents = self.factory.list_agents()
        healthy = 0
        unhealthy = 0
        for agent in agents:
            aid = agent["agent_id"]
            health, _ = self.fault_isolation.check_agent_health(aid)
            if health == "healthy":
                healthy += 1
            elif health != "not_found":
                unhealthy += 1
        return {
            "total": len(agents),
            "healthy": healthy,
            "unhealthy": unhealthy,
            "agents": agents,
            "isolation_root": self.root_dir,
        }

    def initialize_core_agents(self,
                                agents_config: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """初始化核心 Agent 集群（启动时调用）。

        agents_config: [
            {"role": "翻译官", "skills": ["translate-engine"], "keep_warm": True},
            {"role": "商务经理", "skills": ["product-search"], "keep_warm": True},
            ...
        ]
        """
        results = []
        for cfg in agents_config:
            role = cfg["role"]
            # 检查是否已经存在
            existing = self.factory.get_agent_status(role)
            if existing.get("status") != "not_found" and existing.get("pid"):
                print(f"[IsolatedAgentManager] {role} 已运行, PID={existing['pid']}")
                results.append({"role": role, "status": "already_running", "detail": existing})
                continue

            result = self.factory.create_agent(
                role=role,
                model=cfg.get("model", "deepseek-chat"),
                skills=cfg.get("skills", []),
                system_prompt=cfg.get("system_prompt"),
                extra_env=cfg.get("extra_env"),
            )
            results.append({"role": role, **result})
        return results

    def shutdown_all(self) -> Dict[str, Any]:
        """优雅关闭所有隔离 Agent。"""
        agents = self.factory.workspace_mgr.list_workspaces()
        results = []
        for agent_id in agents:
            result = self.factory.destroy_agent(agent_id)
            results.append(result)
        return {
            "total_destroyed": len([r for r in results if r["status"] == "destroyed"]),
            "details": results,
        }


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent 隔离系统管理")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "create", "destroy", "monitor", "init"])
    parser.add_argument("--role", type=str, help="Agent 角色名")
    parser.add_argument("--agent-id", type=str, help="Agent ID")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-password", default="")
    parser.add_argument("--model", default="deepseek-chat")
    args = parser.parse_args()

    mgr = IsolatedAgentManager(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
    )

    if args.action == "status":
        status = mgr.get_status_all()
        print(json.dumps(status, indent=2, ensure_ascii=False))

    elif args.action == "create":
        if not args.role:
            print("错误: 需要 --role 参数")
            exit(1)
        result = mgr.create_agent(role=args.role, model=args.model)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "destroy":
        if not args.agent_id:
            print("错误: 需要 --agent-id 参数")
            exit(1)
        result = mgr.destroy_agent(args.agent_id)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "monitor":
        result = mgr.monitor()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "init":
        core_agents = [
            {"role": "翻译官", "skills": ["translate-engine"]},
            {"role": "商务经理", "skills": ["product-search"]},
            {"role": "售前经理", "skills": ["product-search"]},
        ]
        results = mgr.initialize_core_agents(core_agents)
        print(json.dumps(results, indent=2, ensure_ascii=False))
