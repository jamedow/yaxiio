"""
Yaxiio Streams — Redis Streams 协议层
=====================================
替代 Pub/Sub 的点对点通信，提供:
- Consumer Group: L4 Agent 自动负载均衡
- ACK 机制: 消息确认 + 失败重分配
- 持久化: Commander 重启后未消费消息不丢失
- Pending 恢复: Agent 崩溃后任务自动转移

用法:
  bridge = StreamBridge()
  bridge.publish_task("L4", {"agent": "审计官", "task": "..."})
  result = bridge.await_result(task_id, timeout=120)
"""

import json, time, os, uuid
from typing import Optional


class StreamBridge:
    """Redis Streams 协议桥接 — 逐步替代 Pub/Sub"""

    def __init__(self, redis_host="127.0.0.1", redis_port=6379, redis_password=""):
        import redis as _r
        self.r = _r.Redis(
            host=redis_host, port=redis_port,
            password=redis_password,
            decode_responses=True,
            protocol=2,
            socket_connect_timeout=5,
        )
        self._groups_created = set()

    # ═══════════════════════════════════════════
    # Stream 管理
    # ═══════════════════════════════════════════

    def ensure_group(self, stream: str, group: str):
        """确保 Consumer Group 存在 (幂等)"""
        if (stream, group) in self._groups_created:
            return
        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists
        self._groups_created.add((stream, group))

    # ═══════════════════════════════════════════
    # 任务发布
    # ═══════════════════════════════════════════

    def publish_task(self, layer: str, task: dict, task_id: str = "") -> str:
        """发布任务到指定层的 Stream"""
        if not task_id:
            task_id = task.get("taskId", uuid.uuid4().hex[:12])
        stream = f"yaxiio:stream:{layer}"
        msg_id = self.r.xadd(stream, {
            "task_id": task_id,
            "payload": json.dumps(task, ensure_ascii=False),
            "timestamp": str(time.time()),
        }, maxlen=1000)
        return task_id

    # ═══════════════════════════════════════════
    # Agent 消费 (L4)
    # ═══════════════════════════════════════════

    def consume_task(self, agent_name: str, layer: str = "L4",
                     block_ms: int = 5000, count: int = 1) -> list:
        """Agent 从 Stream 消费任务 (Consumer Group 自动负载均衡)"""
        stream = f"yaxiio:stream:{layer}"
        group = f"agents-{layer}"
        self.ensure_group(stream, group)

        try:
            results = self.r.xreadgroup(
                groupname=group,
                consumername=agent_name,
                streams={stream: ">"},
                block=block_ms,
                count=count,
            )
        except Exception:
            return []

        tasks = []
        if results:
            for stream_name, messages in results:
                for msg_id, fields in messages:
                    task = json.loads(fields.get("payload", "{}"))
                    task["_stream_id"] = msg_id
                    task["_stream"] = stream
                    task["_group"] = group
                    tasks.append(task)
        return tasks

    def ack_task(self, agent_name: str, task: dict) -> bool:
        """确认任务完成"""
        stream = task.get("_stream", "")
        group = task.get("_group", "")
        msg_id = task.get("_stream_id", "")
        if stream and group and msg_id:
            self.r.xack(stream, group, msg_id)
            return True
        return False

    # ═══════════════════════════════════════════
    # 故障恢复
    # ═══════════════════════════════════════════

    def recover_pending(self, agent_name: str, layer: str = "L4",
                        min_idle_ms: int = 60000) -> list:
        """恢复超时的 pending 消息 (Agent 崩溃后其他 Agent 接管)"""
        stream = f"yaxiio:stream:{layer}"
        group = f"agents-{layer}"
        self.ensure_group(stream, group)

        try:
            # Claim messages idle > min_idle_ms
            pending = self.r.xpending_range(
                name=stream, groupname=group, min="-", max="+", count=10
            )
            recovered = []
            for entry in pending:
                msg_id = entry["message_id"]
                idle_ms = entry.get("time_since_delivered", 0)
                if idle_ms > min_idle_ms:
                    claimed = self.r.xclaim(
                        name=stream, groupname=group,
                        consumername=agent_name, min_idle_time=min_idle_ms,
                        message_ids=[msg_id],
                    )
                    if claimed:
                        recovered.extend(claimed)
            return recovered
        except Exception:
            return []

    # ═══════════════════════════════════════════
    # 结果等待 (替代 _wait_for_neuron_response)
    # ═══════════════════════════════════════════

    def await_result(self, task_id: str, timeout: int = 120) -> Optional[dict]:
        """等待任务结果 (轮询 Redis key, 替代 Pub/Sub 超时轮询)"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.r.get(f"yaxiio:task:{task_id}")
            if raw:
                try:
                    result = json.loads(raw)
                    if result.get("status") in ("DONE", "FAILED", "success"):
                        return result
                except json.JSONDecodeError:
                    pass
            time.sleep(0.5)
        return None

    def stats(self, layer: str = "L4") -> dict:
        """Stream 统计信息"""
        stream = f"yaxiio:stream:{layer}"
        try:
            info = self.r.xinfo_stream(stream)
            groups = self.r.xinfo_groups(stream)
            return {
                "length": info.get("length", 0),
                "groups": len(groups),
                "last_entry": info.get("last-entry", ""),
            }
        except Exception:
            return {"length": 0, "groups": 0}
