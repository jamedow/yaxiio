#!/usr/bin/env python3
"""
SandboxManager v2.0 — Docker-in-Docker 沙箱管理
================================================
每个用户/会话 = 一个 Docker 容器，基于 yaxiio-sandbox 镜像。
Yaxiio 和沙箱共享 /opt/lightingMetal（Volume）。

沙箱容器配置:
  - 挂载 /var/run/docker.sock → 可以操作宿主机 Docker（部署/重启）
  - 挂载 /opt/lightingMetal → /app/lightingmetal（共享源码）
  - 挂载独立 workspace volume → /workspace（用户工作区，不污染源码）
  - --memory=4g --cpus=2（资源限制）
  - sleep infinity（保持存活，通过 docker exec 操作）

用法:
  mgr = SandboxManager(image="yaxiio-sandbox:lightingmetal")
  sandbox = mgr.create("user-alice")         # 创建沙箱容器
  mgr.exec(session_key, "mvn package")       # 在沙箱内执行命令
  mgr.exec(session_key, "npm run build")
  mgr.deploy(session_key, target="hk")       # 部署到香港
  mgr.destroy(session_key)                   # 销毁沙箱
"""

import subprocess
import json
import time
import uuid
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

DEFAULT_IMAGE = "yaxiio-sandbox:lightingmetal"
DEFAULT_SANDBOX_ROOT = "/var/tmp/yaxiio-sandboxes"
MAX_SANDBOXES = 30
AUTO_DESTROY_HOURS = 12
MAX_MEMORY = "4g"
MAX_CPUS = "2"

# 挂载配置
HOST_CODE_PATH = "/opt/lightingMetal"
CONTAINER_CODE_PATH = "/app/lightingmetal"
DOCKER_SOCK = "/var/run/docker.sock"


# ═══════════════════════════════════════════════════════════════
# 工具
# ═══════════════════════════════════════════════════════════════

def _short_id(n=8):
    return uuid.uuid4().hex[:n]


def _docker(*args, **kwargs) -> subprocess.CompletedProcess:
    """薄封装 docker 命令，异常透传。"""
    cmd = ["docker"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ═══════════════════════════════════════════════════════════════
# 沙箱元数据
# ═══════════════════════════════════════════════════════════════

class SandboxMeta:
    def __init__(self, session_key, container_name, user_id="",
                 image=DEFAULT_IMAGE, created_at=None, status="running"):
        self.session_key = session_key
        self.container_name = container_name
        self.user_id = user_id
        self.image = image
        self.created_at = created_at or datetime.now().isoformat()
        self.status = status  # running | stopped | destroyed

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()}


# ═══════════════════════════════════════════════════════════════
# SandboxManager
# ═══════════════════════════════════════════════════════════════

