"""Redis 连接管理 — 单例 + 自动重连"""
import redis, json, os, time
from typing import Optional, Any
from modules.shared.config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

class RedisClient:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    def __init__(self):
        if self._initialized: return
        self._initialized = True
        self._connect()
    def _connect(self):
        self.client = redis.Redis(protocol=2, host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True, socket_connect_timeout=5, socket_keepalive=True)
    def ping(self) -> bool:
        try: return self.client.ping()
        except: return False
    def get(self, key: str) -> Optional[str]:
        try: return self.client.get(key)
        except: return None
    def set(self, key: str, value: Any, ex: int = None):
        if not isinstance(value, str): value = json.dumps(value, ensure_ascii=False)
        self.client.set(key, value, ex=ex)
    def publish(self, channel: str, message: dict):
        self.client.publish(channel, json.dumps(message, ensure_ascii=False))
    def subscribe(self, *channels):
        pubsub = self.client.pubsub()
        pubsub.subscribe(*channels)
        return pubsub
    def smembers(self, key: str): return self.client.smembers(key)
    def hgetall(self, key: str): return self.client.hgetall(key)
    def keys(self, pattern: str = "*"): return self.client.keys(pattern)
