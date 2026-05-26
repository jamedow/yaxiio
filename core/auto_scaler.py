#!/usr/bin/env python3
"""
优化二：Agent 弹性伸缩管理 — AutoScaler
=========================================
基于等待队列长度 + Agent空闲时间，自动扩缩容。
- 队列 ≥ 3 → 扩容（每次 1~2 个，上限 10）
- 空闲 > 600s → 缩容（保留至少 2 个核心 Agent）

Constitution R2: 上限10个；R1: 使用 commander:* 前缀，TTL 自动回收。
"""

import json
import os
import subprocess
import time
from typing import Optional

import redis

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class AutoScaler:
    """弹性扩缩容管理器"""

    # 核心Agent（缩容时至少保留2个）
    CORE_AGENTS = {"翻译官", "商务经理", "售前经理"}

    def __init__(self, redis_host: str = "127.0.0.1", redis_port: int = 6379,
                 redis_password: str = "Lt@114514!",
                 max_agents: int = 10,
                 scale_up_threshold: int = 3,
                 scale_down_seconds: int = 600):
        self.redis = redis.Redis(
            host=redis_host, port=redis_port,
            password=redis_password, decode_responses=True,
        )
        self.max_agents = max_agents
        self.scale_up_threshold = scale_up_threshold
        self.scale_down_seconds = scale_down_seconds
        self._agent_script = "/app/.pi/agents/runtime/agent.sh"

    # ── 主入口 ───────────────────────────────────────────────

    def check_and_scale(self) -> dict:
        """检测当前负载，自动扩容/缩容。

        Returns:
            {"action": "scale_up"/"scale_down"/"no_change", details...}
        """
        queue_length = self.redis.llen("commander:task_queue")
        active_agents = self._get_active_agents()

        # ── 扩容逻辑 ──
        if queue_length >= self.scale_up_threshold and len(active_agents) < self.max_agents:
            needed = min(max(1, queue_length // 2), self.max_agents - len(active_agents))
            spawned = []
            for _ in range(needed):
                agent_id = self._spawn_agent()
                if agent_id:
                    spawned.append(agent_id)
            if spawned:
                return {
                    "action": "scale_up",
                    "spawned": len(spawned),
                    "agent_ids": spawned,
                    "reason": f"等待队列 {queue_length} >= 阈值 {self.scale_up_threshold}",
                }

        # ── 缩容逻辑 ──
        idle_agents = self._get_idle_agents(active_agents)
        if idle_agents:
            # 保护核心 Agent：非核心 Agent 优先销毁，核心 Agent 保留至少2个
            core_alive = [a for a in active_agents if a in self.CORE_AGENTS]
            non_core_idle = [a for a in idle_agents if a not in self.CORE_AGENTS]
            core_idle = [a for a in idle_agents if a in self.CORE_AGENTS]

            # 先销毁非核心空闲 Agent
            to_destroy = list(non_core_idle)

            # 如果核心 Agent > 2，多余的也可以缩容
            if len(core_alive) > 2:
                to_destroy.extend(core_idle[:len(core_alive) - 2])

            destroyed = []
            for agent_id in to_destroy:
                if self._destroy_agent(agent_id):
                    destroyed.append(agent_id)

            if destroyed:
                return {
                    "action": "scale_down",
                    "destroyed": len(destroyed),
                    "agent_ids": destroyed,
                    "reason": f"空闲超过 {self.scale_down_seconds} 秒",
                }

        return {"action": "no_change"}

    # ── 内部方法 ─────────────────────────────────────────────

    def _get_active_agents(self) -> set:
        """获取当前活跃 Agent 集合。"""
        members = self.redis.smembers("commander:agents:active")
        # 过滤掉心跳已过期的（TTL到期自动消失的视为离线）
        alive = set()
        for m in members:
            status = self.redis.get(f"commander:agent:status:{m}")
            if status == "running":
                alive.add(m)
        return alive

    def _get_idle_agents(self, active_agents: set) -> list:
        """返回空闲超过阈值的 Agent 列表。"""
        idle = []
        for agent_id in active_agents:
            last_activity = float(
                self.redis.hget(f"commander:agent:heartbeat:{agent_id}", "last_activity") or 0
            )
            if last_activity and time.time() - last_activity > self.scale_down_seconds:
                idle.append(agent_id)
        return idle

    def _spawn_agent(self, role: str = "翻译官") -> Optional[str]:
        """通过 PM2 创建新的 Agent 实例。"""
        counter = self.redis.incr("commander:agent:counter")
        agent_id = f"agent-dynamic-{int(time.time())}-{counter}"

        try:
            result = subprocess.run(
                [
                    "pm2", "start", self._agent_script,
                    "--name", agent_id,
                    "--", role,
                ],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 or "online" in result.stdout.lower():
                # 注册到活跃集合
                self.redis.sadd("commander:agents:active", agent_id)
                self.redis.setex(f"commander:agent:status:{agent_id}", 3600, "running")
                self.redis.hset(
                    f"commander:agent:heartbeat:{agent_id}",
                    mapping={"last_activity": str(time.time()), "role": role},
                )
                return agent_id
            else:
                print(f"[AutoScaler] 创建 Agent 失败: {result.stderr}")
                return None
        except Exception as e:
            print(f"[AutoScaler] 创建 Agent 异常: {e}")
            return None

    def _destroy_agent(self, agent_id: str) -> bool:
        """销毁 Agent 实例（PM2 stop + delete）。"""
        try:
            # 发送 shutdown 指令到命令队列
            self.redis.rpush(f"commander:agent:command:{agent_id}", json.dumps({
                "type": "shutdown",
                "timestamp": time.time(),
            }))
            # 设置 TTL 让命令自动过期（避免堆积）
            self.redis.expire(f"commander:agent:command:{agent_id}", 60)

            # PM2 停止+删除
            subprocess.run(["pm2", "stop", agent_id], capture_output=True, timeout=10)
            subprocess.run(["pm2", "delete", agent_id], capture_output=True, timeout=10)

            # 从活跃集合移除（SREM 而非 DEL，遵守R1）
            self.redis.srem("commander:agents:active", agent_id)
            # status key 设短TTL让其自动过期
            self.redis.expire(f"commander:agent:status:{agent_id}", 10)
            self.redis.expire(f"commander:agent:heartbeat:{agent_id}", 10)
            return True
        except Exception as e:
            print(f"[AutoScaler] 销毁 Agent 失败: {e}")
            return False

    # ── 工具方法 ─────────────────────────────────────────────

    def get_queue_depth(self) -> int:
        """获取当前任务队列长度。"""
        return self.redis.llen("commander:task_queue")

    def enqueue_task(self, task_payload: dict):
        """将任务加入等待队列。"""
        self.redis.rpush("commander:task_queue", json.dumps(task_payload, ensure_ascii=False))

    def dequeue_task(self) -> Optional[dict]:
        """从等待队列取出一个任务。"""
        task = self.redis.lpop("commander:task_queue")
        return json.loads(task) if task else None
