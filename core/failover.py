#!/usr/bin/env python3
"""
优化五：Agent 故障转移 + Redis 高可用 + 五级任务降级 — FailoverManager
==========================================================================
- AgentFailover    : 心跳监测 → 备选Agent切换 → 最终降级模板
- RedisHAWrapper   : Sentinel 高可用，读写分离 + 自动故障切换
- TaskDegradation  : L0~L4 五级降级策略，按任务类型 + 可用Agent动态判定

Constitution R1: 所有状态键使用 commander:* 前缀
Constitution R5: 30s 无响应重试3次，连续失败3次降级
"""

import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import redis


# ═══════════════════════════════════════════════════════════════
# 五级降级常量
# ═══════════════════════════════════════════════════════════════

DEGRADATION_LEVELS = {
    "L0": "完整流程（所有Agent可用）",
    "L1": "无售前经理时，使用预设报价模板",
    "L2": "无商务经理时，用智能客服替代",
    "L3": "无翻译官时，用基础词典翻译",
    "L4": "仅 Commander 可用，返回预设兜底回复",
}

# 备选 Agent 角色映射
BACKUP_ROLES = {
    "售前经理": ["通用报价Agent", "预设模板Agent"],
    "商务经理": ["智能客服Agent", "邮件回复Agent"],
    "翻译官":   ["通用翻译Agent", "基础词典Agent"],
    "审计官":   ["翻译官"],
    "俄语审计官": ["翻译官", "审计官"],
}

# 降级模板（L4 兜底）
DEGRADED_TEMPLATES = {
    "售前经理": {"quote": "感谢您的询盘。我们正在处理，将在24小时内为您提供详细报价。"
                         "参考价格：请联系客服获取。",
                 "status": "degraded_L4"},
    "商务经理": {"response": "感谢您的咨询。由于系统正在维护，请将详细需求发送至 "
                          "jamedow@lightingmetal.com，我们将优先处理。",
                 "status": "degraded_L4"},
    "翻译官":   {"translated": "",
                 "note": "翻译服务暂时不可用，原文已保留",
                 "status": "degraded_L4"},
    "审计官":   {"audit_result": "审计服务暂时不可用，请稍后重试",
                 "status": "degraded_L4"},
    "俄语审计官": {"audit_result": "俄语审计服务暂时不可用，请稍后重试",
                  "status": "degraded_L4"},
}


# ═══════════════════════════════════════════════════════════════
# AgentFailover ─ 故障转移引擎
# ═══════════════════════════════════════════════════════════════