class SandboxManager:
    """DinD 多租户沙箱管理器。"""

    def __init__(self,
                 image: str = DEFAULT_IMAGE,
                 code_path: str = HOST_CODE_PATH,
                 workspace_root: str = DEFAULT_SANDBOX_ROOT,
                 max_sandboxes: int = MAX_SANDBOXES,
                 auto_destroy_hours: int = AUTO_DESTROY_HOURS):
        self.image = image
        self.code_path = code_path
        self.workspace_root = workspace_root
        self.max_sandboxes = max_sandboxes
        self.auto_destroy_hours = auto_destroy_hours

        os.makedirs(self.workspace_root, exist_ok=True)
        self._index: Dict[str, SandboxMeta] = {}
        self._load_index()

    # ═══════════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════════

    def create(self, user_id: str = "",
               session_key: str = None,
               image: str = None,
               env: Dict[str, str] = None) -> Dict[str, Any]:
        """创建沙箱容器。

        Args:
            user_id: 用户标识
            session_key: 指定 session_key（不传则自动生成）
            image: 指定镜像（覆盖默认）
            env: 额外环境变量

        Returns:
            {"session_key", "container_name", "status": "created"}
        """
        # 容量检查
        if self._count_running() >= self.max_sandboxes:
            return {"status": "rejected",
                    "reason": f"沙箱数量已达上限 ({self.max_sandboxes})"}

        session_key = session_key or _short_id(12)
        container_name = f"yaxiio-sandbox-{_short_id(6)}"
        workspace_vol = f"yaxiio-workspace-{session_key}"
        image = image or self.image

        # 创建 workspace volume（如果不存在）
        _docker("volume", "create", workspace_vol, timeout=10)

        # 构建 docker run 命令
        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
            "-v", f"{DOCKER_SOCK}:{DOCKER_SOCK}",
            "-v", f"{self.code_path}:{CONTAINER_CODE_PATH}",
            "-v", f"{workspace_vol}:/workspace",
            "--memory", MAX_MEMORY,
            "--cpus", MAX_CPUS,
            "--hostname", f"sandbox-{session_key[:8]}",
        ]

        # 自定义环境变量
        env = env or {}
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]

        cmd += [image, "sleep", "infinity"]

        # 执行
        result = _docker(*cmd[1:], timeout=30)

        if result.returncode != 0:
            return {"status": "error",
                    "reason": "docker run 失败",
                    "stderr": result.stderr[:500]}

        # 注册
        meta = SandboxMeta(
            session_key=session_key,
            container_name=container_name,
            user_id=user_id,
            image=image,
        )
        self._index[session_key] = meta
        self._save_index()

        print(f"[SandboxManager] 🏗️ 沙箱已创建: {session_key} → {container_name}")

        return {
            "status": "created",
            "session_key": session_key,
            "container_name": container_name,
            "image": image,
            "created_at": meta.created_at,
        }

    def destroy(self, session_key: str) -> Dict[str, Any]:
        """销毁沙箱容器 + workspace volume。"""
        meta = self._index.pop(session_key, None)
        if not meta:
            return {"status": "not_found", "session_key": session_key}

        container_name = meta.container_name
        workspace_vol = f"yaxiio-workspace-{session_key}"

        errors = []

        # 停止并删除容器
        r = _docker("rm", "-f", container_name, timeout=15)
        if r.returncode != 0:
            errors.append(f"docker rm failed: {r.stderr[:200]}")

        # 删除 workspace volume
        r = _docker("volume", "rm", workspace_vol, timeout=10)
        if r.returncode != 0:
            errors.append(f"volume rm failed: {r.stderr[:200]}")

        self._save_index()
        print(f"[SandboxManager] 🗑️ 沙箱已销毁: {session_key}")

        return {
            "status": "destroyed" if not errors else "destroy_with_errors",
            "session_key": session_key,
            "errors": errors if errors else None,
        }

    # ═══════════════════════════════════════════════════════════
    # 命令执行（核心）
    # ═══════════════════════════════════════════════════════════

    def exec(self, session_key: str, command: str,
             timeout: int = 120, workdir: str = None) -> Dict[str, Any]:
        """在沙箱内执行命令。

        Args:
            session_key: 沙箱标识
            command: 要执行的命令（shell 格式）
            timeout: 超时秒数
            workdir: 工作目录（默认 /workspace）

        Returns:
            {"status": "success"|"error", "stdout", "stderr", "exit_code"}
        """
        meta = self._index.get(session_key)
        if not meta:
            return {"status": "not_found",
                    "session_key": session_key,
                    "error": "沙箱不存在"}

        container = meta.container_name

        # 检查容器是否在运行
        r = _docker("inspect", "-f", "{{.State.Running}}", container, timeout=10)
        if r.stdout.strip() != "true":
            return {"status": "error",
                    "session_key": session_key,
                    "error": f"容器未运行 (状态: {r.stdout.strip()})"}

        # 构建 exec 命令
        cmd = ["docker", "exec"]
        if workdir:
            cmd += ["-w", workdir]
        cmd += [container, "bash", "-c", command]

        try:
            r = _docker(*cmd[1:], timeout=timeout)
            return {
                "status": "success" if r.returncode == 0 else "error",
                "exit_code": r.returncode,
                "stdout": r.stdout[-5000:],
                "stderr": r.stderr[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "session_key": session_key,
                "error": f"命令超时 ({timeout}s)",
            }

    # ═══════════════════════════════════════════════════════════
    # 便捷操作
    # ═══════════════════════════════════════════════════════════

    def build_frontend(self, session_key: str) -> Dict[str, Any]:
        """构建 Nuxt 前端。"""
        return self.exec(session_key,
                         "cd /app/lightingmetal/customer-portal && npm install && npm run build",
                         timeout=300,
                         workdir="/app/lightingmetal/customer-portal")

    def build_backend(self, session_key: str) -> Dict[str, Any]:
        """构建 Spring Boot 后端。"""
        return self.exec(session_key,
                         "cd /app/lightingmetal/service-backend && mvn package -DskipTests",
                         timeout=300,
                         workdir="/app/lightingmetal/service-backend")

    def restart_service(self, session_key: str,
                        service: str = "lightingmetal-backend") -> Dict[str, Any]:
        """重启宿主机上的 Docker 容器。"""
        return self.exec(session_key,
                         f"docker restart {service}",
                         timeout=30)

    def deploy_hk(self, session_key: str) -> Dict[str, Any]:
        """SSH 部署到香港服务器。

        凭证从环境变量读取（由 Commander 在创建沙箱时注入）。
        """
        return self.exec(session_key, """
            DEPLOY_HOST="${DEPLOY_HOST:-}"
            DEPLOY_PASSWORD="${DEPLOY_PASSWORD:-}"
            if [ -z "$DEPLOY_HOST" ] || [ -z "$DEPLOY_PASSWORD" ]; then
                echo "错误: 未配置部署凭证 (DEPLOY_HOST/DEPLOY_PASSWORD)"
                exit 1
            fi
            cd /app/lightingmetal/customer-portal
            npm run build
            tar czf /tmp/deploy.tar.gz .output/
            sshpass -p "$DEPLOY_PASSWORD" scp /tmp/deploy.tar.gz root@$DEPLOY_HOST:/tmp/
            sshpass -p "$DEPLOY_PASSWORD" ssh root@$DEPLOY_HOST "
                cd /root/customer-portal
                tar xzf /tmp/deploy.tar.gz
                docker restart nuxt-app
            "
            echo "✅ 部署完成"
        """, timeout=300)

    def start_dev_server(self, session_key: str,
                         service: str = "customer-portal") -> Dict[str, Any]:
        """启动开发服务器（用于调试）。"""
        if service == "customer-portal":
            return self.exec(session_key,
                             "cd /app/lightingmetal/customer-portal && npm run dev",
                             timeout=30,
                             workdir="/app/lightingmetal/customer-portal")
        elif service == "ai-server":
            return self.exec(session_key,
                             "cd /app/lightingmetal/ai-server && node src/index.js",
                             timeout=30,
                             workdir="/app/lightingmetal/ai-server")
        elif service == "backend":
            return self.exec(session_key,
                             "cd /app/lightingmetal/service-backend && mvn spring-boot:run",
                             timeout=30,
                             workdir="/app/lightingmetal/service-backend")
        else:
            return {"status": "error", "error": f"未知服务: {service}"}

    # ═══════════════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════════════

    def status(self, session_key: str = None) -> Dict[str, Any]:
        """查询沙箱状态。如果指定 session_key 返回单个，否则返回全部。"""
        if session_key:
            meta = self._index.get(session_key)
            if not meta:
                return {"status": "not_found"}
            return self._container_status(meta)

        result = {}
        for key, meta in self._index.items():
            result[key] = self._container_status(meta)
        return {"count": len(result), "sandboxes": result}

    def list(self) -> List[Dict[str, Any]]:
        """列出所有沙箱。"""
        return [self._container_status(m) for m in self._index.values()]

    def cleanup_expired(self) -> Dict[str, Any]:
        """清理过期沙箱。"""
        destroyed = []
        now = datetime.now()
        for key, meta in list(self._index.items()):
            created = datetime.fromisoformat(meta.created_at)
            if (now - created) > timedelta(hours=self.auto_destroy_hours):
                r = self.destroy(key)
                destroyed.append(r)

        return {"cleaned": len(destroyed), "details": destroyed}

    # ═══════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════

    def _container_status(self, meta: SandboxMeta) -> Dict[str, Any]:
        """获取容器的实时状态。"""
        info = meta.to_dict()
        try:
            r = _docker("inspect",
                        "-f", "{{.State.Status}} {{.State.StartedAt}}",
                        meta.container_name, timeout=10)
            if r.returncode == 0:
                parts = r.stdout.strip().split(" ", 1)
                info["container_status"] = parts[0]
                info["started_at"] = parts[1] if len(parts) > 1 else ""
            else:
                info["container_status"] = "not_found"
        except Exception:
            info["container_status"] = "unknown"
        return info

    def _count_running(self) -> int:
        return len(self._index)

    def _load_index(self):
        """从本地文件加载索引。"""
        idx_path = os.path.join(self.workspace_root, "sandbox-index.json")
        if os.path.exists(idx_path):
            try:
                with open(idx_path) as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._index[k] = SandboxMeta(**v)
            except (json.JSONDecodeError, TypeError):
                pass

    def _save_index(self):
        """持久化索引。"""
        idx_path = os.path.join(self.workspace_root, "sandbox-index.json")
        with open(idx_path, "w") as f:
            json.dump(
                {k: v.to_dict() for k, v in self._index.items()},
                f, indent=2, ensure_ascii=False,
            )


# ═══════════════════════════════════════════════════════════════
# CLI 入口（独立运行 / 测试）
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="SandboxManager v2.0 — DinD 沙箱管理")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有沙箱")

    c = sub.add_parser("create", help="创建沙箱")
    c.add_argument("--user", default="")
    c.add_argument("--image", default=None)

    d = sub.add_parser("destroy", help="销毁沙箱")
    d.add_argument("--key", required=True)

    e = sub.add_parser("exec", help="在沙箱内执行命令")
    e.add_argument("--key", required=True)
    e.add_argument("--cmd", required=True)

    sub.add_parser("cleanup", help="清理过期沙箱")

    args = p.parse_args()
    mgr = SandboxManager()

    if args.cmd == "list":
        print(json.dumps(mgr.list(), indent=2, ensure_ascii=False))
    elif args.cmd == "create":
        print(json.dumps(mgr.create(user_id=args.user, image=args.image),
                         indent=2, ensure_ascii=False))
    elif args.cmd == "destroy":
        print(json.dumps(mgr.destroy(args.key), indent=2, ensure_ascii=False))
    elif args.cmd == "exec":
        print(json.dumps(mgr.exec(args.key, args.cmd), indent=2, ensure_ascii=False))
    elif args.cmd == "cleanup":
        print(json.dumps(mgr.cleanup_expired(), indent=2, ensure_ascii=False))
    else:
        # 默认：创建测试沙箱
        print("=== 创建测试沙箱 ===")
        r = mgr.create("test")
        print(json.dumps(r, indent=2, ensure_ascii=False))
        key = r.get("session_key")
        if key:
            print("\n=== 执行测试命令 ===")
            print(json.dumps(mgr.exec(key, "echo hello && java -version 2>&1 | head -1"),
                             indent=2, ensure_ascii=False))
            print("\n=== 销毁 ===")
            print(json.dumps(mgr.destroy(key), indent=2, ensure_ascii=False))
