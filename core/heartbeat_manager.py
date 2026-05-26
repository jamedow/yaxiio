#!/usr/bin/env python3
"""
AgentHeartbeatManager — 本地 Agent 心跳调度系统
=================================================
Commander 被动接收本地 Agent 的心跳，维护在线列表，需要时反向连接调度。

协议:
  客户端 → 服务器: HTTP POST {agent_id, ip, port, capabilities}
  服务器 → 客户端: 反向连接 (HTTP POST) 分发任务
  无心跳超时: 60s → delayed, 180s → suspended

Constitution:
  R1 — 使用 `commander:heartbeat:*` 前缀
  R2 — 单个 Commander 最多管理 20 个心跳 Agent
"""

import json
import os
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    import requests as http_requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

HEARTBEAT_TIMEOUT_S = 60         # 心跳超时 → delayed
HEARTBEAT_SUSPEND_S = 180        # 连续无心跳 → suspended
HEARTBEAT_TTL_S = 240            # Redis key TTL
DEFAULT_MAX_HEARTBEAT_AGENTS = 20


# ═══════════════════════════════════════════════════════════════
# 健康状态枚举
# ═══════════════════════════════════════════════════════════════

class HeartbeatStatus:
    ONLINE = "online"
    DELAYED = "delayed"
    SUSPENDED = "suspended"
    OFFLINE = "offline"


# ═══════════════════════════════════════════════════════════════
# 心跳记录
# ═══════════════════════════════════════════════════════════════

class HeartbeatRecord:
    """单个 Agent 的心跳记录。"""

    def __init__(self,
                 agent_id: str,
                 ip: str,
                 port: int,
                 capabilities: List[str] = None,
                 metadata: Dict[str, Any] = None,
                 last_heartbeat: Optional[float] = None,
                 status: str = HeartbeatStatus.ONLINE,
                 registered_at: Optional[float] = None):
        self.agent_id = agent_id
        self.ip = ip
        self.port = port
        self.capabilities = capabilities or []
        self.metadata = metadata or {}
        self.last_heartbeat = last_heartbeat or time.time()
        self.status = status
        self.registered_at = registered_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "ip": self.ip,
            "port": self.port,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
            "last_heartbeat": self.last_heartbeat,
            "last_heartbeat_human": datetime.fromtimestamp(
                self.last_heartbeat).isoformat(),
            "status": self.status,
            "registered_at": self.registered_at,
            "registered_at_human": datetime.fromtimestamp(
                self.registered_at).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HeartbeatRecord":
        return cls(
            agent_id=data["agent_id"],
            ip=data["ip"],
            port=data["port"],
            capabilities=data.get("capabilities", []),
            metadata=data.get("metadata", {}),
            last_heartbeat=data.get("last_heartbeat"),
            status=data.get("status", HeartbeatStatus.ONLINE),
            registered_at=data.get("registered_at"),
        )

    @classmethod
    def from_heartbeat_packet(cls, packet: Dict[str, Any]) -> "HeartbeatRecord":
        """从客户端心跳包创建记录。"""
        return cls(
            agent_id=packet["agent_id"],
            ip=packet.get("ip", packet.get("host", "0.0.0.0")),
            port=packet.get("port", 0),
            capabilities=packet.get("capabilities", []),
            metadata=packet.get("metadata", {}),
        )


# ═══════════════════════════════════════════════════════════════
# AgentHeartbeatManager 核心
# ═══════════════════════════════════════════════════════════════

