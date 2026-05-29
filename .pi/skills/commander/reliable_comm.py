#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
优化三：双通道通信 + ACK 消息确认 — ReliableComm
===================================================
- 通道一（Pub/Sub）：广播消息、心跳、结果通知 — 低延迟，尽力送达
- 通道二（List）：关键指令 — 持久化、FIFO、ACK 确认，绝不丢失
- ACK 超时自动重发，3次失败后降级处理

Constitution R4: 消息格式标准化 JSON
Constitution R5: 30s 无响应重试3次，连续失败3次降级
Constitution R1: 使用 commander:* 前缀，TTL 代替 DEL
"""

import json
import os
import threading
import time
from typing import Callable, Optional

import redis


class ReliableComm:
    """双通道可靠通信层"""

    ACK_TIMEOUT = 5       # ACK 超时秒数
    MAX_RETRIES = 3       # 最大重试次数

    def __init__(self, agent_id: str,
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None):
        self.agent_id = agent_id
        self.redis = redis.Redis(
            host=redis_host, port=redis_port,
            password=redis_password or os.environ.get("REDIS_PASSWORD", ""), decode_responses=True,
        )
        self._command_handler: Optional[Callable] = None
        self._running = True

        # 后台监听关键指令队列
        self._listener_thread = threading.Thread(
            target=self._command_listener, daemon=True,
        )
        self._listener_thread.start()

    # ── 后台指令监听 ─────────────────────────────────────────

    def _command_listener(self):
        """后台线程：阻塞监听 List 关键指令通道。"""
        while self._running:
            try:
                result = self.redis.blpop(
                    f"commander:agent:command:{self.agent_id}",
                    timeout=1,
                )
                if result:
                    _, message = result
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        continue
                    self._handle_command(data)
            except redis.RedisError as e:
                print(f"[ReliableComm:{self.agent_id}] 监听异常: {e}")
                time.sleep(1)

    def _handle_command(self, command: dict):
        """处理收到的关键指令并发送 ACK。"""
        cmd_type = command.get("type", "")
        task_id = command.get("taskId", f"cmd-{int(time.time())}")

        # 交给外部处理器
        if self._command_handler:
            try:
                result = self._command_handler(cmd_type, command)
            except Exception as e:
                result = {"status": "error", "error": str(e)}
        else:
            result = {"status": "unhandled", "reason": f"no handler for {cmd_type}"}

        # 发送 ACK
        self._send_ack(task_id, result)

    def _send_ack(self, task_id: str, result: dict):
        """通过 Pub/Sub 发送 ACK 确认（广播给 Commander）。"""
        ack = {
            "from": self.agent_id,
            "to": "commander",
            "type": "ack",
            "taskId": task_id,
            "timestamp": time.time(),
            "payload": result,
        }
        self.redis.publish(
            "lightingmetal:agent:commander",
            json.dumps(ack, ensure_ascii=False),
        )

    # ── 发送关键指令 ─────────────────────────────────────────

    def send_critical_command(self, target_agent: str, command: dict,
                              expect_ack: bool = True,
                              timeout: Optional[int] = None) -> dict:
        """通过 List 通道发送关键指令，可选等待 ACK。

        Args:
            target_agent: 目标 Agent ID
            command: 指令 dict，至少含 "type" 字段
            expect_ack: 是否需要等待 ACK 确认
            timeout: ACK 等待超时秒数，默认 ACK_TIMEOUT

        Returns:
            {"status": "sent"/"ack_received"/"timeout"/"failed", ...}
        """
        timeout = timeout or self.ACK_TIMEOUT
        command.setdefault("taskId", f"cmd-{int(time.time() * 1000)}")
        command.setdefault("timestamp", time.time())
        command["from"] = self.agent_id

        task_id = command["taskId"]

        # 推送到目标 Agent 的命令 List
        self.redis.rpush(
            f"commander:agent:command:{target_agent}",
            json.dumps(command, ensure_ascii=False),
        )

        if not expect_ack:
            return {"status": "sent", "taskId": task_id}

        # 等待 ACK（带重试）
        for attempt in range(1, self.MAX_RETRIES + 1):
            start = time.time()
            while time.time() - start < timeout:
                ack_raw = self.redis.get(f"commander:ack:{task_id}")
                if ack_raw:
                    # ACK 读取后设短 TTL 自动过期（符合 R1）
                    self.redis.expire(f"commander:ack:{task_id}", 5)
                    return {
                        "status": "ack_received",
                        "taskId": task_id,
                        "attempt": attempt,
                        "ack": json.loads(ack_raw),
                    }
                time.sleep(0.1)

            # 超时，重发
            if attempt < self.MAX_RETRIES:
                self.redis.rpush(
                    f"commander:agent:command:{target_agent}",
                    json.dumps(command, ensure_ascii=False),
                )

        # 全部重试失败，降级
        return {
            "status": "timeout",
            "taskId": task_id,
            "attempts": self.MAX_RETRIES,
            "action": "degraded",
        }

    # ── 发送广播消息（Pub/Sub）────────────────────────────────

    def send_broadcast(self, channel: str, message: dict):
        """通过 Pub/Sub 发送广播消息。"""
        message.setdefault("from", self.agent_id)
        message.setdefault("timestamp", time.time())
        return self.redis.publish(channel, json.dumps(message, ensure_ascii=False))

    # ── 注册外部处理器 ───────────────────────────────────────

    def register_handler(self, handler: Callable):
        """注册指令处理器：handler(cmd_type, command) -> result dict"""
        self._command_handler = handler

    # ── 生命周期 ─────────────────────────────────────────────

    def shutdown(self):
        """优雅关闭。"""
        self._running = False

    def wait_ack_channel(self, task_id: str):
        """在 ACK 通道写入结果，供 Commander 消费（Pub/Sub 场景下使用）。"""
        pass  # ACK 通过 Pub/Sub 自动广播，无需额外操作


# ── 使用示例 ─────────────────────────────────────────────────
if __name__ == "__main__":
    comm = ReliableComm("commander")

    # 注册处理器
    def my_handler(cmd_type, cmd):
        print(f"收到指令: {cmd_type}")
        return {"status": "ok", "handled_by": "commander"}

    comm.register_handler(my_handler)

    # 发送关键指令
    result = comm.send_critical_command("翻译官", {
        "type": "task",
        "payload": {"action": "translate", "text": "热镀锌螺旋地桩", "target_lang": "ru"},
    })
    print(f"指令发送结果: {result}")

    # 发送广播
    comm.send_broadcast("lightingmetal:agent:翻译官", {
        "type": "heartbeat_check",
        "to": "翻译官",
    })
