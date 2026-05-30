
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
雅溪 模型路由配置 v2.0
====================
Agent → Model → Thinking 三级映射, Redis 热配置

DeepSeek 模型:
  deepseek-chat      — V3 标准, 平衡性价比 (默认)
  deepseek-reasoner  — R1 深度推理, 慢但准确
  deepseek-v4-pro    — V4 Pro, 复杂任务
  deepseek-v4-flash  — V4 Flash, 最快

Thinking 模式 (reasoning_effort):
  off   — 无推理链, 最快
  low   — 轻量推理
  medium— 平衡
  high  — 深度推理, 慢但可靠
  max   — 最大推理, 最慢

路由规则 (优先级从高到低):
  1. 任务级覆盖: payload 里带 _model 或 _thinking
  2. Agent→Task 映射: 查 AGENT_TASK_CONFIG
  3. Agent 默认: 查 AGENT_DEFAULTS
  4. 全局默认: deepseek-chat + high
"""

import json, os
from typing import Dict, Optional

# ═══════════════════════════════════════════════
# Agent 默认模型 (按角色)
# ═══════════════════════════════════════════════

AGENT_DEFAULTS = {
    # 深度分析型 → 需要推理
    "审计官":       {"model": "deepseek-chat", "thinking": "medium"},
    "系统医生":     {"model": "deepseek-chat", "thinking": "medium"},
    "品牌策略师":   {"model": "deepseek-chat", "thinking": "medium"},

    # 创意生成型 → 平衡
    "UI/UX设计师":  {"model": "deepseek-chat", "thinking": "medium"},
    "前端工程师":   {"model": "deepseek-chat", "thinking": "medium"},

    # 快速响应型 → 追求速度
    "翻译官":       {"model": "deepseek-chat", "thinking": "low"},
    "售前经理":     {"model": "deepseek-chat", "thinking": "low"},
    "商务经理":     {"model": "deepseek-chat", "thinking": "low"},

    # 通用
    "通用Agent":    {"model": "deepseek-chat", "thinking": "medium"},
    "修复Agent":    {"model": "deepseek-chat", "thinking": "medium"},
}

# ═══════════════════════════════════════════════
# Agent × 任务类型 → 模型覆盖
# ═══════════════════════════════════════════════

AGENT_TASK_CONFIG = {
    "审计官": {
        "audit":        {"model": "deepseek-chat", "thinking": "medium"},
        "diagnose":     {"model": "deepseek-chat", "thinking": "medium"},
        "fix":          {"model": "deepseek-chat", "thinking": "medium"},
        "drill":        {"model": "deepseek-chat", "thinking": "medium"},
    },
    "UI/UX设计师": {
        "redesign":     {"model": "deepseek-chat", "thinking": "high"},
        "design":       {"model": "deepseek-chat", "thinking": "medium"},
        "layout":       {"model": "deepseek-chat", "thinking": "medium"},
    },
    "翻译官": {
        "translate":    {"model": "deepseek-chat", "thinking": "low"},
        "i18n":         {"model": "deepseek-chat", "thinking": "low"},
    },
    "售前经理": {
        "quote":        {"model": "deepseek-chat", "thinking": "medium"},
        "search":       {"model": "deepseek-chat", "thinking": "low"},
    },
    "系统医生": {
        "diagnose_and_fix": {"model": "deepseek-chat", "thinking": "high"},
    },
}

# ═══════════════════════════════════════════════
# Commander 自身用的模型 (按任务类型)
# ═══════════════════════════════════════════════

COMMANDER_MODEL_CONFIG = {
    # 编排类 — 不需要深度推理
    "route":        {"model": "deepseek-chat", "thinking": "low"},
    "decompose":    {"model": "deepseek-chat", "thinking": "medium"},
    "score":        {"model": "deepseek-chat", "thinking": "low"},
    "summarize":    {"model": "deepseek-chat", "thinking": "low"},
    # 复杂推理
    "diagnose":     {"model": "deepseek-chat", "thinking": "high"},
    "evolve":       {"model": "deepseek-chat", "thinking": "high"},
    # 默认
    "default":      {"model": "deepseek-chat", "thinking": "medium"},
}


class ModelConfig:
    """模型配置管理器 — 从 Redis 热加载, 文件兜底"""

    REDIS_KEY = "yaxiio:model:config"

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._cache = None
        self._cache_ts = 0

    def get_agent_config(self, agent_name: str, task_action: str = "") -> dict:
        """获取 Agent 执行某个任务时应使用的模型配置"""
        # 1. 查 Redis 覆盖 (热配置)
        redis_config = self._load_redis(agent_name)
        if redis_config:
            if task_action and task_action in redis_config.get("tasks", {}):
                return redis_config["tasks"][task_action]
            if "default" in redis_config:
                return redis_config["default"]

        # 2. Agent × Task 精确匹配
        if agent_name in AGENT_TASK_CONFIG and task_action in AGENT_TASK_CONFIG[agent_name]:
            return dict(AGENT_TASK_CONFIG[agent_name][task_action])

        # 3. Agent 默认
        if agent_name in AGENT_DEFAULTS:
            return dict(AGENT_DEFAULTS[agent_name])

        # 4. 全局兜底
        return {"model": "deepseek-chat", "thinking": "medium"}

    def get_commander_config(self, task_type: str = "default") -> dict:
        """获取 Commander 自身使用的模型配置"""
        if task_type in COMMANDER_MODEL_CONFIG:
            return dict(COMMANDER_MODEL_CONFIG[task_type])
        return dict(COMMANDER_MODEL_CONFIG["default"])

    def _load_redis(self, agent_name: str) -> Optional[dict]:
        """从 Redis 加载热配置"""
        if not self.redis:
            return None
        try:
            raw = self.redis.client.get(f"{self.REDIS_KEY}:{agent_name}")
            if raw:
                return json.loads(raw)
        except:
            pass
        return None

    def save_redis(self, agent_name: str, config: dict):
        """热更新 Agent 模型配置"""
        if self.redis:
            self.redis.client.setex(
                f"{self.REDIS_KEY}:{agent_name}",
                86400,
                json.dumps(config, ensure_ascii=False)
            )


# ═══════════════════════════════════════════════
# 应用 thinking 模式到 API 调用
# ═══════════════════════════════════════════════

def apply_thinking(extra_body: dict, thinking: str) -> dict:
    """将 thinking 模式写入 extra_body"""
    body = dict(extra_body or {})
    if thinking and thinking != "off":
        body["reasoning_effort"] = thinking
    elif "reasoning_effort" in body:
        del body["reasoning_effort"]
    return body


# ── 便捷函数: 一行获取模型+thinking ──

def resolve(agent_name: str, task_action: str = "",
            redis_client=None, payload_override: dict = None) -> dict:
    """
    解析最终使用的模型配置。
    优先级: payload_override > Redis热配置 > Agent×Task > Agent默认 > 全局兜底
    """
    mc = ModelConfig(redis_client)

    # payload 覆盖 (最高优先级)
    if payload_override:
        override_model = payload_override.get("_model", "")
        override_thinking = payload_override.get("_thinking", "")
        if override_model or override_thinking:
            base = mc.get_agent_config(agent_name, task_action)
            if override_model:
                base["model"] = override_model
            if override_thinking:
                base["thinking"] = override_thinking
            return base

    return mc.get_agent_config(agent_name, task_action)