class AgentHeartbeatManager:
    """管理本地 Agent 心跳与调度。

    工作流程:
      1. 客户端启动 → POST /heartbeat 上报信息
      2. Commander 接收 → 存入 Redis + 设置 TTL
      3. 每 60s 无心跳 → 标记 delayed
      4. 每 180s 无心跳 → 标记 suspended
      5. 心跳恢复 → online (自动解除 suspended)
      6. 需要调度 → 从 Redis 获取 IP:Port → 反向连接
      7. 连接失败 → 不重试，等待下一次心跳

    Redis 数据结构:
      commander:heartbeat:agents           ← Hash: agent_id → record JSON
      commander:heartbeat:timestamps        ← Hash: agent_id → last_heartbeat_ts
      commander:heartbeat:online            ← Set: 在线 agent_id
    """

    def __init__(self,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 max_agents: int = DEFAULT_MAX_HEARTBEAT_AGENTS,
                 timeout_s: int = HEARTBEAT_TIMEOUT_S,
                 suspend_s: int = HEARTBEAT_SUSPEND_S):
        self.max_agents = max_agents
        self.timeout_s = timeout_s
        self.suspend_s = suspend_s

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

        # 回调: 当 Agent 状态变化时调用
        self._status_callbacks: Dict[str, List[Callable]] = {
            "online": [],
            "delayed": [],
            "suspended": [],
            "offline": [],
        }

    # ═══════════════════════════════════════════════════════════
    # 心跳接收
    # ═══════════════════════════════════════════════════════════

    def receive_heartbeat(self, packet: Dict[str, Any]) -> Dict[str, Any]:
        """接收客户端心跳包。

        packet = {
            "agent_id": "lobster-001",       # 必填: Agent 唯一标识
            "ip": "192.168.1.100",           # 客户端 IP（用于反向连接）
            "port": 8899,                    # 客户端监听端口
            "capabilities": ["translate", "audit"],  # 能力列表
            "metadata": {"hostname": "dev-machine", "version": "1.0"}
        }

        返回:
            {"status": "accepted", ...} 或 {"status": "rejected", ...}
        """
        agent_id = packet.get("agent_id")
        if not agent_id:
            return {"status": "rejected", "reason": "缺少 agent_id"}

        # 检查容量
        online_count = self._count_online()
        if online_count >= self.max_agents and not self._is_registered(agent_id):
            return {"status": "rejected", "reason": f"心跳Agent已达上限 ({self.max_agents})"}

        # 创建/更新心跳记录
        record = HeartbeatRecord.from_heartbeat_packet(packet)

        # 上一次状态（用于回调）
        prev_status = self._get_status(agent_id)

        # 写入 Redis
        self._save_record(record)

        # 检查状态变化并触发回调
        if prev_status != record.status:
            self._fire_status_change(agent_id, prev_status, record.status)

        # 返回确认 + Commander 连接信息
        return {
            "status": "accepted",
            "agent_id": agent_id,
            "server_time": time.time(),
            "next_heartbeat_in_s": self.timeout_s // 2,
            "commander_online_agents": online_count,
        }

    def receive_heartbeat_batch(self, packets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """批量接收心跳包。"""
        return [self.receive_heartbeat(p) for p in packets]

    # ═══════════════════════════════════════════════════════════
    # 健康检查
    # ═══════════════════════════════════════════════════════════

    def check_all_health(self) -> Dict[str, Any]:
        """检查所有心跳 Agent 的健康状态。"""
        agents = self._get_all_records()
        now = time.time()

        results = {
            "total": len(agents),
            "online": 0,
            "delayed": 0,
            "suspended": 0,
            "offline": 0,
            "changes": [],
        }

        for agent in agents:
            elapsed = now - agent.last_heartbeat
            prev_status = agent.status

            if elapsed > self.suspend_s:
                agent.status = HeartbeatStatus.SUSPENDED
            elif elapsed > self.timeout_s:
                agent.status = HeartbeatStatus.DELAYED
            else:
                # 从 delayed/suspended 恢复
                if agent.status in (HeartbeatStatus.DELAYED,
                                     HeartbeatStatus.SUSPENDED):
                    agent.status = HeartbeatStatus.ONLINE
                elif agent.status != HeartbeatStatus.ONLINE:
                    agent.status = HeartbeatStatus.OFFLINE

            # 保存
            if agent.status != prev_status:
                results["changes"].append({
                    "agent_id": agent.agent_id,
                    "from": prev_status,
                    "to": agent.status,
                    "elapsed_s": elapsed,
                })
                self._fire_status_change(agent.agent_id, prev_status, agent.status)

            self._save_record(agent)
            results[agent.status] += 1

        return results

    def get_agent_health(self, agent_id: str) -> Dict[str, Any]:
        """获取单个 Agent 的健康信息。"""
        record = self._get_record(agent_id)
        if not record:
            return {"agent_id": agent_id, "status": "not_found"}

        elapsed = time.time() - record.last_heartbeat
        return {
            "agent_id": agent_id,
            "status": record.status,
            "ip": record.ip,
            "port": record.port,
            "capabilities": record.capabilities,
            "last_heartbeat_s": elapsed,
            "last_heartbeat_human": datetime.fromtimestamp(
                record.last_heartbeat).isoformat(),
            "suspended": record.status == HeartbeatStatus.SUSPENDED,
        }

    # ═══════════════════════════════════════════════════════════
    # 调度: 根据能力查找 + 反向连接
    # ═══════════════════════════════════════════════════════════

    def find_agent_by_capability(self,
                                  capability: str,
                                  only_online: bool = True) -> Optional[HeartbeatRecord]:
        """根据能力查找最优 Agent。

        决策: 在线 > 延迟 > 最近心跳时间
        """
        agents = self._get_all_records()

        # 筛选拥有目标能力的 Agent
        candidates = []
        for agent in agents:
            if capability in agent.capabilities or any(
                    cap.startswith(capability) or capability.startswith(cap)
                    for cap in agent.capabilities):
                candidates.append(agent)

        if not candidates:
            return None

        if only_online:
            # 优先在线
            online = [a for a in candidates
                      if a.status == HeartbeatStatus.ONLINE]
            if online:
                candidates = online
            else:
                # 退回到 delayed
                delayed = [a for a in candidates
                           if a.status == HeartbeatStatus.DELAYED]
                if delayed:
                    candidates = delayed

        # 按最近心跳排序
        candidates.sort(key=lambda a: a.last_heartbeat, reverse=True)
        return candidates[0] if candidates else None

    def find_all_by_capabilities(self,
                                  capabilities: List[str],
                                  only_online: bool = True) -> List[HeartbeatRecord]:
        """查找所有匹配任一能力的 Agent（去重）。"""
        found = {}
        for cap in capabilities:
            agent = self.find_agent_by_capability(cap, only_online)
            if agent and agent.agent_id not in found:
                found[agent.agent_id] = agent
        return list(found.values())

    def dispatch_task(self,
                       agent_id: str,
                       task: Dict[str, Any],
                       timeout_s: int = 30) -> Dict[str, Any]:
        """反向连接到本地 Agent 并分发任务。

        连接失败 → 不重试，返回失败状态。
        Agent 会在 30s 后自动发心跳，Commander 届时可重试。
        """
        if not HAS_REQUESTS:
            return {"status": "failed", "reason": "requests 库未安装"}

        record = self._get_record(agent_id)
        if not record:
            return {"status": "failed", "reason": "Agent 未注册"}

        url = f"http://{record.ip}:{record.port}/task"

        try:
            resp = http_requests.post(
                url,
                json={
                    "from": "commander",
                    "type": "task",
                    "task": task,
                    "timestamp": time.time(),
                },
                timeout=timeout_s,
            )
            if resp.status_code == 200:
                return {
                    "status": "dispatched",
                    "agent_id": agent_id,
                    "ip": record.ip,
                    "port": record.port,
                    "response": resp.json() if resp.text else {},
                }
            else:
                return {
                    "status": "failed",
                    "agent_id": agent_id,
                    "http_status": resp.status_code,
                    "response": resp.text[:500],
                }
        except Exception as e:
            return {
                "status": "failed",
                "agent_id": agent_id,
                "reason": str(e),
                "note": "不重试，等待下一次心跳（Agent 会在 30s 后自动更新 IP）",
            }

    # ═══════════════════════════════════════════════════════════
    # Agent 管理
    # ═══════════════════════════════════════════════════════════

    def list_agents(self,
                     status_filter: str = None) -> List[Dict[str, Any]]:
        """列出所有心跳 Agent。"""
        agents = self._get_all_records()
        if status_filter:
            agents = [a for a in agents if a.status == status_filter]
        return [a.to_dict() for a in agents]

    def deregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """手动注销 Agent（客户端主动退出时调用）。"""
        if self._redis:
            try:
                self._redis.hdel("commander:heartbeat:agents", agent_id)
                self._redis.hdel("commander:heartbeat:timestamps", agent_id)
                self._redis.srem("commander:heartbeat:online", agent_id)
            except Exception:
                pass
        return {"agent_id": agent_id, "status": "deregistered"}

    # ═══════════════════════════════════════════════════════════
    # 回调系统
    # ═══════════════════════════════════════════════════════════

    def on_status_change(self, new_status: str, callback: Callable):
        """注册状态变化回调。

        new_status: "online" | "delayed" | "suspended" | "offline"
        callback(agent_id: str, from_status: str, to_status: str)
        """
        if new_status in self._status_callbacks:
            self._status_callbacks[new_status].append(callback)

    def _fire_status_change(self, agent_id: str, from_status: str,
                             to_status: str):
        """触发状态变化回调。"""
        print(f"[HeartbeatManager] 🔔 {agent_id}: {from_status} → {to_status}")
        for callback in self._status_callbacks.get(to_status, []):
            try:
                callback(agent_id, from_status, to_status)
            except Exception as e:
                print(f"[HeartbeatManager] ⚠️ 回调异常: {e}")

    # ═══════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════

    def _save_record(self, record: HeartbeatRecord):
        """将心跳记录保存到 Redis。"""
        if not self._redis:
            return
        try:
            pipe = self._redis.pipeline()
            # 记录数据
            pipe.hset("commander:heartbeat:agents",
                      record.agent_id,
                      json.dumps(record.to_dict(), ensure_ascii=False))
            # 时间戳
            pipe.hset("commander:heartbeat:timestamps",
                      record.agent_id, str(record.last_heartbeat))
            # TTL
            pipe.expire("commander:heartbeat:agents", HEARTBEAT_TTL_S)
            pipe.expire("commander:heartbeat:timestamps", HEARTBEAT_TTL_S)
            # 在线状态集合
            if record.status == HeartbeatStatus.ONLINE:
                pipe.sadd("commander:heartbeat:online", record.agent_id)
            else:
                pipe.srem("commander:heartbeat:online", record.agent_id)
            pipe.execute()
        except Exception as e:
            print(f"[HeartbeatManager] ⚠️ Redis 保存失败: {e}")

    def _get_record(self, agent_id: str) -> Optional[HeartbeatRecord]:
        """从 Redis 获取单个心跳记录。"""
        if not self._redis:
            return None
        try:
            raw = self._redis.hget("commander:heartbeat:agents", agent_id)
            if raw:
                return HeartbeatRecord.from_dict(json.loads(raw))
        except Exception:
            pass
        return None

    def _get_all_records(self) -> List[HeartbeatRecord]:
        """从 Redis 获取所有心跳记录。"""
        if not self._redis:
            return []
        try:
            raw_all = self._redis.hgetall("commander:heartbeat:agents")
            return [HeartbeatRecord.from_dict(json.loads(v))
                    for v in raw_all.values()]
        except Exception:
            return []

    def _get_status(self, agent_id: str) -> str:
        """获取 Agent 当前状态。"""
        record = self._get_record(agent_id)
        return record.status if record else HeartbeatStatus.OFFLINE

    def _is_registered(self, agent_id: str) -> bool:
        """检查 Agent 是否已注册。"""
        if not self._redis:
            return False
        try:
            return self._redis.hexists("commander:heartbeat:agents", agent_id)
        except Exception:
            return False

    def _count_online(self) -> int:
        """统计在线 Agent 数。"""
        agents = self._get_all_records()
        return sum(1 for a in agents if a.status == HeartbeatStatus.ONLINE)


# ═══════════════════════════════════════════════════════════════
# 轻量 HTTP 服务（可选，用于接收心跳）
# ═══════════════════════════════════════════════════════════════

def create_heartbeat_app(heartbeat_manager: AgentHeartbeatManager):
    """创建 Flask 心跳接收应用。

    路由:
      POST /heartbeat  — 接收心跳包
      GET  /status     — 查看所有心跳 Agent 状态
      POST /deregister — 注销 Agent
    """
    try:
        from flask import Flask, request, jsonify
    except ImportError:
        print("[HeartbeatManager] ⚠️ Flask 未安装，无法创建 HTTP 服务")
        return None

    app = Flask("commander_heartbeat")

    @app.route("/heartbeat", methods=["POST"])
    def receive_heartbeat():
        packet = request.get_json(force=True, silent=True)
        if not packet:
            return jsonify({"status": "rejected", "reason": "无效的 JSON"}), 400
        result = heartbeat_manager.receive_heartbeat(packet)
        status_code = 200 if result["status"] == "accepted" else 429
        return jsonify(result), status_code

    @app.route("/heartbeat/status", methods=["GET"])
    def get_status():
        return jsonify({
            "agents": heartbeat_manager.list_agents(),
            "health": heartbeat_manager.check_all_health(),
        })

    @app.route("/heartbeat/online", methods=["GET"])
    def list_online():
        return jsonify(heartbeat_manager.list_agents(
            status_filter=HeartbeatStatus.ONLINE))

    @app.route("/heartbeat/capability", methods=["GET"])
    def find_by_capability():
        cap = request.args.get("q", "")
        if not cap:
            return jsonify({"error": "缺少 q 参数"}), 400
        agent = heartbeat_manager.find_agent_by_capability(cap)
        if agent:
            return jsonify(agent.to_dict())
        return jsonify({"status": "not_found"}), 404

    @app.route("/heartbeat/deregister", methods=["POST"])
    def deregister():
        packet = request.get_json(force=True, silent=True)
        if not packet or "agent_id" not in packet:
            return jsonify({"error": "缺少 agent_id"}), 400
        result = heartbeat_manager.deregister_agent(packet["agent_id"])
        return jsonify(result)

    @app.route("/heartbeat/dispatch", methods=["POST"])
    def dispatch():
        data = request.get_json(force=True, silent=True)
        if not data or "agent_id" not in data or "task" not in data:
            return jsonify({"error": "缺少 agent_id 或 task"}), 400
        result = heartbeat_manager.dispatch_task(
            data["agent_id"], data["task"])
        return jsonify(result)

    return app


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent Heartbeat Manager")
    parser.add_argument("action", nargs="?", default="status",
                        choices=["status", "health", "serve"])
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-password", default="")
    parser.add_argument("--port", type=int, default=3399,
                        help="HTTP 服务端口")
    args = parser.parse_args()

    mgr = AgentHeartbeatManager(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
    )

    if args.action == "status":
        result = mgr.list_agents()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "health":
        result = mgr.check_all_health()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.action == "serve":
        app = create_heartbeat_app(mgr)
        if app:
            print(f"[HeartbeatManager] 🫀 心跳服务启动在 0.0.0.0:{args.port}")
            from waitress import serve
            serve(app, host="0.0.0.0", port=args.port)
        else:
            print("错误: Flask 未安装")
            exit(1)
