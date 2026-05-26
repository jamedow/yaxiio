#!/usr/bin/env python3
"""
SandboxManager — 多租户沙箱隔离系统
=====================================
每个用户独立的工作空间，拥有独立的 PM2 实例、Agent 集群、会话数据。

核心能力:
  - 创建沙箱: 分配 session_key → 创建独立目录 + PM2 实例
  - 恢复沙箱: 用 session_key 重新接入 → 恢复 PM2 进程 + 会话上下文
  - 保留沙箱: 用户退出但选择保留 → PM2 暂停，沙箱保留 (idle)
  - 销毁沙箱: 用户主动销毁 → rm -rf 整个用户目录
  - 超时清理: 超过保留期限 → 自动执行销毁流程

Constitution 合规:
  R1 — 使用 `commander:sandbox:*` 前缀，不碰 page:* / lightingmetal:*
  R2 — 每个沙箱内 Agent 上限受 SafetyBoundary 控制
"""

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

DEFAULT_SANDBOX_ROOT = "/app/sandboxes"
DEFAULT_MAX_SANDBOXES = 50               # 最多同时活跃的沙箱数
DEFAULT_AUTO_DESTROY_HOURS = 24          # 默认自动销毁时间（小时）
DEFAULT_MAX_DISK_MB = 1024               # 每个沙箱磁盘上限 (1GB)
DEFAULT_MAX_MEMORY_MB = 4096             # 每个沙箱内存上限 (4GB)
DEFAULT_MAX_CPU_CORES = 2                # 每个沙箱 CPU 上限

# PM2 配置
DEFAULT_MAX_MEMORY_RESTART = "512M"


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _short_id(length: int = 8) -> str:
    """生成短唯一 session_key。"""
    return uuid.uuid4().hex[:length]


def _safe_name(raw: str) -> str:
    """将用户标识转换为安全的目录名。"""
    import re
    return re.sub(r'[^a-zA-Z0-9\-_]', '-', raw)[:32]


# ═══════════════════════════════════════════════════════════════
# 沙箱元数据
# ═══════════════════════════════════════════════════════════════

class SandboxMeta:
    """单个沙箱的元数据。"""

    def __init__(self,
                 session_key: str,
                 user_dir: str,
                 user_identifier: str = "",
                 created_at: Optional[str] = None,
                 last_active: Optional[str] = None,
                 status: str = "active",
                 auto_destroy_after_hours: Optional[int] = None):
        self.session_key = session_key
        self.user_dir = user_dir
        self.user_identifier = user_identifier
        self.created_at = created_at or datetime.now().isoformat()
        self.last_active = last_active or self.created_at
        self.status = status  # active | idle | archived | destroyed
        self.auto_destroy_after_hours = auto_destroy_after_hours

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_key": self.session_key,
            "user_dir": self.user_dir,
            "user_identifier": self.user_identifier,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "status": self.status,
            "auto_destroy_after_hours": self.auto_destroy_after_hours,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SandboxMeta":
        return cls(
            session_key=data["session_key"],
            user_dir=data["user_dir"],
            user_identifier=data.get("user_identifier", ""),
            created_at=data.get("created_at"),
            last_active=data.get("last_active"),
            status=data.get("status", "active"),
            auto_destroy_after_hours=data.get("auto_destroy_after_hours"),
        )

    def is_expired(self) -> bool:
        """检查沙箱是否已过期。"""
        if self.auto_destroy_after_hours is None:
            return False
        last = datetime.fromisoformat(self.last_active)
        expires = last + timedelta(hours=self.auto_destroy_after_hours)
        return datetime.now() > expires

    def touch(self):
        """更新最后活跃时间。"""
        self.last_active = datetime.now().isoformat()


# ═══════════════════════════════════════════════════════════════
# SandboxManager 核心
# ═══════════════════════════════════════════════════════════════

