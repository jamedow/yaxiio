
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
SessionManager v3.0 — 会话与连接分离架构
=========================================
会话独立于连接生命周期运行。客户端断开后会话保持 active，任务继续执行，
结果暂存离线队列。重连后一次性吐出所有积压消息（seq去重）。

支持:
  - 多端接入（同一 token 在浏览器/手机/PC 互通）
  - 离线队列（最多1000条，超24h自动归档）
  - 历史同步（新端接入获得完整历史）
  - Lamport 逻辑时钟
  - HMAC 令牌签名 + 客户端指纹绑定

Redis 数据结构:
  session:active           Set    活跃会话 token 集合
  session:{token}:meta     Hash   会话元数据
  session:{token}:seq      String 全局递增 seq 计数器
  session:{token}:queue    List   离线消息队列 (LPUSH/RPOP)
  session:{token}:history  List   最近 N 条历史 (LPUSH/LTRIM)
  session:{token}:clients  Set    当前连接的客户端指纹集合

MongoDB 集合:
  sessions                doc    会话详情 + 冷归档
  session_history         doc    完整历史归档 (>500条)
  audit_logs              doc    审计日志
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    import pymongo
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False


# ═══════════════════════════════════════════════════════════════
# 配置（全部从环境变量注入）
# ═══════════════════════════════════════════════════════════════

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/")
MONGO_DB = os.environ.get("MONGO_DATABASE", "example_db")

SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "commander-v3-secret")
MAX_OFFLINE_QUEUE = int(os.environ.get("SESSION_MAX_OFFLINE_QUEUE", "1000"))
MAX_HISTORY_REDIS = int(os.environ.get("SESSION_MAX_HISTORY_REDIS", "500"))
OFFLINE_ARCHIVE_HOURS = int(os.environ.get("SESSION_OFFLINE_ARCHIVE_HOURS", "24"))
WS_PING_INTERVAL = int(os.environ.get("WS_PING_INTERVAL", "15"))
WS_PING_TIMEOUT = int(os.environ.get("WS_PING_TIMEOUT", "10"))


# ═══════════════════════════════════════════════════════════════
# SessionManager
# ═══════════════════════════════════════════════════════════════

