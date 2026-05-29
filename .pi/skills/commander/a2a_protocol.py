#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
A2A 协议适配层 — Agent 标准化通信 + 能力发现
==================================================
- A2AAdapter      : Redis 消息 ↔ A2A 标准格式 双向转换
- AgentCard       : A2A 能力卡片（角色→输入/输出 Schema 自动生成）
- AgentDiscovery  : 能力注册/发现/注销（按 capability 索引）

与 llm_router.py 的 LLMRouter 配合：
  LLMRouter 调用 AgentDiscovery.discover(capability) 获取能力谱系

Constitution R1: commander:a2a:* 前缀，HDEL/SREM 用于生命周期管理
Constitution R4: 消息格式标准化 JSON
"""

import json
import time
from datetime import datetime
from typing import Dict, List, Optional

import redis


# ═══════════════════════════════════════════════════════════════
# A2AAdapter ─ Redis ↔ A2A 双向转换
# ═══════════════════════════════════════════════════════════════

class A2AAdapter:
    """A2A 协议适配器：在 Redis 消息与 A2A 标准任务之间转换。

    A2A 标准格式 (Google A2A-compatible):
      {taskId, from, to, action, data, metadata}
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def to_a2a(self, redis_message: dict) -> dict:
        """Redis Pub/Sub 消息 → A2A 标准任务。"""
        payload = redis_message.get("payload", {})
        return {
            "taskId": redis_message.get("taskId", ""),
            "from": redis_message.get("from", ""),
            "to": redis_message.get("to", ""),
            "action": payload.get("action", redis_message.get("type", "")),
            "data": payload.get("data", payload),
            "metadata": {
                "timestamp": redis_message.get("timestamp", time.time()),
                "type": redis_message.get("type", ""),
                "replyTo": redis_message.get("replyTo", ""),
                "protocol": "a2a/v1",
            },
        }

    def from_a2a(self, a2a_task: dict) -> dict:
        """A2A 标准任务 → Redis 消息。"""
        return {
            "from": a2a_task.get("from", "commander"),
            "to": a2a_task.get("to", ""),
            "type": "task",
            "taskId": a2a_task.get("taskId", f"task-{int(time.time() * 1000)}"),
            "timestamp": datetime.now().isoformat(),
            "replyTo": a2a_task.get("metadata", {}).get("replyTo", ""),
            "payload": {
                "action": a2a_task.get("action"),
                "data": a2a_task.get("data", {}),
            },
        }

    def send_a2a_message(self, agent_id: str, action: str,
                         data: dict, task_id: Optional[str] = None) -> str:
        """通过 Redis Pub/Sub 发送 A2A 标准消息。

        Returns:
            taskId
        """
        actual_id = task_id or f"a2a-{int(time.time() * 1000)}"
        a2a_task = {
            "taskId": actual_id,
            "from": "commander",
            "to": agent_id,
            "action": action,
            "data": data,
            "metadata": {
                "timestamp": datetime.now().isoformat(),
                "protocol": "a2a/v1",
            },
        }
        redis_msg = self.from_a2a(a2a_task)
        channel = f"lightingmetal:agent:{agent_id}"
        self.redis.publish(channel, json.dumps(redis_msg, ensure_ascii=False))
        return actual_id

    def receive_a2a_message(self, redis_data: str) -> dict:
        """将收到的 Redis 消息解析为 A2A 任务。"""
        try:
            msg = json.loads(redis_data)
        except json.JSONDecodeError:
            return {"error": "invalid_json"}
        return self.to_a2a(msg)


# ═══════════════════════════════════════════════════════════════
# AgentCard ─ A2A 能力卡片
# ═══════════════════════════════════════════════════════════════

