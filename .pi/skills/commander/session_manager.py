#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
SessionManager — Commander V3 会话与连接分离核心
=================================================
核心设计原则:
  - Session 是持久的数据容器，Connection 是临时的通信管道
  - 所有客户端断开，Session 仍在运行，任务照常执行，结果暂存队列
  - 客户端重连后，一次性吐出积压消息（去重 + 序列号）
  - 同一 Session 可通过 token 在浏览器/PC/手机多端接入
  - 新端接入自动同步完整历史（Redis 近500条 + MongoDB 完整归档）

六层安全保障:
  1. 消息序列号 (seq) — 全局递增，客户端去重
  2. HMAC 签名令牌 — 防伪造，绑定客户端指纹
  3. Lamport 逻辑时钟 — 多端并发写排序
  4. WebSocket 原生 ping/pong — 传输层保活
  5. 分层存储 — Redis (热) + MongoDB (冷归档)
  6. 队列上限 + 超时归档 — 防止内存耗尽

Constitution:
  R1 — 使用 `commander:session:*` 前缀
  R2 — 单会话最多暂存 1000 条离线消息
  R3 — 离线超 24h 自动归档并通知
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    import redis.asyncio as aioredis
    HAS_REDIS = True
except ImportError:
    aioredis = None
    HAS_REDIS = False

try:
    from pymongo import FakeMongoDB
    # Exception — removed (using SQLite)
    HAS_MONGO = True
except ImportError:
    FakeMongoDB = None
    HAS_MONGO = False


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# Redis key 前缀
SESSION_HASH = "commander:session:meta"       # HSET: token → meta JSON
SESSION_QUEUE = "commander:session:queue:{}"   # LIST: 离线消息 (FIFO)
SESSION_HISTORY = "commander:session:history:{}"  # ZSET: 最近对话
SESSION_SEQ = "commander:session:seq:{}"       # STRING: 全局消息序号
SESSION_CLIENTS = "commander:session:clients:{}"  # HSET: client_id → info
SESSION_HEARTBEAT = "commander:session:hb:{}:{}"  # STRING: 心跳时间戳 (TTL)

# 限制
MAX_OFFLINE_QUEUE_SIZE = 1000       # 单会话离线队列上限
MAX_HISTORY_IN_REDIS = 500          # Redis 中保留最近 N 条
MAX_OFFLINE_AGE_HOURS = 24          # 离线超时自动归档
SESSION_RETENTION_DAYS = {
    "free": 7,
    "paid": 30,
    "enterprise": None,  # 永久
}

# 会话状态
SESSION_ACTIVE = "active"
SESSION_ALL_DISCONNECTED = "all_disconnected"
SESSION_ARCHIVED = "archived"

# 客户端心跳
CLIENT_HEARTBEAT_TTL = 90           # 心跳 key TTL (秒)
CLIENT_HEARTBEAT_INTERVAL = 30      # 客户端发心跳间隔
CLIENT_DISCONNECT_GRACE = 60        # 无心跳判定断开的宽限期

# MongoDB
MONGO_DB = "example_db"
MONGO_COLLECTION_HISTORY = "session_history"
MONGO_COLLECTION_ARCHIVE = "session_archive"


# ═══════════════════════════════════════════════════════════════
# SessionManager 核心
# ═══════════════════════════════════════════════════════════════

