# Copyright 2026 LightingMetal
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#!/usr/bin/env python3
"""
Skill 动态挂载管理器 — Commander 扩展系统 1/3
================================================
让 Commander 能自主为 Agent 安装、卸载、启用、禁用 Skill。
基于 Pi 生态的 npm 包管理 + 本地 .pi/skills/ 目录。

Constitution R1: 使用 skills:* / agent:skills:* 前缀，不碰 page:*/lightingmetal:*
Constitution R2: Agent 上限由 AutoScaler + SafetyBoundary 控制

集成点：
  - CommanderV2.handle_task() → ExtensionRouter → SkillManager.install_skill()
  - AgentLifecycleManagerV2.create_agent() → SkillManager.enable_skill_for_agent()
  - 定期评估 → SkillManager.search_skill() 发现新能力

v1.0 | 2026-05-24 | 初始版本
"""

import asyncio
import json
import subprocess
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import redis as redis_lib


class SkillManager:
    """Skill 生命周期管理器。

    管理 Skill 的安装/卸载/启用/禁用，持久化到 Redis + MongoDB。
    支持三种来源：npm（推荐）、github、local（.pi/skills/）。
    """

    # Redis 前缀（遵守 Constitution R1）
    KEY_REGISTRY = "skills:registry"            # Hash: skill_name → metadata JSON
    KEY_AGENT_SKILLS = "agent:skills:{agent}"   # Set: agent → skill_names
    KEY_AGENT_STATUS = "agent:skill_status:{agent}"  # Hash: skill_name → enabled/disabled
    KEY_GLOBAL_TOGGLE = "skills:global_toggle"  # Hash: skill_name → enabled/disabled

    def __init__(
        self,
        redis_client: redis_lib.Redis,
        mongo_client=None,
        pi_config_path: str = ".pi",
    ):
        self.redis = redis_client
        self.mongo = mongo_client
        self.pi_config_path = Path(pi_config_path)
        self.skills_dir = self.pi_config_path / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    # ── 安装 / 卸载 ──────────────────────────────────────────

    async def install_skill(
        self,
        skill_name: str,
        source: str = "npm",
        version: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict:
        """安装一个 Skill。

        Args:
            skill_name: Skill 名称（如 'translate-engine'）
            source: 来源类型 npm | github | local
            version: 版本号（可选，仅 npm 有效）
            agent_id: 目标 Agent ID（None 则全局安装）

        Returns:
            {skill_name, source, action, agent_id, timestamp, status, [error]}
        """
        install_log = {
            "skill_name": skill_name,
            "source": source,
            "action": "install",
            "agent_id": agent_id,
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
        }

        try:
            if source == "npm":
                package_name = (
                    f"@lightingmetal/skill-{skill_name}"
                    if not skill_name.startswith("@")
                    else skill_name
                )
                cmd = ["npm", "install"]
                if version:
                    cmd.append(f"{package_name}@{version}")
                else:
                    cmd.append(package_name)

                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(self.pi_config_path),
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=120
                )

                if proc.returncode == 0:
                    install_log["status"] = "success"
                    install_log["output"] = stdout.decode()[:500]
                    self._register_in_redis(skill_name, version, agent_id)
                else:
                    install_log["status"] = "failed"
                    install_log["error"] = stderr.decode()[:500]

            elif source == "github":
                # 通过 git clone 到本地 skills 目录（不依赖 npm）
                skill_path = self.skills_dir / skill_name
                repo_url = (
                    f"https://github.com/{skill_name}"
                    if "/" in skill_name
                    else f"https://github.com/lightingmetal/skill-{skill_name}"
                )
                proc = await asyncio.create_subprocess_exec(
                    "git", "clone", "--depth", "1", repo_url, str(skill_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )

                if proc.returncode == 0:
                    install_log["status"] = "success"
                    install_log["output"] = f"从 github 克隆: {repo_url} → {skill_path}"
                    self._register_in_redis(skill_name, version, agent_id, source="github")
                else:
                    install_log["status"] = "failed"
                    install_log["error"] = stderr.decode()[:500]

            elif source == "local":
                skill_path = self.skills_dir / skill_name
                if skill_path.exists() and (skill_path / "SKILL.md").exists():
                    install_log["status"] = "success"
                    install_log["output"] = f"从本地加载: {skill_path}"
                    self._register_in_redis(skill_name, version, agent_id, source="local")
                else:
                    install_log["status"] = "failed"
                    install_log["error"] = (
                        f"本地 Skill 不存在或缺少 SKILL.md: {skill_path}"
                    )

            else:
                install_log["status"] = "failed"
                install_log["error"] = f"不支持的来源类型: {source}"

        except asyncio.TimeoutError:
            install_log["status"] = "failed"
            install_log["error"] = "安装超时（120s）"
        except Exception as e:
            install_log["status"] = "failed"
            install_log["error"] = str(e)

        # 持久化到 MongoDB
        self._persist_log("skill_install_logs", install_log)
        return install_log

    async def uninstall_skill(self, skill_name: str) -> Dict:
        """卸载一个 Skill（npm + github 模式清理，local 仅取消注册）。"""
        result = {
            "skill_name": skill_name,
            "action": "uninstall",
            "timestamp": datetime.now().isoformat(),
            "status": "pending",
        }

        try:
            # 获取注册信息来决定清理方式
            existing = self.redis.hget(self.KEY_REGISTRY, skill_name)
            if existing:
                meta = json.loads(existing.decode() if isinstance(existing, bytes) else existing)
                source = meta.get("source", "npm")

                if source == "npm":
                    package_name = (
                        f"@lightingmetal/skill-{skill_name}"
                        if not skill_name.startswith("@")
                        else skill_name
                    )
                    proc = await asyncio.create_subprocess_exec(
                        "npm", "uninstall", package_name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=str(self.pi_config_path),
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=60)

                elif source == "github":
                    skill_path = self.skills_dir / skill_name
                    if skill_path.exists():
                        import shutil
                        shutil.rmtree(str(skill_path), ignore_errors=True)

            # 清理 Redis
            self.redis.hdel(self.KEY_REGISTRY, skill_name)
            self.redis.hdel(self.KEY_GLOBAL_TOGGLE, skill_name)

            result["status"] = "success"

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)

        self._persist_log("skill_install_logs", result)
        return result

    # ── 启用 / 禁用（按 Agent 粒度）──────────────────────────

    async def enable_skill_for_agent(self, skill_name: str, agent_id: str) -> Dict:
        """为指定 Agent 启用某个 Skill。"""
        self.redis.sadd(self.KEY_AGENT_SKILLS.format(agent=agent_id), skill_name)
        self.redis.hset(
            self.KEY_AGENT_STATUS.format(agent=agent_id),
            skill_name,
            "enabled",
        )
        return {
            "agent_id": agent_id,
            "skill_name": skill_name,
            "status": "enabled",
            "timestamp": datetime.now().isoformat(),
        }

    async def disable_skill_for_agent(self, skill_name: str, agent_id: str) -> Dict:
        """为指定 Agent 禁用某个 Skill（不卸载，仅标记）。"""
        self.redis.srem(self.KEY_AGENT_SKILLS.format(agent=agent_id), skill_name)
        self.redis.hset(
            self.KEY_AGENT_STATUS.format(agent=agent_id),
            skill_name,
            "disabled",
        )
        return {
            "agent_id": agent_id,
            "skill_name": skill_name,
            "status": "disabled",
            "timestamp": datetime.now().isoformat(),
        }

    async def set_global_toggle(self, skill_name: str, enabled: bool) -> Dict:
        """全局启用/禁用 Skill（影响所有 Agent）。"""
        status = "enabled" if enabled else "disabled"
        self.redis.hset(self.KEY_GLOBAL_TOGGLE, skill_name, status)
        return {"skill_name": skill_name, "status": status}

    # ── 查询 ──────────────────────────────────────────────────

    def get_agent_skills(self, agent_id: str) -> List[str]:
        """获取某个 Agent 已启用的 Skill 列表。"""
        skills = self.redis.smembers(self.KEY_AGENT_SKILLS.format(agent=agent_id))
        return [s.decode() if isinstance(s, bytes) else s for s in skills]

    def get_agent_skill_status(self, agent_id: str) -> Dict[str, str]:
        """获取 Agent 的所有 Skill 及状态。"""
        items = self.redis.hgetall(self.KEY_AGENT_STATUS.format(agent=agent_id))
        return {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in items.items()
        }

    def get_global_skills(self) -> List[Dict]:
        """获取全局已注册的所有 Skill 及元数据。"""
        all_skills = self.redis.hgetall(self.KEY_REGISTRY)
        result = []
        for k, v in all_skills.items():
            name = k.decode() if isinstance(k, bytes) else k
            meta = json.loads(v.decode() if isinstance(v, bytes) else v)
            # 附加全局开关状态
            toggle = self.redis.hget(self.KEY_GLOBAL_TOGGLE, name)
            meta["global_enabled"] = (
                toggle.decode() if isinstance(toggle, bytes) else toggle
            ) != "disabled"
            result.append({"name": name, **meta})
        return result

    async def search_skill(self, capability: str) -> List[str]:
        """根据能力描述搜索合适的 Skill。

        Commander 调用此方法来发现能满足需求的 Skill。
        策略：先精确匹配名称 → 描述关键词匹配 → 返回候选列表。
        """
        all_skills = self.get_global_skills()
        matching = []

        # 1. 名称精确匹配
        for skill in all_skills:
            if capability.lower() == skill["name"].lower():
                matching.append(skill["name"])

        if matching:
            return matching

        # 2. 关键词匹配（双向：capability 关键词在 skill 名称中，反之亦然）
        cap_keywords = set(capability.lower().replace("-", " ").replace("_", " ").split())
        for skill in all_skills:
            skill_keywords = set(
                skill["name"].lower().replace("-", " ").replace("_", " ").split()
            )
            # 检测交叉
            overlap = cap_keywords & skill_keywords
            if overlap or any(kw in skill["name"].lower() for kw in cap_keywords):
                matching.append(skill["name"])

        return matching

    # ── 内部方法 ──────────────────────────────────────────────

    def _register_in_redis(
        self, skill_name: str, version: Optional[str], agent_id: Optional[str],
        source: str = "npm",
    ):
        """在 Redis 中注册 Skill 元数据。"""
        meta = {
            "installed_at": datetime.now().isoformat(),
            "version": version or "latest",
            "source": source,
            "agent_id": agent_id or "global",
        }
        self.redis.hset(self.KEY_REGISTRY, skill_name, json.dumps(meta, ensure_ascii=False))

        # 如果指定了 agent，自动为该 agent 启用
        if agent_id:
            self.redis.sadd(self.KEY_AGENT_SKILLS.format(agent=agent_id), skill_name)
            self.redis.hset(
                self.KEY_AGENT_STATUS.format(agent=agent_id),
                skill_name,
                "enabled",
            )

    def _persist_log(self, collection: str, log: Dict):
        """持久化操作日志到 MongoDB（非关键路径，失败不抛异常）。"""
        if self.mongo is None:
            return
        try:
            self.mongo[collection].insert_one(log)
        except Exception as e:
            print(f"[SkillManager] MongoDB 写入 {collection} 失败: {e}")