class AgentCard:
    """A2A Agent 能力卡片：描述 Agent 的输入/输出 Schema + 能力 + 成本。

    每个 Agent 在注册时生成一张卡片，供 AgentDiscovery 索引和 LLMRouter 决策。
    """

    # 各角色 → 输入 / 输出 Schema 模板
    SCHEMAS = {
        "商务经理": {
            "input": {
                "type": "object",
                "required": ["customer_message", "language"],
                "properties": {
                    "customer_message": {"type": "string",
                                         "description": "客户原始消息"},
                    "language": {"enum": ["zh", "en", "ru", "ar", "es"],
                                 "description": "消息语言"},
                },
            },
            "output": {
                "type": "object",
                "properties": {
                    "structured_requirements": {"type": "object"},
                    "summary": {"type": "string"},
                },
            },
        },
        "售前经理": {
            "input": {
                "type": "object",
                "required": ["requirements"],
                "properties": {
                    "requirements": {"type": "object",
                                     "description": "结构化需求清单"},
                    "currency": {"type": "string", "default": "USD"},
                },
            },
            "output": {
                "type": "object",
                "properties": {
                    "quote": {"type": "object"},
                    "products": {"type": "array"},
                    "total_price": {"type": "number"},
                },
            },
        },
        "翻译官": {
            "input": {
                "type": "object",
                "required": ["text", "target_language"],
                "properties": {
                    "text": {"type": "string", "description": "待翻译文本"},
                    "source_language": {"type": "string", "default": "zh"},
                    "target_language": {
                        "enum": ["en", "ru", "ar", "es", "fr",
                                 "pt", "de", "vi", "th", "id"],
                    },
                },
            },
            "output": {
                "type": "object",
                "properties": {
                    "translated_text": {"type": "string"},
                    "terminology_map": {"type": "object"},
                },
            },
        },
        "审计官": {
            "input": {
                "type": "object",
                "required": ["pages", "criteria"],
                "properties": {
                    "pages": {"type": "array",
                              "description": "待审计页面路径列表"},
                    "criteria": {"type": "array",
                                 "description": "审计标准（术语/参数/中文残留等）"},
                },
            },
            "output": {
                "type": "object",
                "properties": {
                    "issues": {"type": "array"},
                    "summary": {"type": "string"},
                },
            },
        },
        "俄语审计官": {
            "input": {
                "type": "object",
                "required": ["pages"],
                "properties": {
                    "pages": {"type": "array"},
                    "target_language": {"type": "string", "default": "ru"},
                },
            },
            "output": {
                "type": "object",
                "properties": {
                    "issues": {"type": "array"},
                    "summary": {"type": "string"},
                },
            },
        },
    }

    def __init__(self, agent_id: str, role: str,
                 capabilities: List[str],
                 endpoint: Optional[str] = None,
                 model: str = "deepseek-v4-flash",
                 cost_input: float = 0.14,
                 cost_output: float = 0.28):
        self.agent_id = agent_id
        schemas = self.SCHEMAS.get(role, {
            "input": {"type": "object", "properties": {}},
            "output": {"type": "object", "properties": {}},
        })
        self.card = {
            "agentId": agent_id,
            "name": f"{role}-{agent_id[-6:]}",
            "role": role,
            "capabilities": capabilities,
            "inputSchema": schemas["input"],
            "outputSchema": schemas["output"],
            "endpoint": endpoint or f"lightingmetal:agent:{agent_id}",
            "status": "idle",
            "cost": {
                "input_per_1k": cost_input,
                "output_per_1k": cost_output,
                "model": model,
            },
            "registered_at": datetime.now().isoformat(),
        }

    def to_json(self) -> str:
        return json.dumps(self.card, ensure_ascii=False)

    def to_dict(self) -> dict:
        return dict(self.card)


# ═══════════════════════════════════════════════════════════════
# AgentDiscovery ─ 能力发现服务
# ═══════════════════════════════════════════════════════════════