class SessionManager:
    """会话与连接分离的管理器。

    使用:
        mgr = SessionManager()
        token = mgr.create_session(client_fingerprint="browser-chrome-xyz")
        mgr.connect(token, client_fingerprint="browser-chrome-xyz")
        mgr.enqueue_message(token, {"type": "task_result", "data": {...}})
        messages = mgr.get_offline_messages(token, last_seq=5)
    """

    def __init__(self):
        self._redis = self._init_redis()
        self._mongo = self._init_mongo()

    # ── Redis / Mongo 初始化 ──────────────────────────

    def _init_redis(self):
        if not HAS_REDIS:
            return None
        try:
            r = redis_lib.Redis(host=REDIS_HOST, port=REDIS_PORT,
                                password=REDIS_PASSWORD,
                                decode_responses=True,
                                socket_connect_timeout=5,
                                health_check_interval=30)
            r.ping()
            return r
        except Exception as e:
            print(f"[SessionManager] Redis 连接失败: {e}")
            return None

    def _init_mongo(self):
        if not HAS_MONGO:
            return None
        try:
            client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)
            client.server_info()
            db = client[MONGO_DB]
            db["sessions"].create_index("token", unique=True)
            db["session_history"].create_index([("token", 1), ("seq", 1)])
            db["audit_logs"].create_index([("session_token", 1), ("timestamp", -1)])
            return db
        except Exception as e:
            print(f"[SessionManager] MongoDB 连接失败: {e}")
            return None

    # ── 令牌生成与验证 ────────────────────────────────

    def generate_token(self, client_fingerprint: str = "") -> str:
        """生成 HMAC 签名的会话令牌。格式: sess-{random}-{signature}"""
        rand = uuid.uuid4().hex[:16]
        msg = f"{rand}:{client_fingerprint}"
        sig = hmac.new(
            SESSION_TOKEN_SECRET.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return f"sess-{rand}-{sig}"

    def validate_token(self, token: str, client_fingerprint: str = "") -> bool:
        """验证令牌签名和指纹绑定。"""
        if not token or not token.startswith("sess-"):
            return False
        parts = token.split("-")
        if len(parts) < 3:
            return False
        rand, sig = parts[1], parts[2]
        msg = f"{rand}:{client_fingerprint}"
        expected = hmac.new(
            SESSION_TOKEN_SECRET.encode(),
            msg.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return hmac.compare_digest(sig, expected)

    # ── 会话生命周期 ──────────────────────────────────

    def create_session(self, client_fingerprint: str = "",
                       metadata: dict = None) -> dict:
        """创建新会话，返回 token 和元数据。"""
        token = self.generate_token(client_fingerprint)
        now = time.time()

        meta = {
            "token": token,
            "created_at": now,
            "last_active": now,
            "fingerprint": client_fingerprint,
            "status": "active",
            "seq": 0,
            "lamport_clock": 0,
            "metadata": json.dumps(metadata or {}),
        }

        if self._redis:
            pipe = self._redis.pipeline()
            pipe.sadd("session:active", token)
            pipe.hset(f"session:{token}:meta", mapping=meta)
            pipe.set(f"session:{token}:seq", "0")
            pipe.expire(f"session:{token}:meta", 86400 * 7)
            pipe.execute()

        if self._mongo is not None:
            try:
                self._mongo["sessions"].update_one(
                    {"token": token},
                    {"$set": {**meta, "created_at": datetime.fromtimestamp(now)}},
                    upsert=True
                )
            except Exception:
                pass

        return {"token": token, "created_at": now, "status": "active"}

    def connect(self, token: str, client_fingerprint: str) -> dict:
        """客户端连接会话。验证令牌，注册指纹，返回最后 seq。"""
        if not self.validate_token(token, client_fingerprint):
            return {"error": "invalid_token"}

        if self._redis:
            self._redis.sadd(f"session:{token}:clients", client_fingerprint)
            self._redis.hset(f"session:{token}:meta", "last_active", str(time.time()))
            try:
                last_seq = int(self._redis.get(f"session:{token}:seq") or "0")
            except (ValueError, TypeError):
                last_seq = 0
            queue_size = self._redis.llen(f"session:{token}:queue")
            return {
                "status": "connected",
                "token": token,
                "last_seq": last_seq,
                "offline_queue_size": queue_size,
            }

        return {"error": "redis_unavailable"}

    def disconnect(self, token: str, client_fingerprint: str):
        """客户端断开连接。会话保持 active。"""
        if self._redis:
            self._redis.srem(f"session:{token}:clients", client_fingerprint)
            remaining = self._redis.scard(f"session:{token}:clients")
            if remaining == 0:
                self._redis.hset(f"session:{token}:meta", mapping={
                    "last_active": str(time.time()),
                    "status": "active"  # 保持 active，不关闭
                })
            return {"disconnected": True, "remaining_clients": remaining}
        return {"disconnected": True}

    def close_session(self, token: str) -> dict:
        """彻底关闭会话并归档。"""
        if self._redis:
            self._redis.srem("session:active", token)
            self._redis.delete(f"session:{token}:meta")
            self._redis.delete(f"session:{token}:seq")
            self._redis.delete(f"session:{token}:queue")
            self._redis.delete(f"session:{token}:history")
            self._redis.delete(f"session:{token}:clients")

        if self._mongo is not None:
            try:
                self._mongo["sessions"].update_one(
                    {"token": token},
                    {"$set": {"status": "closed", "closed_at": datetime.now()}}
                )
            except Exception:
                pass

        return {"status": "closed"}

    # ── 消息队列 ──────────────────────────────────────

    def _next_seq(self, token: str) -> int:
        """原子递增 seq 计数器。"""
        if self._redis:
            return self._redis.incr(f"session:{token}:seq")
        return 0

    def enqueue_message(self, token: str, message: dict,
                        depends_on: int = None) -> int:
        """将消息写入离线队列和历史。返回分配的 seq。"""
        seq = self._next_seq(token)
        msg = {
            "seq": seq,
            "timestamp": time.time(),
            "lamport_clock": self._tick_lamport(token),
            "depends_on": depends_on,
            "payload": message,
        }
        msg_json = json.dumps(msg, ensure_ascii=False, default=str)

        if self._redis:
            pipe = self._redis.pipeline()
            # 离线队列
            pipe.lpush(f"session:{token}:queue", msg_json)
            pipe.ltrim(f"session:{token}:queue", 0, MAX_OFFLINE_QUEUE - 1)
            # 历史（Redis 缓存最近N条）
            pipe.lpush(f"session:{token}:history", msg_json)
            pipe.ltrim(f"session:{token}:history", 0, MAX_HISTORY_REDIS - 1)
            pipe.execute()

        # MongoDB 完整归档
        if self._mongo is not None:
            try:
                self._mongo["session_history"].insert_one({
                    "token": token, "seq": seq, "message": message,
                    "lamport_clock": msg["lamport_clock"],
                    "timestamp": datetime.fromtimestamp(msg["timestamp"]),
                })
            except Exception:
                pass

        return seq

    def get_offline_messages(self, token: str,
                              last_seq: int = 0) -> List[dict]:
        """获取 seq > last_seq 的所有积压消息（seq 去重）。"""
        messages = []
        if not self._redis:
            return messages

        # 从离线队列获取所有消息，过滤 seq <= last_seq
        raw = self._redis.lrange(f"session:{token}:queue", 0, -1)
        seen = set()
        for r in reversed(raw):  # LPUSH 导致顺序反转，需要逆转
            try:
                msg = json.loads(r)
                if msg["seq"] > last_seq and msg["seq"] not in seen:
                    seen.add(msg["seq"])
                    messages.append(msg)
            except (json.JSONDecodeError, KeyError):
                continue

        # 按 Lamport 时钟排序
        messages.sort(key=lambda m: m.get("lamport_clock", 0))
        return messages

    def get_history(self, token: str, before_seq: int = None,
                     limit: int = 50) -> List[dict]:
        """分页获取历史消息。先 Redis，再 MongoDB。"""
        messages = []

        # 1. Redis 最近历史
        if self._redis:
            raw = self._redis.lrange(f"session:{token}:history", 0, -1)
            for r in reversed(raw):
                try:
                    msg = json.loads(r)
                    if before_seq is None or msg["seq"] < before_seq:
                        messages.append(msg)
                except (json.JSONDecodeError, KeyError):
                    continue

        # 2. 如果不够且需要更早的记录，查 MongoDB
        if len(messages) < limit and self._mongo:
            try:
                oldest_seq = messages[-1]["seq"] if messages else (before_seq or 0)
                cursor = self._mongo["session_history"].find(
                    {"token": token, "seq": {"$lt": oldest_seq}}
                ).sort("seq", -1).limit(limit - len(messages))
                for doc in cursor:
                    messages.append({
                        "seq": doc["seq"],
                        "timestamp": doc["timestamp"].timestamp(),
                        "message": doc["message"],
                    })
            except Exception:
                pass

        return messages[:limit]

    # ── Lamport 逻辑时钟 ──────────────────────────────

    def _tick_lamport(self, token: str) -> int:
        """递增并返回 Lamport 逻辑时钟。"""
        if self._redis:
            return self._redis.hincrby(f"session:{token}:meta", "lamport_clock", 1)
        return 0

    def update_lamport(self, token: str, received_clock: int) -> int:
        """更新 Lamport 时钟为 max(local, received)。"""
        if self._redis:
            current = int(self._redis.hget(f"session:{token}:meta", "lamport_clock") or "0")
            new_clock = max(current, received_clock) + 1
            self._redis.hset(f"session:{token}:meta", "lamport_clock", str(new_clock))
            return new_clock
        return 0

    # ── 会话管理查询 ──────────────────────────────────

    def get_session_meta(self, token: str) -> dict:
        """获取会话元数据。"""
        if self._redis:
            meta = self._redis.hgetall(f"session:{token}:meta")
            if meta:
                clients = self._redis.smembers(f"session:{token}:clients") or set()
                meta["connected_clients"] = list(clients)
                meta["is_client_connected"] = len(clients) > 0
                return meta
        return {"token": token, "status": "not_found"}

    def list_active_sessions(self) -> List[dict]:
        """列出所有活跃会话。"""
        sessions = []
        if self._redis:
            tokens = self._redis.smembers("session:active") or set()
            for t in tokens:
                sessions.append(self.get_session_meta(t))
        return sessions

    def check_expired_sessions(self) -> dict:
        """检查并归档过期会话（离线超 OFFLINE_ARCHIVE_HOURS 小时）。"""
        archived = 0
        notified = 0
        cutoff = time.time() - OFFLINE_ARCHIVE_HOURS * 3600

        if self._redis:
            tokens = self._redis.smembers("session:active") or set()
            for t in tokens:
                try:
                    last_active = float(
                        self._redis.hget(f"session:{token}:meta", "last_active") or "0"
                    )
                except (ValueError, TypeError):
                    last_active = 0

                if last_active < cutoff:
                    # 归档
                    queue_size = self._redis.llen(f"session:{token}:queue")
                    if self._mongo is not None and queue_size > 0:
                        try:
                            self._mongo["sessions"].update_one(
                                {"token": t},
                                {"$set": {
                                    "status": "archived",
                                    "archived_at": datetime.now(),
                                    "final_queue_size": queue_size,
                                }}
                            )
                        except Exception:
                            pass
                    self._redis.srem("session:active", t)
                    self._redis.hset(f"session:{token}:meta", "status", "archived")
                    archived += 1
                    if queue_size > 0:
                        notified += 1

        return {"archived": archived, "notified": notified}

    def get_queue_stats(self, token: str) -> dict:
        """获取离线队列统计。"""
        if self._redis:
            return {
                "offline_queue_size": self._redis.llen(f"session:{token}:queue") or 0,
                "history_count": self._redis.llen(f"session:{token}:history") or 0,
                "is_full": (self._redis.llen(f"session:{token}:queue") or 0) >= MAX_OFFLINE_QUEUE,
            }
        return {"offline_queue_size": 0}
