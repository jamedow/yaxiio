"""
RedisDataBus — L3 数据中转总线
================================
替代 WorkflowSnapshot 的文件系统存储。
使用 Redis Stream 在并行子任务之间传递数据。

特性:
  - 并发安全: Redis 原子操作
  - 自动 TTL: 任务完成后 1 小时自动过期
  - 流式消费: 支持按 sid 精确读取
  - 降级: Redis Stream 不可用时自动降级到 String key
"""
import json
import time
from typing import Dict, Optional


class RedisDataBus:
    """Redis Stream 数据中转总线"""

    STREAM_PREFIX = "yaxiio:data_bus"
    DEFAULT_TTL = 3600  # 1 小时

    def __init__(self, redis_client):
        """
        Args:
            redis_client: RedisClient 实例
        """
        self.redis = redis_client

    # ═══════════════════════════════════════════════
    # 写入
    # ═══════════════════════════════════════════════

    def put(self, task_id: str, sid: str, data: dict) -> str:
        """
        发布子任务结果到 Stream

        Args:
            task_id: 父任务 ID
            sid: 子任务 ID
            data: 结果字典

        Returns:
            Stream 消息 ID 或 "fallback_key"
        """
        stream_key = f"{self.STREAM_PREFIX}:{task_id}"
        payload = {
            "sid": sid,
            "data": json.dumps(data, ensure_ascii=False, default=str),
            "timestamp": str(time.time()),
        }
        try:
            msg_id = self.redis.client.xadd(
                stream_key, payload, maxlen=200, approximate=True
            )
            self.redis.client.expire(stream_key, self.DEFAULT_TTL)
            return msg_id
        except Exception:
            # 降级: String key
            fallback_key = f"{self.STREAM_PREFIX}:{task_id}:{sid}"
            self.redis.set(
                fallback_key,
                json.dumps(data, ensure_ascii=False, default=str),
                ex=self.DEFAULT_TTL,
            )
            return "fallback_key"

    # ═══════════════════════════════════════════════
    # 读取
    # ═══════════════════════════════════════════════

    def get(self, task_id: str, sid: str) -> Optional[dict]:
        """
        获取特定子任务的结果

        Args:
            task_id: 父任务 ID
            sid: 子任务 ID

        Returns:
            结果字典，或 None
        """
        # 尝试 Stream 读取
        try:
            stream_key = f"{self.STREAM_PREFIX}:{task_id}"
            results = self.redis.client.xread({stream_key: "0"}, count=200)
            if results:
                for _stream_name, messages in results:
                    for _msg_id, fields in messages:
                        msg_sid = fields.get(b"sid", b"").decode()
                        if msg_sid == sid:
                            data_raw = fields.get(b"data", b"{}").decode()
                            return json.loads(data_raw)
        except Exception:
            pass

        # 降级: String key
        try:
            raw = self.redis.get(f"{self.STREAM_PREFIX}:{task_id}:{sid}")
            if raw:
                return json.loads(raw)
        except Exception:
            pass

        return None

    def get_all(self, task_id: str) -> Dict[str, dict]:
        """
        获取任务的所有子任务结果

        Returns:
            {sid: result_dict, ...}
        """
        results: Dict[str, dict] = {}

        # Stream 读取
        try:
            stream_key = f"{self.STREAM_PREFIX}:{task_id}"
            raw = self.redis.client.xread({stream_key: "0"}, count=200)
            if raw:
                for _stream_name, messages in raw:
                    for _msg_id, fields in messages:
                        msg_sid = fields.get(b"sid", b"").decode()
                        data_raw = fields.get(b"data", b"{}").decode()
                        if msg_sid:
                            results[msg_sid] = json.loads(data_raw)
        except Exception:
            pass

        return results

    # ═══════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════

    def cleanup(self, task_id: str):
        """清理任务的所有数据"""
        try:
            stream_key = f"{self.STREAM_PREFIX}:{task_id}"
            self.redis.client.delete(stream_key)

            # 清理 String key 降级数据
            pattern = f"{self.STREAM_PREFIX}:{task_id}:*"
            keys = self.redis.keys(pattern)
            if keys:
                self.redis.client.delete(*keys)
        except Exception:
            pass

    def extend_ttl(self, task_id: str, ttl: int = None):
        """延长数据 TTL（用于长时间运行的任务）"""
        ttl = ttl or self.DEFAULT_TTL
        try:
            self.redis.client.expire(f"{self.STREAM_PREFIX}:{task_id}", ttl)
        except Exception:
            pass