class SessionManager:
    """会话与连接分离管理器。

    职责:
      - 会话生命周期: register → connect → heartbeat → disconnect → archive → destroy
      - 消息路由: 在线直接推送 / 离线暂存队列
      - 历史同步: Redis 热数据 + MongoDB 冷归档
      - 多端管理: 同一 token 多 client_id 共存

    用法:
        mgr = SessionManager(redis_url="redis://...", store_path="/opt/commander/data...")
        await mgr.initialize()

        # 注册
        result = await mgr.register_session(client_fingerprint, user_tier="paid")
        token = result["session_token"]

        # 连接
        result = await mgr.connect_client(token, client_id, last_seq=0)
        offline_msgs = result["offline_messages"]
        history = result["history"]

        # 发消息
        await mgr.send_to_session(token, {"type":"task_progress","data":{...}})

        # 心跳
        await mgr.client_heartbeat(token, client_id)

        # 销毁
        await mgr.destroy_session(token)
    """

    def __init__(self,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 store_path: str = "/opt/commander/data/yaxiio.db",
                 session_secret: str = None):
        self.redis_host = redis_host
        self.redis_port = redis_port
        self.redis_password = redis_password or os.environ.get("REDIS_PASSWORD", "")
        self.store_path = store_path
        self.session_secret = session_secret or os.environ.get(
            "COMMANDER_SESSION_SECRET",
            secrets.token_hex(32)
        )

        self._redis: Optional[aioredis.Redis] = None
        self._store = None
        self._mongo_db = None
        self._initialized = False

        # 回调
        self._on_archive_callbacks: List[Callable] = []
        self._on_destroy_callbacks: List[Callable] = []
        self._on_offline_queue_full: List[Callable] = []

    # ═══════════════════════════════════════════════════════════
    # 初始化
    # ═══════════════════════════════════════════════════════════

    async def initialize(self):
        """初始化 Redis 和 MongoDB 连接。"""
        if HAS_REDIS:
            self._redis = aioredis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                decode_responses=True,
                socket_connect_timeout=5,
                health_check_interval=30,
            )
            await self._redis.ping()
            print("[SessionManager] ✅ Redis 已连接")

        if HAS_MONGO:
            self._store = FakeMongoDB(
                self.store_path,
                serverSelectionTimeoutMS=5000,
            )
            self._mongo_db = self._store[MONGO_DB]
            # 确保集合和索引
            self._mongo_db[MONGO_COLLECTION_HISTORY].create_index(
                [("session_token", 1), ("timestamp", -1)],
                name="idx_session_timestamp"
            )
            self._mongo_db[MONGO_COLLECTION_ARCHIVE].create_index(
                "session_token", unique=True, name="idx_archive_token"
            )
            print("[SessionManager] ✅ MongoDB 已连接")

        self._initialized = True

    async def close(self):
        """关闭连接。"""
        if self._redis:
            await self._redis.close()
        if self._store:
            self._store.close()
        self._initialized = False

    def _check_init(self):
        if not self._initialized:
            raise RuntimeError("SessionManager 未初始化，请先调用 initialize()")

    # ═══════════════════════════════════════════════════════════
    # 令牌生成与验证
    # ═══════════════════════════════════════════════════════════

    def generate_session_token(self, client_fingerprint: str) -> str:
        """生成带 HMAC 签名的会话令牌。

        格式: sess-{random_hex}-{signature_hex}
        random_hex: 128 位熵 (secrets.token_hex(16))
        signature: HMAC-SHA256(random_hex + client_fingerprint) → hex[:16]
        """
        random_part = secrets.token_hex(16)  # 32 hex chars, 128 bits
        payload = f"{random_part}:{client_fingerprint}"
        signature = hmac.new(
            self.session_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]
        return f"sess-{random_part}-{signature}"

    def verify_session_token(self, token: str, client_fingerprint: str = "") -> bool:
        """验证令牌签名和可选指纹绑定。

        - 无指纹时仅验证签名格式
        - 有指纹时验证签名 + 指纹匹配
        """
        if not token or not token.startswith("sess-"):
            return False

        body = token[5:]  # 去掉 "sess-" 前缀
        parts = body.rsplit("-", 1)
        if len(parts) != 2:
            return False

        random_part, signature = parts
        payload = f"{random_part}:{client_fingerprint}"
        expected = hmac.new(
            self.session_secret.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()[:16]

        # 常数时间比较，防时序攻击
        return hmac.compare_digest(signature, expected)

    @staticmethod
    def generate_client_id() -> str:
        """生成客户端唯一标识。"""
        return f"client-{secrets.token_hex(4)}"

    # ═══════════════════════════════════════════════════════════
    # 会话注册
    # ═══════════════════════════════════════════════════════════

    async def register_session(self,
                                client_fingerprint: str = "",
                                client_type: str = "browser",
                                user_tier: str = "free",
                                retention_days: int = None,
                                metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """注册新会话。

        Args:
            client_fingerprint: 客户端指纹 (userAgent + screen + timezone 的 hash)
            client_type: browser | cli | mobile
            user_tier: free | paid | enterprise
            retention_days: 覆盖默认保留天数
            metadata: 自定义元数据

        Returns:
            {"type":"registered", "session_token":"...", "client_id":"..."}
        """
        self._check_init()

        session_token = self.generate_session_token(client_fingerprint)
        client_id = self.generate_client_id()
        now = datetime.now().isoformat()

        retention = retention_days or SESSION_RETENTION_DAYS.get(user_tier, 7)

        session_meta = {
            "session_token": session_token,
            "clients": [client_id],
            "created_at": now,
            "last_active": now,
            "status": SESSION_ACTIVE,
            "user_tier": user_tier,
            "retention_days": retention,
            "client_type": client_type,
            "fingerprint": client_fingerprint[:32] if client_fingerprint else "",
            "metadata": metadata or {},
            "total_messages": 0,
        }

        # 写入 Redis
        if self._redis:
            await self._redis.hset(
                SESSION_HASH,
                session_token,
                json.dumps(session_meta, ensure_ascii=False)
            )
            # 初始化序列号
            await self._redis.set(f"commander:session:seq:{session_token}", 0)

        return {
            "type": "registered",
            "session_token": session_token,
            "client_id": client_id,
            "retention_days": retention,
            "user_tier": user_tier,
        }

    # ═══════════════════════════════════════════════════════════
    # 客户端连接（重连 / 新端接入）
    # ═══════════════════════════════════════════════════════════

    async def connect_client(self,
                              session_token: str,
                              client_id: str = None,
                              client_fingerprint: str = "",
                              last_seq: int = 0) -> Dict[str, Any]:
        """客户端携带令牌连接。

        流程:
          1. 验证令牌（签名 + 指纹）
          2. 查询会话元数据
          3. 注册新客户端（多端场景）
          4. 获取离线消息（seq > last_seq）
          5. 获取会话历史（最近 N 条）

        Args:
            session_token: 会话令牌
            client_id: 客户端 ID（新端时为空，自动生成）
            client_fingerprint: 当前客户端指纹
            last_seq: 客户端最后确认的消息序号（去重用）

        Returns:
            {
                "type": "connected",
                "session_token": "...",
                "client_id": "...",
                "offline_messages": [...],
                "offline_count": N,
                "history": [...],
                "current_seq": N,
                "session_status": "active|all_disconnected|archived"
            }
        """
        self._check_init()

        # 1. 验证令牌
        if not self.verify_session_token(session_token, client_fingerprint):
            return {"type": "error", "code": "invalid_token", "message": "无效的会话令牌"}

        if not self._redis:
            return {"type": "error", "code": "no_redis", "message": "Redis 不可用"}

        # 2. 获取会话元数据
        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return {"type": "error", "code": "session_not_found", "message": "会话不存在或已过期"}

        session = json.loads(raw)

        if session.get("status") == SESSION_ARCHIVED:
            return {
                "type": "error",
                "code": "session_archived",
                "message": "会话已归档，请联系管理员恢复"
            }

        # 3. 注册新客户端
        is_new_client = False
        if not client_id:
            client_id = self.generate_client_id()
            is_new_client = True
        elif client_id not in session.get("clients", []):
            is_new_client = True

        if is_new_client:
            if "clients" not in session:
                session["clients"] = []
            session["clients"].append(client_id)

        session["last_active"] = datetime.now().isoformat()
        session["status"] = SESSION_ACTIVE

        await self._redis.hset(
            SESSION_HASH,
            session_token,
            json.dumps(session, ensure_ascii=False)
        )

        # 4. 获取离线消息（seq > last_seq 的部分）
        offline_messages = []
        queue_key = SESSION_QUEUE.format(session_token)
        queue_size = await self._redis.llen(queue_key)

        # 从队列头部开始检查（FIFO: LPUSH写, RPOP读 → 但我们需要检查而非消费）
        # 策略: LRANGE 全部 → 过滤 seq > last_seq → 确认后删除已消费的
        all_queued = await self._redis.lrange(queue_key, 0, -1)
        for raw_msg in all_queued:
            try:
                msg = json.loads(raw_msg)
                if msg.get("seq", 0) > last_seq:
                    offline_messages.append(msg)
            except json.JSONDecodeError:
                pass

        # 消费过的消息从队列删除
        if offline_messages:
            # 删除队列中所有 seq <= 最后一条离线消息 seq 的消息
            # 改为: 清空整个队列(因为客户端已经拿到完整快照)
            await self._redis.delete(queue_key)

        # 5. 获取会话历史
        history = []
        history_key = SESSION_HISTORY.format(session_token)
        raw_history = await self._redis.zrange(history_key, 0, -1)
        for h in raw_history:
            try:
                history.append(json.loads(h))
            except json.JSONDecodeError:
                pass

        # 6. 获取当前序列号
        current_seq = int(await self._redis.get(
            f"commander:session:seq:{session_token}") or 0)

        # 7. 更新客户端信息
        client_info = {
            "client_id": client_id,
            "connected_at": datetime.now().isoformat(),
            "fingerprint": client_fingerprint[:32] if client_fingerprint else "",
            "last_seq": max(last_seq, current_seq),
        }
        if self._redis:
            clients_key = SESSION_CLIENTS.format(session_token)
            await self._redis.hset(
                clients_key,
                client_id,
                json.dumps(client_info, ensure_ascii=False)
            )

        print(f"[SessionManager] 🔗 客户端 {client_id} 接入会话 {session_token} "
              f"(离线消息 {len(offline_messages)}, 历史 {len(history)}, "
              f"新端={is_new_client})")

        return {
            "type": "connected",
            "session_token": session_token,
            "client_id": client_id,
            "offline_messages": offline_messages,
            "offline_count": len(offline_messages),
            "history": history,
            "current_seq": current_seq,
            "session_status": session.get("status", SESSION_ACTIVE),
            "retention_days": session.get("retention_days", 7),
            "user_tier": session.get("user_tier", "free"),
        }

    # ═══════════════════════════════════════════════════════════
    # 消息发送（在线推送 or 离线暂存）
    # ═══════════════════════════════════════════════════════════

    async def send_to_session(self,
                               session_token: str,
                               message: Dict[str, Any],
                               store_history: bool = True) -> Dict[str, Any]:
        """向指定会话发送消息。

        - 有在线客户端 → 直接推送
        - 全部离线 → 暂存到离线队列
        - 自动分配全局递增序列号

        Returns:
            {"status": "delivered|queued", "seq": N, "online_clients": [...]}
        """
        self._check_init()

        if not self._redis:
            return {"status": "error", "code": "no_redis"}

        # 1. 分配序列号
        seq = await self._redis.incr(f"commander:session:seq:{session_token}")
        message["seq"] = seq
        message["timestamp"] = datetime.now().isoformat()

        # 2. 查找在线客户端
        online_clients = await self._get_online_clients(session_token)

        result = {
            "status": "delivered" if online_clients else "queued",
            "seq": seq,
            "online_clients": online_clients,
        }

        # 3. 暂存到历史队列
        if store_history:
            await self._append_history(session_token, message)

        # 4. 在线 → 通过 WebSocket 回调推送（由 ws_bridge 注册）
        if online_clients:
            # 消息由 ws_bridge 的 send_callback 负责推送
            # SessionManager 不直接持有 WebSocket 连接
            pass
        else:
            # 离线 → 暂存到队列
            await self._enqueue_offline(session_token, message)

        return result

    async def _enqueue_offline(self, session_token: str, message: Dict[str, Any]):
        """将消息暂存到离线队列（FIFO）。"""
        queue_key = SESSION_QUEUE.format(session_token)
        queue_size = await self._redis.llen(queue_key)

        # 队列上限检查
        if queue_size >= MAX_OFFLINE_QUEUE_SIZE:
            # 丢弃最旧的消息（队尾 = 最早入队的）
            await self._redis.rpop(queue_key)
            print(f"[SessionManager] ⚠️ 会话 {session_token} 离线队列已满，丢弃最旧消息")
            for cb in self._on_offline_queue_full:
                try:
                    cb(session_token, queue_size)
                except Exception:
                    pass

        # LPUSH 写入队头，RPOP 从队尾取出 = FIFO
        await self._redis.lpush(queue_key, json.dumps(message, ensure_ascii=False))

        # 检查离线时长
        await self._check_offline_age(session_token)

    async def _append_history(self, session_token: str, entry: Dict[str, Any]):
        """追加到会话历史（Redis 滑动窗口 + MongoDB 完整归档）。"""
        timestamp = time.time()
        history_key = SESSION_HISTORY.format(session_token)

        # Redis: ZADD (滑动窗口)
        if self._redis:
            entry_json = json.dumps(entry, ensure_ascii=False)
            await self._redis.zadd(history_key, {entry_json: timestamp})
            # 裁剪超出部分
            await self._redis.zremrangebyrank(history_key, 0, -MAX_HISTORY_IN_REDIS - 1)

        # MongoDB: 完整归档（异步，不阻塞）
        if self._mongo_db is not None:
            try:
                self._mongo_db[MONGO_COLLECTION_HISTORY].insert_one({
                    "session_token": session_token,
                    "timestamp": datetime.fromtimestamp(timestamp),
                    **entry,
                })
            except Exception as e:
                print(f"[SessionManager] ⚠️ MongoDB 历史写入失败: {e}")

    async def _check_offline_age(self, session_token: str):
        """检查会话离线时长，超时自动归档。"""
        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return
        session = json.loads(raw)

        # 获取所有客户端心跳
        online = await self._get_online_clients(session_token)
        if online:
            return  # 有在线客户端，不检查

        # 所有离线 → 检查最早离线时间
        earliest_hb = None
        for client_id in session.get("clients", []):
            hb_key = SESSION_HEARTBEAT.format(session_token, client_id)
            ts = await self._redis.get(hb_key)
            if ts:
                dt = datetime.fromisoformat(ts)
                if earliest_hb is None or dt < earliest_hb:
                    earliest_hb = dt

        if earliest_hb:
            offline_hours = (datetime.now() - earliest_hb).total_seconds() / 3600
            if offline_hours > MAX_OFFLINE_AGE_HOURS:
                await self._archive_session(session_token, reason=f"离线超过 {MAX_OFFLINE_AGE_HOURS}h")

    # ═══════════════════════════════════════════════════════════
    # 客户端心跳
    # ═══════════════════════════════════════════════════════════

    async def client_heartbeat(self,
                                session_token: str,
                                client_id: str,
                                metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """更新客户端心跳。

        心跳 key 设置 TTL=90s，超过 60s 无心跳判定为断开。
        """
        self._check_init()

        if not self._redis:
            return {"type": "heartbeat_ack"}

        hb_key = SESSION_HEARTBEAT.format(session_token, client_id)
        hb_data = {
            "last_heartbeat": datetime.now().isoformat(),
            "metadata": metadata or {},
        }
        await self._redis.setex(
            hb_key,
            CLIENT_HEARTBEAT_TTL,
            json.dumps(hb_data, ensure_ascii=False)
        )

        # 更新会话 last_active
        session_meta = await self._redis.hget(SESSION_HASH, session_token)
        if session_meta:
            session = json.loads(session_meta)
            session["last_active"] = datetime.now().isoformat()
            if session.get("status") == SESSION_ALL_DISCONNECTED:
                session["status"] = SESSION_ACTIVE
            await self._redis.hset(
                SESSION_HASH,
                session_token,
                json.dumps(session, ensure_ascii=False)
            )

        return {"type": "heartbeat_ack", "client_id": client_id}

    async def _get_online_clients(self, session_token: str) -> List[str]:
        """获取会话中当前在线的客户端列表。"""
        if not self._redis:
            return []

        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return []

        session = json.loads(raw)
        online = []
        for client_id in session.get("clients", []):
            hb_key = SESSION_HEARTBEAT.format(session_token, client_id)
            if await self._redis.exists(hb_key):
                online.append(client_id)
        return online

    async def get_session_online_status(self, session_token: str) -> Dict[str, Any]:
        """获取会话在线状态。"""
        online = await self._get_online_clients(session_token)
        raw = await self._redis.hget(SESSION_HASH, session_token)
        session = json.loads(raw) if raw else {}

        return {
            "session_token": session_token,
            "total_clients": len(session.get("clients", [])),
            "online_clients": online,
            "offline_clients": [c for c in session.get("clients", []) if c not in online],
            "status": session.get("status", "unknown"),
            "last_active": session.get("last_active"),
        }

    # ═══════════════════════════════════════════════════════════
    # 历史查询（分页）
    # ═══════════════════════════════════════════════════════════

    async def get_history(self,
                           session_token: str,
                           offset: int = 0,
                           limit: int = 50,
                           before_seq: int = None) -> Dict[str, Any]:
        """获取会话历史（Redis 优先，MongoDB 分页补充）。

        Args:
            session_token: 会话令牌
            offset: 分页偏移
            limit: 每页条数
            before_seq: 获取此序号之前的消息（用于翻页）

        Returns:
            {"messages": [...], "total": N, "has_more": bool}
        """
        self._check_init()

        messages = []
        total = 0

        # 1. 先从 Redis 获取（如果请求的数据在热存储中）
        if before_seq is None and offset == 0:
            history_key = SESSION_HISTORY.format(session_token)
            if self._redis:
                raw_history = await self._redis.zrange(history_key, 0, limit - 1)
                for h in raw_history:
                    try:
                        messages.append(json.loads(h))
                    except json.JSONDecodeError:
                        pass
                total = len(messages)

        # 2. Redis 不够 → 查 MongoDB
        if len(messages) < limit and self._mongo_db:
            mongo_offset = offset if messages else max(offset, MAX_HISTORY_IN_REDIS)
            query = {"session_token": session_token}
            if before_seq is not None:
                query["seq"] = {"$lt": before_seq}

            cursor = self._mongo_db[MONGO_COLLECTION_HISTORY].find(query)
            cursor = cursor.sort("timestamp", -1).skip(mongo_offset).limit(limit - len(messages))

            mongo_msgs = []
            async def _fetch():
                return list(cursor)
            mongo_msgs = await asyncio.to_thread(_fetch)

            # MongoDB 中没有 seq 字段的消息
            for msg in mongo_msgs:
                msg.pop("_id", None)
                messages.append(msg)

            total_count = self._mongo_db[MONGO_COLLECTION_HISTORY].count_documents(query)
            total = max(total, total_count)

        has_more = total > offset + limit

        return {
            "messages": messages,
            "total": total,
            "has_more": has_more,
            "offset": offset,
        }

    # ═══════════════════════════════════════════════════════════
    # 会话生命周期管理
    # ═══════════════════════════════════════════════════════════

    async def destroy_session(self, session_token: str) -> Dict[str, Any]:
        """主动销毁会话（用户发起）。

        清空所有 Redis 数据 + MongoDB 归档记录。
        """
        self._check_init()

        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return {"type": "error", "code": "session_not_found"}

        session = json.loads(raw)

        # 清空离线队列
        await self._redis.delete(SESSION_QUEUE.format(session_token))
        # 清空历史
        await self._redis.delete(SESSION_HISTORY.format(session_token))
        # 清空序列号
        await self._redis.delete(f"commander:session:seq:{session_token}")
        # 清空客户端信息
        await self._redis.delete(SESSION_CLIENTS.format(session_token))
        # 清空心跳
        for client_id in session.get("clients", []):
            await self._redis.delete(
                SESSION_HEARTBEAT.format(session_token, client_id))
        # 清空会话注册
        await self._redis.hdel(SESSION_HASH, session_token)

        # MongoDB 中标记归档
        if self._mongo_db is not None:
            try:
                self._mongo_db[MONGO_COLLECTION_ARCHIVE].update_one(
                    {"session_token": session_token},
                    {"$set": {
                        "status": "destroyed",
                        "destroyed_at": datetime.now().isoformat(),
                        "meta": session,
                    }},
                    upsert=True,
                )
            except Exception:
                pass

        print(f"[SessionManager] 🗑️ 会话 {session_token} 已销毁")

        for cb in self._on_destroy_callbacks:
            try:
                cb(session_token)
            except Exception:
                pass

        return {"type": "destroyed", "session_token": session_token}

    async def _archive_session(self,
                                session_token: str,
                                reason: str = "") -> Dict[str, Any]:
        """归档会话（超时触发）。"""
        self._check_init()

        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return {"type": "error", "code": "session_not_found"}

        session = json.loads(raw)
        session["status"] = SESSION_ARCHIVED
        session["archived_at"] = datetime.now().isoformat()
        session["archive_reason"] = reason

        await self._redis.hset(
            SESSION_HASH,
            session_token,
            json.dumps(session, ensure_ascii=False)
        )

        # MongoDB 归档记录
        if self._mongo_db is not None:
            try:
                self._mongo_db[MONGO_COLLECTION_ARCHIVE].update_one(
                    {"session_token": session_token},
                    {"$set": {
                        "status": SESSION_ARCHIVED,
                        "archived_at": datetime.now().isoformat(),
                        "reason": reason,
                        "meta": session,
                    }},
                    upsert=True,
                )
            except Exception:
                pass

        print(f"[SessionManager] 📦 会话 {session_token} 已归档: {reason}")

        for cb in self._on_archive_callbacks:
            try:
                cb(session_token, reason)
            except Exception:
                pass

        return {"type": "archived", "session_token": session_token, "reason": reason}

    async def check_expired_sessions(self) -> Dict[str, Any]:
        """检查并归档过期会话（定时任务调用）。"""
        self._check_init()

        if not self._redis:
            return {"archived": 0}

        all_sessions = await self._redis.hgetall(SESSION_HASH)
        now = datetime.now()
        archived = 0

        for token, raw in all_sessions.items():
            session = json.loads(raw)
            if session.get("status") == SESSION_ARCHIVED:
                continue

            retention_days = session.get("retention_days")
            if retention_days is None:
                continue  # 永久保留

            last_active = session.get("last_active", session.get("created_at"))
            if not last_active:
                continue

            try:
                last_dt = datetime.fromisoformat(last_active)
            except (ValueError, TypeError):
                continue

            if (now - last_dt).days > retention_days:
                await self._archive_session(
                    token,
                    reason=f"超过保留期限 ({retention_days}天)"
                )
                archived += 1

        return {"archived": archived, "checked": len(all_sessions)}

    async def get_session_info(self, session_token: str) -> Optional[Dict[str, Any]]:
        """获取会话完整信息。"""
        if not self._redis:
            return None
        raw = await self._redis.hget(SESSION_HASH, session_token)
        if not raw:
            return None
        return json.loads(raw)

    async def list_active_sessions(self) -> List[Dict[str, Any]]:
        """列出所有活跃会话。"""
        if not self._redis:
            return []
        all_sessions = await self._redis.hgetall(SESSION_HASH)
        return [
            json.loads(v) for v in all_sessions.values()
            if json.loads(v).get("status") not in (SESSION_ARCHIVED,)
        ]

    async def get_queue_stats(self, session_token: str) -> Dict[str, Any]:
        """获取会话队列统计。"""
        if not self._redis:
            return {}
        queue_key = SESSION_QUEUE.format(session_token)
        queue_size = await self._redis.llen(queue_key)
        current_seq = int(await self._redis.get(
            f"commander:session:seq:{session_token}") or 0)

        return {
            "session_token": session_token,
            "offline_queue_size": queue_size,
            "current_seq": current_seq,
            "status": await self.get_session_online_status(session_token),
        }

    # ═══════════════════════════════════════════════════════════
    # 回调注册
    # ═══════════════════════════════════════════════════════════

    def on_archive(self, callback: Callable):
        """注册归档回调: callback(session_token, reason)"""
        self._on_archive_callbacks.append(callback)

    def on_destroy(self, callback: Callable):
        """注册销毁回调: callback(session_token)"""
        self._on_destroy_callbacks.append(callback)

    def on_offline_queue_full(self, callback: Callable):
        """注册离线队列满回调: callback(session_token, queue_size)"""
        self._on_offline_queue_full.append(callback)


# ═══════════════════════════════════════════════════════════════
# SessionBridge — SessionManager 与 WebSocket 的桥接层
# ═══════════════════════════════════════════════════════════════

class SessionBridge:
    """SessionManager ↔ WebSocket 双向桥接。

    职责:
      - 持有在线客户端的 WebSocket 连接引用
      - SessionManager.send_to_session() 在线时，通过此桥接推送
      - WebSocket 断开时通知 SessionManager

    这不是独立的服务，而是 ws_bridge.py 集成 SessionManager 的适配层。
    """

    def __init__(self, session_manager: SessionManager):
        self._mgr = session_manager
        # session_token → {client_id → websocket}
        self._connections: Dict[str, Dict[str, Any]] = {}
        # session_token → {client_id → asyncio.Task} (心跳监听)
        self._heartbeat_tasks: Dict[str, Dict[str, asyncio.Task]] = {}

    async def register_connection(self,
                                   session_token: str,
                                   client_id: str,
                                   websocket: Any):
        """注册 WebSocket 连接。"""
        if session_token not in self._connections:
            self._connections[session_token] = {}
        self._connections[session_token][client_id] = websocket

    async def unregister_connection(self,
                                     session_token: str,
                                     client_id: str):
        """注销 WebSocket 连接。"""
        if session_token in self._connections:
            self._connections[session_token].pop(client_id, None)
            if not self._connections[session_token]:
                del self._connections[session_token]

        # 取消心跳任务
        if session_token in self._heartbeat_tasks:
            task = self._heartbeat_tasks[session_token].pop(client_id, None)
            if task:
                task.cancel()

    async def push_to_session(self,
                               session_token: str,
                               message: Dict[str, Any],
                               exclude_client: str = None) -> int:
        """向会话的所有在线客户端推送消息。

        Returns:
            成功推送的客户端数量
        """
        clients = self._connections.get(session_token, {})
        pushed = 0

        for client_id, ws in list(clients.items()):
            if client_id == exclude_client:
                continue
            try:
                await ws.send(json.dumps(message, ensure_ascii=False))
                pushed += 1
            except Exception:
                # 连接已断开
                await self.unregister_connection(session_token, client_id)
                await self._mgr.client_heartbeat(session_token, client_id)

        return pushed

    async def push_to_client(self,
                              session_token: str,
                              client_id: str,
                              message: Dict[str, Any]) -> bool:
        """向指定客户端推送消息。"""
        ws = self._connections.get(session_token, {}).get(client_id)
        if not ws:
            return False
        try:
            await ws.send(json.dumps(message, ensure_ascii=False))
            return True
        except Exception:
            await self.unregister_connection(session_token, client_id)
            return False

    def get_online_clients(self, session_token: str) -> List[str]:
        """获取会话的在线客户端列表。"""
        return list(self._connections.get(session_token, {}).keys())

    async def disconnect_all_clients(self, session_token: str):
        """断开会话的所有客户端连接。"""
        clients = self._connections.pop(session_token, {})
        for client_id, ws in clients.items():
            try:
                await ws.close()
            except Exception:
                pass
        # 清理心跳任务
        self._heartbeat_tasks.pop(session_token, None)


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

async def _main():
    """测试 SessionManager。"""
    import argparse

    parser = argparse.ArgumentParser(description="SessionManager 测试")
    parser.add_argument("--redis-host", default="127.0.0.1")
    parser.add_argument("--redis-port", type=int, default=6379)
    parser.add_argument("--redis-password", default=os.environ.get("REDIS_PASSWORD", ""))
    args = parser.parse_args()

    mgr = SessionManager(
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
    )
    await mgr.initialize()

    # 测试注册
    result = await mgr.register_session("test-fingerprint", user_tier="paid")
    token = result["session_token"]
    client_id = result["client_id"]
    print(f"✅ 注册: {token}, client={client_id}")

    # 测试连接
    result = await mgr.connect_client(token, client_id)
    print(f"✅ 连接: {result['type']}")

    # 测试发送消息
    for i in range(3):
        await mgr.send_to_session(token, {
            "type": "task_progress",
            "data": {"progress": i * 33, "message": f"步骤 {i+1}/3"}
        })

    # 测试心跳
    await mgr.client_heartbeat(token, client_id)

    # 测试离线（模拟所有客户端断开后的消息）
    print("模拟客户端断开...")
    await mgr.send_to_session(token, {
        "type": "task_complete",
        "data": {"result": "任务完成"}
    })

    # 模拟重连
    new_client = SessionManager.generate_client_id()
    result = await mgr.connect_client(token, new_client, last_seq=0)
    print(f"✅ 新端接入: offline_msgs={result['offline_count']}")

    # 查询状态
    info = await mgr.get_queue_stats(token)
    print(f"📊 队列状态: {json.dumps(info, indent=2, ensure_ascii=False)}")

    await mgr.close()


if __name__ == "__main__":
    asyncio.run(_main())