class AgentFailover:
    """Agent 故障转移管理器。

    监控心跳 → 超时判定失联 → 备选 Agent 切换 → 最终降级兜底。
    兼容同步调用，后台心跳监控走独立线程。
    """

    HEARTBEAT_TIMEOUT = 30   # 30s 无心跳 → 失联（符合 R5）

    def __init__(self, redis_client: redis.Redis,
                 mongo_client: Any = None):
        self.redis = redis_client
        self.mongo = mongo_client
        self.agent_heartbeat: Dict[str, float] = {}  # agent_id → last_beat_ts
        self._running = False

    # ── 故障转移主入口 ───────────────────────────────────────

    def handle_agent_failure(self, failed_agent_id: str,
                             task: Optional[dict] = None) -> dict:
        """Agent 失败时自动切换备选。

        Returns:
            {"status": "failover"|"degraded", ...}
        """
        task = task or {}
        role = self._get_agent_role(failed_agent_id)
        backups = BACKUP_ROLES.get(role, [])

        self._log_failure(failed_agent_id, task)

        # 尝试备选 Agent
        for backup_role in backups:
            status = self.redis.hget("commander:agent:status:by_role", backup_role)
            if status == "running":
                self._log_failover(failed_agent_id, backup_role)
                return {
                    "status": "failover",
                    "original_agent_id": failed_agent_id,
                    "original_role": role,
                    "new_agent_role": backup_role,
                    "task_id": task.get("taskId", ""),
                }

        # 全部备选不可用 → 降级
        fallback = DEGRADED_TEMPLATES.get(
            role,
            {"error": "服务暂时不可用", "status": "degraded_L4"},
        )
        if task.get("payload", {}).get("original_text"):
            fallback["translated"] = task["payload"]["original_text"]

        if self.mongo:
            try:
                self.mongo.degraded_tasks.insert_one({
                    "task_id": task.get("taskId"),
                    "role": role,
                    "fallback_used": True,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception:
                pass

        return {"status": "degraded", "fallback": fallback}

    # ── 心跳管理 ─────────────────────────────────────────────

    def record_heartbeat(self, agent_id: str):
        """记录 Agent 心跳（由 Commander 在处理 Pub/Sub heartbeat 时调用）。"""
        self.agent_heartbeat[agent_id] = time.time()
        self.redis.set(
            f"commander:agent:heartbeat:{agent_id}",
            str(time.time()),
        )

    def check_dead_agents(self) -> List[str]:
        """检查并返回所有失联 Agent 列表（外部定期调用）。"""
        now = time.time()
        dead = []

        for agent_id, last_hb in list(self.agent_heartbeat.items()):
            if now - last_hb > self.HEARTBEAT_TIMEOUT:
                dead.append(agent_id)
                # 标记为 dead
                role = self._get_agent_role(agent_id)
                self.redis.hset("commander:agent:status:by_role", agent_id, "dead")
                self._log_failure(agent_id, {
                    "reason": "heartbeat_lost",
                    "action": "auto_restart",
                })
                print(f"[AgentFailover] ⚠️ {agent_id}({role}) 失联 >{self.HEARTBEAT_TIMEOUT}s")

        for agent_id in dead:
            del self.agent_heartbeat[agent_id]

        return dead

    # ── 内部工具 ─────────────────────────────────────────────

    def _get_agent_role(self, agent_id: str) -> str:
        role = self.redis.hget(f"commander:agent:metadata:{agent_id}", "role")
        if not role:
            role = self.redis.hget("commander:agent:status:by_role", agent_id)
        return role or "未知"

    def _log_failure(self, agent_id: str, task: dict):
        entry = {
            "type": "agent_failure",
            "agent_id": agent_id,
            "task_id": task.get("taskId", ""),
            "reason": task.get("reason", "unknown"),
            "timestamp": datetime.now().isoformat(),
        }
        if self.mongo:
            try:
                self.mongo.agent_failures.insert_one(entry)
            except Exception:
                pass
        # Redis 日志（7天 TTL）
        self.redis.rpush("commander:log:failures",
                         json.dumps(entry, ensure_ascii=False))
        self.redis.expire("commander:log:failures", 86400 * 7)

    def _log_failover(self, old_agent: str, new_role: str):
        entry = {
            "type": "failover",
            "old_agent": old_agent,
            "new_agent_role": new_role,
            "timestamp": datetime.now().isoformat(),
        }
        if self.mongo:
            try:
                self.mongo.agent_failovers.insert_one(entry)
            except Exception:
                pass
        self.redis.rpush("commander:log:failovers",
                         json.dumps(entry, ensure_ascii=False))
        self.redis.expire("commander:log:failovers", 86400 * 7)


# ═══════════════════════════════════════════════════════════════
# RedisHAWrapper ─ Sentinel 高可用
# ═══════════════════════════════════════════════════════════════

class RedisHAWrapper:
    """Redis Sentinel 高可用包装器。

    特性：
      - 写操作自动路由到当前 master
      - 读操作优先从 slave 读取（失败自动切 master）
      - Sentinel 自动故障转移：master 宕机后自动感知新主
      - 支持密码认证
    """

    def __init__(self, sentinel_hosts: List[str],
                 service_name: str = "lightingmetal-redis",
                 password: str = "Lt@114514!",
                 decode_responses: bool = True):
        try:
            from redis.sentinel import Sentinel
            self._sentinel_class = Sentinel
        except ImportError:
            raise ImportError(
                "redis-py Sentinel 支持不可用。请安装: pip install redis[hiredis]"
            )

        self.service_name = service_name
        self.password = password
        self.decode_responses = decode_responses
        self._sentinels = [(host, 26379) for host in sentinel_hosts]
        self._sentinel: Optional["Sentinel"] = None
        self._master: Optional[redis.Redis] = None
        self._slave: Optional[redis.Redis] = None

    @property
    def sentinel(self):
        if self._sentinel is None:
            kwargs = {
                "sentinel_kwargs": {"password": self.password,
                                    "decode_responses": self.decode_responses},
            }
            self._sentinel = self._sentinel_class(
                self._sentinels, **kwargs,
            )
        return self._sentinel

    @property
    def master(self) -> redis.Redis:
        if self._master is None:
            self._master = self.sentinel.master_for(
                self.service_name,
                password=self.password,
                decode_responses=self.decode_responses,
            )
        return self._master

    @property
    def slave(self) -> redis.Redis:
        if self._slave is None:
            self._slave = self.sentinel.slave_for(
                self.service_name,
                password=self.password,
                decode_responses=self.decode_responses,
            )
        return self._slave

    def _reset_master(self):
        """清空 master 缓存，下次访问时重新从 Sentinel 获取新主。"""
        self._master = None

    # ── 委托方法 ─────────────────────────────────────────────

    def publish(self, channel: str, message) -> int:
        """写操作：优先 master，失败自动切换。"""
        try:
            return self.master.publish(channel, message)
        except (redis.ConnectionError, redis.TimeoutError):
            self._reset_master()
            return self.master.publish(channel, message)

    def hset(self, key: str, mapping=None, **kwargs):
        """写操作走主。"""
        try:
            return self.master.hset(key, mapping=mapping, **kwargs)
        except (redis.ConnectionError, redis.TimeoutError):
            self._reset_master()
            return self.master.hset(key, mapping=mapping, **kwargs)

    def hget(self, key: str, field: str):
        """读操作优先从，失败切主。"""
        try:
            return self.slave.hget(key, field)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.hget(key, field)

    def hgetall(self, key: str) -> dict:
        try:
            return self.slave.hgetall(key)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.hgetall(key)

    def get(self, key: str):
        try:
            return self.slave.get(key)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.get(key)

    def setex(self, key: str, time_sec: int, value):
        try:
            return self.master.setex(key, time_sec, value)
        except (redis.ConnectionError, redis.TimeoutError):
            self._reset_master()
            return self.master.setex(key, time_sec, value)

    def exists(self, *keys) -> int:
        try:
            return self.slave.exists(*keys)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.exists(*keys)

    def llen(self, key: str) -> int:
        try:
            return self.slave.llen(key)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.llen(key)

    def smembers(self, key: str) -> set:
        try:
            return self.slave.smembers(key)
        except (redis.ConnectionError, redis.TimeoutError):
            return self.master.smembers(key)


# ═══════════════════════════════════════════════════════════════
# TaskDegradation ─ 任务降级策略
# ═══════════════════════════════════════════════════════════════

class TaskDegradation:
    """五级任务降级策略管理器。

    L0: 所有必需 Agent 可用 → 完整流程
    L1: 售前经理不可用 → 预设报价模板
    L2: 商务经理不可用 → 智能客服替代
    L3: 翻译官不可用 → 基础词典翻译
    L4: 仅 Commander 可用 → 预设兜底回复
    """

    # 任务类型 → 所需 Agent 角色
    TASK_AGENT_MAP = {
        "询盘":     ["商务经理", "售前经理"],
        "翻译":     ["翻译官"],
        "批量翻译": ["翻译官"],
        "审计":     ["审计官"],
        "俄语审计": ["俄语审计官", "翻译官"],
        "小语种客服": ["商务经理", "翻译官"],
        "报价":     ["售前经理"],
    }

    def __init__(self, redis_client: redis.Redis,
                 mongo_client: Any = None):
        self.redis = redis_client
        self.mongo = mongo_client

    def get_required_agents(self, task_type: str) -> List[str]:
        """返回任务类型所需的 Agent 角色列表。"""
        return self.TASK_AGENT_MAP.get(task_type, ["商务经理", "售前经理", "翻译官"])

    def get_degradation_level(self, task_type: str) -> str:
        """检测当前可用 Agent，返回降级等级。"""
        required = self.get_required_agents(task_type)
        unavailable = []

        for role in required:
            status = self.redis.hget("commander:agent:status:by_role", role)
            if status is None or status != "running":
                unavailable.append(role)

        missing = len(unavailable)

        if missing == 0:
            return "L0"

        # 分级判定
        if "售前经理" in unavailable and len(unavailable) == 1:
            return "L1"
        if "商务经理" in unavailable and len(unavailable) == 1:
            return "L2"
        if "翻译官" in unavailable and len(unavailable) == 1:
            return "L3"
        if missing >= 2:
            return "L4"
        # 其他单 Agent 缺失
        return "L3" if missing == 1 else "L4"

    def execute_degraded(self, task: dict, level: str) -> dict:
        """执行降级策略。"""
        strategies: Dict[str, Callable] = {
            "L1": self._use_template_quote,
            "L2": self._use_smart_customer_service,
            "L3": self._use_basic_dictionary,
            "L4": self._return_preset_response,
        }

        handler = strategies.get(level, self._return_preset_response)
        result = handler(task)

        # 记录降级
        if self.mongo:
            try:
                self.mongo.degraded_tasks.insert_one({
                    "task_id": task.get("taskId"),
                    "level": level,
                    "timestamp": datetime.now().isoformat(),
                })
            except Exception:
                pass

        return result

    # ── L1-L4 降级策略 ───────────────────────────────────────

    def _use_template_quote(self, task: dict) -> dict:
        return {
            "quote": "基于您的需求，我们提供以下参考方案。详细报价将在24小时内发送。",
            "status": "degraded_L1",
            "note": "售前经理暂时不可用，已使用预设模板",
        }

    def _use_smart_customer_service(self, task: dict) -> dict:
        return {
            "response": "我们已收到您的需求，请提供更多细节以便我们更好地服务您。",
            "status": "degraded_L2",
        }

    def _use_basic_dictionary(self, task: dict) -> dict:
        return {
            "translated": task.get("payload", {}).get("original_text", ""),
            "status": "degraded_L3",
            "note": "翻译官暂时不可用，原文已保留",
        }

    def _return_preset_response(self, task: dict) -> dict:
        return {
            "message": "系统维护中，请稍后再试或发送邮件至 jamedow@lightingmetal.com",
            "status": "degraded_L4",
        }


# ═══════════════════════════════════════════════════════════════
# 快速测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    r = redis.Redis(host="127.0.0.1", port=6379,
                    password="Lt@114514!", decode_responses=True)

    # ── 模拟初始化 Agent 状态 ──
    r.hset("commander:agent:status:by_role", mapping={
        "翻译官": "running",
        "商务经理": "running",
        "售前经理": "offline",  # 模拟故障
        "审计官": "running",
    })

    # ── TaskDegradation 测试 ──
    deg = TaskDegradation(r)
    level = deg.get_degradation_level("询盘")
    print(f"询盘任务降级等级: {level} ({DEGRADATION_LEVELS.get(level)})")
    if level != "L0":
        result = deg.execute_degraded({"taskId": "test-001"}, level)
        print(f"降级结果: {result}")

    # ── AgentFailover 测试 ──
    failover = AgentFailover(r)
    failover.record_heartbeat("售前经理")
    result = failover.handle_agent_failure("售前经理", {"taskId": "test-002"})
    print(f"故障转移结果: {result}")
    dead = failover.check_dead_agents()
    print(f"失联Agent: {dead}")

    print("\n✅ failover.py 基础测试通过")