# ═══════════════════════════════════════════════════════════════
# 与现有 Skill 生态的适配器
# ═══════════════════════════════════════════════════════════════

class LocalSkillAdapter:
    """适配 Pi 文件系统 Skill（.pi/skills/*/SKILL.md）到 SkillManager 注册表。

    用于引导阶段：将已存在的本地 Skill 批量注册到 Redis，
    使得 SkillManager.get_global_skills() 能查到它们。
    """

    def __init__(self, skill_manager: SkillManager, skills_dir: str = ".pi/skills"):
        self.manager = skill_manager
        self.skills_dir = Path(skills_dir)

    def bootstrap_local_skills(self) -> List[str]:
        """扫描 .pi/skills/ 目录，将已有的 Skill 注册到 Redis。

        Returns:
            注册成功的 Skill 名称列表。
        """
        registered = []
        if not self.skills_dir.exists():
            return registered

        for entry in sorted(self.skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue

            skill_name = entry.name
            # 读取 SKILL.md 获取元数据
            description = ""
            try:
                with open(skill_md, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("description:"):
                            description = line.split(":", 1)[1].strip()
                            break
            except Exception:
                pass

            meta = {
                "installed_at": datetime.fromtimestamp(
                    skill_md.stat().st_mtime
                ).isoformat(),
                "version": "local",
                "source": "local",
                "agent_id": "global",
                "description": description,
            }
            self.manager.redis.hset(
                self.manager.KEY_REGISTRY,
                skill_name,
                json.dumps(meta, ensure_ascii=False),
            )
            registered.append(skill_name)

        return registered