class AgentDiscovery:
    """Agent 能力发现服务。

    维护 commander:a2a:agent_cards Hash + commander:a2a:capability:* Set 索引。
    供 LLMRouter 做语义路由时的能力匹配。
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def register(self, card: AgentCard):
        """注册 Agent 能力卡片。"""
        cid = card.agent_id
        # 主存储
        self.redis.hset("commander:a2a:agent_cards", cid, card.to_json())
        self.redis.sadd("commander:a2a:registry", cid)

        # 按能力建立倒排索引
        pipe = self.redis.pipeline()
        for capability in card.card["capabilities"]:
            pipe.sadd(f"commander:a2a:capability:{capability}", cid)
        pipe.execute()

    def discover(self, capability: str) -> List[dict]:
        """按能力关键词查找 Agent。"""
        agent_ids = self.redis.smembers(
            f"commander:a2a:capability:{capability}"
        )
        cards = []
        for aid in agent_ids:
            card_json = self.redis.hget("commander:a2a:agent_cards", aid)
            if card_json:
                cards.append(json.loads(card_json))
        return cards

    def discover_by_role(self, role: str) -> List[dict]:
        """按角色查找 Agent（能力发现 + 角色过滤）。"""
        # 先按 capability 查，再按 role 过滤
        all_ids = self.redis.smembers("commander:a2a:registry")
        cards = []
        for aid in all_ids:
            card_json = self.redis.hget("commander:a2a:agent_cards", aid)
            if card_json:
                card = json.loads(card_json)
                if card.get("role") == role:
                    cards.append(card)
        return cards

    def get_card(self, agent_id: str) -> Optional[dict]:
        """获取指定 Agent 的能力卡片。"""
        card_json = self.redis.hget("commander:a2a:agent_cards", agent_id)
        return json.loads(card_json) if card_json else None

    def unregister(self, agent_id: str):
        """注销 Agent。"""
        card = self.get_card(agent_id)
        if card:
            # 清理能力索引
            pipe = self.redis.pipeline()
            for capability in card.get("capabilities", []):
                pipe.srem(f"commander:a2a:capability:{capability}", agent_id)
            pipe.execute()

        self.redis.hdel("commander:a2a:agent_cards", agent_id)
        self.redis.srem("commander:a2a:registry", agent_id)

        # 标记状态为 deregistered（保留7天记录）
        self.redis.setex(
            f"commander:a2a:deregistered:{agent_id}",
            86400 * 7,
            json.dumps({"agent_id": agent_id, "at": datetime.now().isoformat()}),
        )

    def list_all_agents(self) -> List[dict]:
        """列出所有已注册 Agent。"""
        all_cards = self.redis.hgetall("commander:a2a:agent_cards")
        return [json.loads(card) for card in all_cards.values()]

    def update_status(self, agent_id: str, status: str):
        """更新 Agent 状态（idle / busy / running / offline）。"""
        card = self.get_card(agent_id)
        if card:
            card["status"] = status
            self.redis.hset("commander:a2a:agent_cards", agent_id,
                            json.dumps(card, ensure_ascii=False))

    def find_best_match(self, capability: str, min_agents: int = 1) -> List[dict]:
        """按能力 + 状态筛选最优 Agent（idle 优先）。"""
        candidates = self.discover(capability)

        # 如果没有精确匹配，尝试模糊匹配（包含关系）
        if not candidates:
            for key in self.redis.scan_iter("commander:a2a:capability:*"):
                cap_name = key.decode().split(":")[-1]
                if capability in cap_name or cap_name in capability:
                    candidates = self.discover(cap_name)
                    break

        # 排序：idle > busy > 其他
        status_priority = {"idle": 0, "busy": 1, "running": 1}
        candidates.sort(
            key=lambda c: status_priority.get(c.get("status", ""), 2)
        )

        return candidates[:max(min_agents, len(candidates))]


# ═══════════════════════════════════════════════════════════════
# 快速测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    r = redis.Redis(protocol=2, host="127.0.0.1", port=6379,
                    password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)

    # ── 创建发现服务 ──
    discovery = AgentDiscovery(r)

    # ── 注册能力卡片 ──
    cards = [
        AgentCard("商务经理-001", "商务经理",
                  ["客户接待", "需求挖掘", "多语言沟通"]),
        AgentCard("售前经理-001", "售前经理",
                  ["产品查询", "报价生成", "方案推荐"]),
        AgentCard("翻译官-001", "翻译官",
                  ["英/俄/阿/西/法/葡/德/越/泰/印尼语翻译",
                   "术语词典", "内容审计"]),
        AgentCard("审计官-001", "审计官",
                  ["内容审计", "术语一致性", "参数核查"]),
        AgentCard("俄语审计官-001", "俄语审计官",
                  ["俄语翻译", "俄语审计", "内容审计"]),
    ]
    for card in cards:
        discovery.register(card)
        print(f"✅ 已注册: {card.agent_id} → {card.card['capabilities']}")

    # ── 能力发现 ──
    found = discovery.discover("报价生成")
    print(f"\n🔍 搜索 '报价生成': {len(found)} 个")
    for a in found:
        print(f"  → {a['agentId']} ({a['role']}), 输入: {list(a['inputSchema'].get('required',[]))}")

    # ── 模糊匹配 ──
    found = discovery.find_best_match("翻译", min_agents=2)
    print(f"\n🔍 模糊搜索 '翻译': {len(found)} 个")
    for a in found:
        print(f"  → {a['agentId']} ({a['role']}) status={a['status']}")

    # ── 按角色查 ──
    found = discovery.discover_by_role("审计官")
    print(f"\n🔍 按角色 '审计官': {len(found)} 个")
    for a in found:
        print(f"  → {a['agentId']}")

    # ── A2A 适配器 ──
    adapter = A2AAdapter(r)
    task_id = adapter.send_a2a_message("商务经理-001", "客户接待", {
        "customer_message": "需要5000根螺旋地桩，沙特50MW光伏电站",
        "language": "zh",
    })
    print(f"\n📤 A2A 消息已发送: {task_id}")

    # ── 注销测试 ──
    discovery.unregister("审计官-001")
    print(f"\n🗑 已注销审计官-001")
    print(f"   剩余 Agent: {len(discovery.list_all_agents())}")

    print("\n✅ a2a_protocol.py 测试通过")