class SandboxManager:
    """多租户沙箱管理系统。

    目录结构:
        {root}/
        ├── index.json                    ← session_key → 沙箱映射
        ├── user-{identifier}/            ← 用户独立工作空间
        │   ├── workspace/
        │   ├── sessions/
        │   ├── models.json
        │   ├── agent.json
        │   ├── .pm2/                     ← 独立 PM2 实例
        │   ├── mcp-servers.json          ← 信任的外部 MCP 服务
        │   └── logs/
        └── user-{identifier}/
            └── ...

    Redis 索引:
        commander:sandbox:index            ← Hash: session_key → meta JSON
        commander:sandbox:active           ← Set: 活跃 session_key
        commander:sandbox:{key}:state      ← Hash: 沙箱运行时状态
    """

    def __init__(self,
                 root_dir: str = DEFAULT_SANDBOX_ROOT,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 max_sandboxes: int = DEFAULT_MAX_SANDBOXES,
                 max_disk_mb: int = DEFAULT_MAX_DISK_MB,
                 max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
                 max_cpu_cores: int = DEFAULT_MAX_CPU_CORES):
        self.root_dir = os.path.abspath(root_dir)
        self.max_sandboxes = max_sandboxes
        self.max_disk_mb = max_disk_mb
        self.max_memory_mb = max_memory_mb
        self.max_cpu_cores = max_cpu_cores

        os.makedirs(self.root_dir, exist_ok=True)

        # Redis 连接
        if HAS_REDIS:
            self._redis = redis_lib.Redis(
                host=redis_host, port=redis_port,
                password=redis_password, decode_responses=True,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
        else:
            self._redis = None

        # 加载本地索引
        self._index: Dict[str, SandboxMeta] = {}
        self._load_index()

    # ═══════════════════════════════════════════════════════════
    # 沙箱生命周期
    # ═══════════════════════════════════════════════════════════

    def create_sandbox(self,
                       user_identifier: str = "",
                       auto_destroy_hours: Optional[int] = DEFAULT_AUTO_DESTROY_HOURS,
                       trusted_mcp_servers: Optional[List[Dict]] = None,
                       ) -> Dict[str, Any]:
        """为用户创建独立的沙箱。

        Args:
            user_identifier: 用户标识（可选，用于恢复）
            auto_destroy_hours: 自动销毁时间（小时），None=永久保留
            trusted_mcp_servers: 信任的外部 MCP 服务列表

        Returns:
            {
                "session_key": str,
                "user_dir": str,
                "sandbox_path": str,
                "status": "created",
                "created_at": str,
            }
        """
        # 检查容量上限
        active_count = self._count_active()
        if active_count >= self.max_sandboxes:
            return {
                "status": "rejected",
                "reason": f"沙箱数量已达上限 ({self.max_sandboxes})",
                "active_count": active_count,
            }

        # 生成 session_key
        session_key = _short_id(12)
        safe_uid = _safe_name(user_identifier or f"anon-{_short_id(6)}")
        user_dir = f"user-{safe_uid}-{_short_id(4)}"

        sandbox_path = os.path.join(self.root_dir, user_dir)

        # 创建目录结构
        for subdir in ["workspace", "sessions", "logs"]:
            os.makedirs(os.path.join(sandbox_path, subdir), exist_ok=True)

        # 写入 models.json
        self._write_models_config(sandbox_path)

        # 写入 agent.json 元数据
        agent_meta = {
            "session_key": session_key,
            "user_dir": user_dir,
            "user_identifier": user_identifier,
            "created_at": datetime.now().isoformat(),
            "sandbox_version": "3.0",
        }
        with open(os.path.join(sandbox_path, "agent.json"), "w", encoding="utf-8") as f:
            json.dump(agent_meta, f, indent=2, ensure_ascii=False)

        # 写入 MCP 信任列表
        self._write_mcp_config(sandbox_path, trusted_mcp_servers or [])

        # 初始化独立 PM2 实例
        pm2_home = os.path.join(sandbox_path, ".pm2")
        self._init_pm2_instance(pm2_home)

        # 注册元数据
        meta = SandboxMeta(
            session_key=session_key,
            user_dir=user_dir,
            user_identifier=user_identifier,
            auto_destroy_after_hours=auto_destroy_hours,
            status="active",
        )
        self._register_meta(meta)

        return {
            "session_key": session_key,
            "user_dir": user_dir,
            "sandbox_path": os.path.abspath(sandbox_path),
            "status": "created",
            "created_at": meta.created_at,
            "auto_destroy_after_hours": auto_destroy_hours,
        }

    def restore_sandbox(self, session_key: str) -> Dict[str, Any]:
        """用 session_key 恢复沙箱（恢复 PM2 进程 + 会话上下文）。

        Returns:
            {"status": "restored" | "not_found" | "already_active", ...}
        """
        meta = self._get_meta(session_key)
        if not meta:
            return {"status": "not_found", "session_key": session_key,
                    "reason": "session_key 无效，请重新创建沙箱"}

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)
        if not os.path.exists(sandbox_path):
            return {"status": "not_found", "session_key": session_key,
                    "reason": "沙箱目录已被删除，请重新创建"}

        if meta.status == "active":
            meta.touch()
            self._update_meta(meta)
            return {"status": "already_active", "session_key": session_key,
                    "user_dir": meta.user_dir, "sandbox_path": sandbox_path}

        # 恢复 PM2 进程
        pm2_home = os.path.join(sandbox_path, ".pm2")
        self._init_pm2_instance(pm2_home)

        # 加载会话上下文
        sessions = self._load_sessions(sandbox_path)

        # 更新状态
        meta.status = "active"
        meta.touch()
        self._update_meta(meta)

        return {
            "status": "restored",
            "session_key": session_key,
            "user_dir": meta.user_dir,
            "sandbox_path": sandbox_path,
            "session_count": len(sessions),
            "last_active": meta.last_active,
        }

    def preserve_sandbox(self, session_key: str) -> Dict[str, Any]:
        """暂停沙箱（PM2 进程暂停，沙箱保留）。"""
        meta = self._get_meta(session_key)
        if not meta:
            return {"status": "not_found", "session_key": session_key}

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)

        # 暂停 PM2 进程
        pm2_home = os.path.join(sandbox_path, ".pm2")
        self._stop_pm2_instance(pm2_home)

        # 更新状态
        meta.status = "idle"
        meta.touch()
        self._update_meta(meta)

        return {
            "status": "preserved",
            "session_key": session_key,
            "user_dir": meta.user_dir,
            "last_active": meta.last_active,
        }

    def destroy_sandbox(self, session_key: str) -> Dict[str, Any]:
        """彻底销毁沙箱（rm -rf 整个目录）。"""
        meta = self._get_meta(session_key)
        if not meta:
            return {"status": "not_found", "session_key": session_key}

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)

        destroyed = False
        errors = []

        # 停止 PM2
        pm2_home = os.path.join(sandbox_path, ".pm2")
        try:
            self._kill_pm2_instance(pm2_home)
        except Exception as e:
            errors.append(f"PM2 stop failed: {e}")

        # rm -rf
        if os.path.exists(sandbox_path):
            try:
                shutil.rmtree(sandbox_path)
                destroyed = True
            except Exception as e:
                errors.append(f"rmtree failed: {e}")

        # 注销
        self._unregister_meta(session_key)

        return {
            "status": "destroyed" if destroyed else "destroy_failed",
            "session_key": session_key,
            "user_dir": meta.user_dir,
            "errors": errors if errors else None,
        }

    def cleanup_expired_sandboxes(self) -> Dict[str, Any]:
        """清理所有过期的沙箱。"""
        expired_keys = []
        destroyed = 0
        failed = 0

        for session_key, meta in list(self._index.items()):
            if meta.is_expired():
                expired_keys.append(session_key)

        for key in expired_keys:
            result = self.destroy_sandbox(key)
            if result["status"] == "destroyed":
                destroyed += 1
                print(f"[SandboxManager] 🗑️ 过期沙箱已清理: {key}")
            else:
                failed += 1

        return {
            "expired_detected": len(expired_keys),
            "destroyed": destroyed,
            "failed": failed,
        }

    # ═══════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════

    def get_sandbox_info(self, session_key: str) -> Optional[Dict[str, Any]]:
        """获取沙箱详细信息。"""
        meta = self._get_meta(session_key)
        if not meta:
            return None

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)

        info = meta.to_dict()
        info["exists"] = os.path.exists(sandbox_path)
        info["sandbox_path"] = os.path.abspath(sandbox_path)

        # 统计磁盘使用
        if info["exists"]:
            info["disk_usage_mb"] = self._get_disk_usage(sandbox_path)
            info["session_count"] = len(self._load_sessions(sandbox_path))
            info["pm2_processes"] = self._get_pm2_count(os.path.join(sandbox_path, ".pm2"))

        return info

    def list_sandboxes(self, status_filter: str = None) -> List[Dict[str, Any]]:
        """列出所有沙箱，可按状态过滤。"""
        result = []
        for session_key, meta in self._index.items():
            if status_filter and meta.status != status_filter:
                continue
            info = meta.to_dict()
            info["exists"] = os.path.exists(
                os.path.join(self.root_dir, meta.user_dir))
            result.append(info)
        return sorted(result, key=lambda x: x["last_active"], reverse=True)

    def get_stats(self) -> Dict[str, Any]:
        """获取沙箱系统统计。"""
        active = self._count_by_status("active")
        idle = self._count_by_status("idle")
        archived = self._count_by_status("archived")
        return {
            "total_sandboxes": len(self._index),
            "active": active,
            "idle": idle,
            "archived": archived,
            "max_sandboxes": self.max_sandboxes,
            "root_dir": self.root_dir,
            "disk_usage_mb_total": self._get_disk_usage(self.root_dir),
        }

    # ═══════════════════════════════════════════════════════════
    # MCP 服务管理
    # ═══════════════════════════════════════════════════════════

    def set_trusted_mcp_servers(self, session_key: str,
                                 servers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """设置沙箱信任的外部 MCP 服务器列表。"""
        meta = self._get_meta(session_key)
        if not meta:
            return {"status": "not_found", "session_key": session_key}

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)
        self._write_mcp_config(sandbox_path, servers)
        return {"status": "updated", "server_count": len(servers)}

    def get_trusted_mcp_servers(self, session_key: str) -> Optional[List[Dict[str, Any]]]:
        """获取沙箱信任的外部 MCP 服务器列表。"""
        meta = self._get_meta(session_key)
        if not meta:
            return None

        sandbox_path = os.path.join(self.root_dir, meta.user_dir)
        return self._read_mcp_config(sandbox_path)

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 元数据管理
    # ═══════════════════════════════════════════════════════════

    def _load_index(self):
        """从本地文件和 Redis 加载沙箱索引。"""
        # 本地 index.json
        index_path = os.path.join(self.root_dir, "index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for key, meta_dict in data.items():
                        self._index[key] = SandboxMeta.from_dict(meta_dict)
            except (json.JSONDecodeError, IOError):
                pass

        # Redis 索引（校验/补齐）
        if self._redis:
            try:
                redis_data = self._redis.hgetall("commander:sandbox:index")
                for key, meta_json in redis_data.items():
                    try:
                        meta_dict = json.loads(meta_json)
                        if key not in self._index:
                            self._index[key] = SandboxMeta.from_dict(meta_dict)
                    except (json.JSONDecodeError, KeyError):
                        pass
            except Exception:
                pass

    def _save_index(self):
        """持久化沙箱索引到本地文件和 Redis。"""
        data = {key: meta.to_dict() for key, meta in self._index.items()}

        # 本地文件
        index_path = os.path.join(self.root_dir, "index.json")
        try:
            with open(index_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except IOError:
            pass

        # Redis
        if self._redis:
            try:
                pipe = self._redis.pipeline()
                for key, meta in self._index.items():
                    pipe.hset("commander:sandbox:index", key,
                              json.dumps(meta.to_dict(), ensure_ascii=False))
                # 维护活跃集合
                active_keys = [
                    k for k, m in self._index.items() if m.status == "active"]
                pipe.delete("commander:sandbox:active")
                if active_keys:
                    pipe.sadd("commander:sandbox:active", *active_keys)
                pipe.execute()
            except Exception:
                pass

    def _register_meta(self, meta: SandboxMeta):
        """注册沙箱元数据。"""
        self._index[meta.session_key] = meta
        self._save_index()
        print(f"[SandboxManager] 🏗️ 沙箱已注册: {meta.session_key} → {meta.user_dir}")

    def _update_meta(self, meta: SandboxMeta):
        """更新沙箱元数据。"""
        self._index[meta.session_key] = meta
        self._save_index()

    def _unregister_meta(self, session_key: str):
        """注销沙箱元数据。"""
        self._index.pop(session_key, None)
        self._save_index()
        if self._redis:
            try:
                self._redis.hdel("commander:sandbox:index", session_key)
                self._redis.srem("commander:sandbox:active", session_key)
                self._redis.delete(f"commander:sandbox:{session_key}:state")
            except Exception:
                pass

    def _get_meta(self, session_key: str) -> Optional[SandboxMeta]:
        """获取沙箱元数据。"""
        return self._index.get(session_key)

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 计数
    # ═══════════════════════════════════════════════════════════

    def _count_active(self) -> int:
        return self._count_by_status("active")

    def _count_by_status(self, status: str) -> int:
        return sum(1 for m in self._index.values() if m.status == status)

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — 文件操作
    # ═══════════════════════════════════════════════════════════

    def _write_models_config(self, sandbox_path: str):
        """为沙箱写入模型配置。"""
        config = {
            "providers": {
                "deepseek": {
                    "baseUrl": os.environ.get("LLM_BASE_URL",
                                               "https://api.deepseek.com/v1"),
                    "apiKey": "$DEEPSEEK_API_KEY",
                    "models": [{"id": "deepseek-chat", "name": "deepseek-chat"}]
                }
            }
        }
        config_path = os.path.join(sandbox_path, "models.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def _write_mcp_config(self, sandbox_path: str,
                           servers: List[Dict[str, Any]]):
        """写入 MCP 信任服务列表。"""
        config = {"trusted_servers": servers, "updated_at": datetime.now().isoformat()}
        config_path = os.path.join(sandbox_path, "mcp-servers.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

    def _read_mcp_config(self, sandbox_path: str) -> List[Dict[str, Any]]:
        """读取 MCP 信任服务列表。"""
        config_path = os.path.join(sandbox_path, "mcp-servers.json")
        if not os.path.exists(config_path):
            return []
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("trusted_servers", [])
        except (json.JSONDecodeError, IOError):
            return []

    def _load_sessions(self, sandbox_path: str) -> List[Dict[str, Any]]:
        """加载沙箱的会话上下文。"""
        sessions_dir = os.path.join(sandbox_path, "sessions")
        if not os.path.exists(sessions_dir):
            return []
        sessions = []
        for fname in sorted(os.listdir(sessions_dir)):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(sessions_dir, fname), "r",
                              encoding="utf-8") as f:
                        sessions.append(json.load(f))
                except (json.JSONDecodeError, IOError):
                    pass
        return sessions

    def _get_disk_usage(self, path: str) -> float:
        """获取目录磁盘使用 (MB)。"""
        if not os.path.exists(path):
            return 0.0
        try:
            total = 0
            for dirpath, dirnames, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
            return total / (1024 * 1024)
        except Exception:
            return 0.0

    # ═══════════════════════════════════════════════════════════
    # 内部方法 — PM2 管理
    # ═══════════════════════════════════════════════════════════

    def _init_pm2_instance(self, pm2_home: str):
        """为沙箱初始化独立 PM2 实例。"""
        os.makedirs(pm2_home, exist_ok=True)
        try:
            env = {**os.environ, "PM2_HOME": pm2_home}
            subprocess.run(
                ["pm2", "ping"],
                env=env,
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _stop_pm2_instance(self, pm2_home: str):
        """暂停沙箱的 PM2 实例。"""
        try:
            env = {**os.environ, "PM2_HOME": pm2_home}
            subprocess.run(
                ["pm2", "stop", "all"],
                env=env,
                capture_output=True, timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _kill_pm2_instance(self, pm2_home: str):
        """彻底终止沙箱的 PM2 实例。"""
        try:
            env = {**os.environ, "PM2_HOME": pm2_home}
            # 先 kill 所有进程
            subprocess.run(
                ["pm2", "delete", "all"],
                env=env,
                capture_output=True, timeout=15,
            )
            # 再 kill PM2 daemon
            subprocess.run(
                ["pm2", "kill"],
                env=env,
                capture_output=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    def _get_pm2_count(self, pm2_home: str) -> int:
        """获取 PM2 实例中的进程数。"""
        if not os.path.exists(pm2_home):
            return 0
        try:
            env = {**os.environ, "PM2_HOME": pm2_home}
            result = subprocess.run(
                ["pm2", "jlist"],
                env=env,
                capture_output=True, text=True, timeout=10,
            )
            processes = json.loads(result.stdout)
            return len(processes)
        except (json.JSONDecodeError, subprocess.TimeoutExpired,
                FileNotFoundError):
            return 0


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SandboxManager — 多租户沙箱管理")
    parser.add_argument("action", nargs="?", default="stats",
                        choices=["create", "restore", "destroy", "preserve",
                                 "stats", "list", "cleanup"])
    parser.add_argument("--session-key", type=str, help="沙箱 session_key")
    parser.add_argument("--user", type=str, default="", help="用户标识")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-password", default="")
    args = parser.parse_args()

    mgr = SandboxManager(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
    )

    if args.action == "create":
        result = mgr.create_sandbox(user_identifier=args.user)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "restore":
        if not args.session_key:
            print("错误: 需要 --session-key")
            exit(1)
        result = mgr.restore_sandbox(args.session_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "destroy":
        if not args.session_key:
            print("错误: 需要 --session-key")
            exit(1)
        result = mgr.destroy_sandbox(args.session_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "preserve":
        if not args.session_key:
            print("错误: 需要 --session-key")
            exit(1)
        result = mgr.preserve_sandbox(args.session_key)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "stats":
        result = mgr.get_stats()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "list":
        result = mgr.list_sandboxes()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "cleanup":
        result = mgr.cleanup_expired_sandboxes()
        print(json.dumps(result, indent=2, ensure_ascii=False))
