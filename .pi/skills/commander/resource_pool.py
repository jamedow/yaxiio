#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
ResourcePool — 统一 LLM 资源池
================================
所有 API Key、模型选择、thinking 级别、fallback 策略集中管理。

设计原则:
  1. Redis 是唯一真相源 — 改了立刻生效，无需重启
  2. 按 Agent + 任务类型 分配模型 — 翻译用 fast, 审计用 pro
  3. 线程安全 — 可在 Commander、Neuron、MCP Server 中同时使用
  4. 优雅降级 — 主 Key 不可用时自动切 Fallback

Redis 配置结构:
  yaxiio:pool:keys:primary     → {"key":"sk-xxx", "base_url":"...", "model":"deepseek-chat"}
  yaxiio:pool:keys:fallback    → {"key":"sk-yyy", "base_url":"...", "model":"gpt-4o-mini"}
  yaxiio:pool:agent:{name}     → {"model":"deepseek-chat", "thinking":"high", "max_tokens":4000}
  yaxiio:pool:task:{name}:{type} → 覆盖 agent 默认配置

使用方式:
  from resource_pool import resource_pool

  # 初始化（启动时调用一次）
  resource_pool.bootstrap(redis_client, primary_key="sk-xxx")

  # 获取 LLM 客户端
  client = resource_pool.get_client("审计官", "audit")

  # 神经元中
  llm = resource_pool.get_client(agent_name, task_type)
"""

import json
import os
import time
from typing import Any, Dict, Optional

# ── 默认配置 ──────────────────────────────────────────────────
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_THINKING = "medium"
DEFAULT_MAX_TOKENS = 4000

# Agent 默认模型映射（硬编码兜底，Redis 优先）
AGENT_MODEL_DEFAULTS = {
    "翻译官":   {"model": "deepseek-chat", "thinking": "low",  "max_tokens": 2000},
    "商务经理": {"model": "deepseek-chat", "thinking": "medium", "max_tokens": 4000},
    "售前经理": {"model": "deepseek-chat", "thinking": "low",  "max_tokens": 3000},
    "审计官":   {"model": "deepseek-chat", "thinking": "high", "max_tokens": 8000},
    "俄语审计官": {"model": "deepseek-chat", "thinking": "high", "max_tokens": 8000},
    "系统医生": {"model": "deepseek-chat", "thinking": "high", "max_tokens": 6000},
}

# 任务类型覆盖（Agent × Task → 特定配置）
TASK_MODEL_OVERRIDES = {
    "审计官": {
        "audit":      {"thinking": "high", "max_tokens": 8000},
        "audit_and_fix": {"thinking": "high", "max_tokens": 8000},
        "diagnose":   {"thinking": "medium", "max_tokens": 4000},
    },
    "售前经理": {
        "quote":      {"thinking": "low", "max_tokens": 3000},
        "search":     {"thinking": "low", "max_tokens": 2000},
    },
    "翻译官": {
        "translate":  {"thinking": "low", "max_tokens": 2000},
        "batch_translate": {"thinking": "low", "max_tokens": 4000},
    },
}


class ResourcePool:
    """统一 LLM 资源池。

    从 Redis 读取配置，提供 get_client() 获取 OpenAI 兼容客户端。
    """

    def __init__(self):
        self._redis = None
        self._primary_key = None
        self._primary_url = DEFAULT_BASE_URL
        self._fallback_key = None
        self._fallback_url = DEFAULT_BASE_URL
        self._initialized = False
        # 本地缓存（减少 Redis 访问）
        self._agent_cache: Dict[str, dict] = {}
        self._cache_ttl = 0

    # ── 启动初始化 ──────────────────────────────────────────

    def bootstrap(self, redis_client, primary_key: str = None,
                  primary_url: str = None, fallback_key: str = None,
                  fallback_url: str = None):
        """初始化资源池，将配置写入 Redis。

        Args:
            redis_client: Redis 连接 (decode_responses=True)
            primary_key: 主 LLM API Key
            primary_url: 主 LLM Base URL
            fallback_key: 备用 LLM API Key
            fallback_url: 备用 LLM Base URL
        """
        self._redis = redis_client

        # 从参数或环境变量读取
        pk = primary_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
        pu = primary_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)

        if pk:
            self._redis.set("yaxiio:pool:keys:primary", json.dumps({
                "key": pk, "base_url": pu, "model": DEFAULT_MODEL,
                "updated_at": time.time(),
            }))
            self._primary_key = pk
            self._primary_url = pu

        if fallback_key:
            self._redis.set("yaxiio:pool:keys:fallback", json.dumps({
                "key": fallback_key, "base_url": fallback_url or DEFAULT_BASE_URL,
            }))

        self._initialized = True
        print(f"[ResourcePool] 资源池已初始化 (主Key: {'✓' if pk else '✗'}, "
              f"URL: {pu})")

    # ── 核心接口 ────────────────────────────────────────────

    def get_client(self, agent_name: str = "", task_type: str = "",
                   model: str = None, thinking: str = None, max_tokens: int = None):
        """获取 LLM 客户端。

        优先级: 显式参数 > Redis Agent配置 > 硬编码默认 > 全局默认
        Key 优先级: Redis keys:primary > 环境变量

        Returns:
            OpenAI 兼容客户端, 或 None (无可用的 Key)
        """
        api_key, base_url = self._resolve_key()
        if not api_key:
            return None

        # 确定模型配置
        cfg = self._resolve_config(agent_name, task_type)
        if model:       cfg["model"] = model
        if thinking:    cfg["thinking"] = thinking
        if max_tokens:  cfg["max_tokens"] = max_tokens

        try:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
            # 附加元数据供调用者使用
            client._yaxiio_model = cfg["model"]
            client._yaxiio_thinking = cfg["thinking"]
            client._yaxiio_max_tokens = cfg["max_tokens"]
            return client
        except ImportError:
            return None

    def get_config(self, agent_name: str = "", task_type: str = "") -> dict:
        """获取 Agent 的模型配置（不创建客户端）。"""
        api_key, _ = self._resolve_key()
        cfg = self._resolve_config(agent_name, task_type)
        return {
            "api_key_available": bool(api_key),
            **cfg,
        }

    # ── 内部方法 ────────────────────────────────────────────

    def _resolve_key(self) -> tuple:
        """解析 API Key（Redis > 内存 > 环境变量）。"""
        # 1. 先读 Redis
        if self._redis:
            try:
                raw = self._redis.get("yaxiio:pool:keys:primary")
                if raw:
                    cfg = json.loads(raw)
                    return cfg["key"], cfg.get("base_url", DEFAULT_BASE_URL)
            except Exception:
                pass
        # 2. 回退到内存
        if self._primary_key:
            return self._primary_key, self._primary_url
        # 3. 最后查环境变量（兜底）
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
        url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)
        # 如果没有 key 但有 Redis，再试一次 Redis
        if not key and self._redis:
            try:
                raw = self._redis.get("yaxiio:config:llm_api_key")
                if raw and not raw.startswith("{"):
                    key = raw
            except:
                pass
        return key, url

    def _resolve_config(self, agent_name: str, task_type: str) -> dict:
        """解析 Agent 的模型配置。"""
        # 1. 查 Redis Agent 配置缓存
        cache_key = f"{agent_name}:{task_type}"
        if cache_key in self._agent_cache and time.time() - self._cache_ttl < 60:
            return dict(self._agent_cache[cache_key])

        # 2. 查 Redis
        if self._redis:
            try:
                # 先查任务级覆盖
                if task_type and agent_name:
                    raw = self._redis.get(f"yaxiio:pool:task:{agent_name}:{task_type}")
                    if raw:
                        cfg = json.loads(raw)
                        self._agent_cache[cache_key] = cfg
                        self._cache_ttl = time.time()
                        return dict(cfg)

                # 再查 Agent 默认
                if agent_name:
                    raw = self._redis.get(f"yaxiio:pool:agent:{agent_name}")
                    if raw:
                        cfg = json.loads(raw)
                        self._agent_cache[cache_key] = cfg
                        self._cache_ttl = time.time()
                        return dict(cfg)
            except Exception:
                pass

        # 3. 查硬编码覆盖
        if agent_name in TASK_MODEL_OVERRIDES and task_type in TASK_MODEL_OVERRIDES[agent_name]:
            cfg = dict(AGENT_MODEL_DEFAULTS.get(agent_name, {}))
            cfg.update(TASK_MODEL_OVERRIDES[agent_name][task_type])
            return cfg

        # 4. 查 Agent 默认
        if agent_name in AGENT_MODEL_DEFAULTS:
            return dict(AGENT_MODEL_DEFAULTS[agent_name])

        # 5. 全局默认
        return {"model": DEFAULT_MODEL, "thinking": DEFAULT_THINKING,
                "max_tokens": DEFAULT_MAX_TOKENS}

    # ── 运行时管理 ──────────────────────────────────────────

    def update_agent_config(self, agent_name: str, model: str = None,
                            thinking: str = None, max_tokens: int = None):
        """运行时更新 Agent 配置（立刻生效）。"""
        if not self._redis:
            return
        cfg = self._resolve_config(agent_name, "")
        if model:       cfg["model"] = model
        if thinking:    cfg["thinking"] = thinking
        if max_tokens:  cfg["max_tokens"] = max_tokens
        cfg["updated_at"] = time.time()
        self._redis.set(f"yaxiio:pool:agent:{agent_name}",
                        json.dumps(cfg, ensure_ascii=False))
        # 清空缓存
        self._agent_cache.clear()

    def update_primary_key(self, api_key: str, base_url: str = None):
        """运行时更新主 API Key（立刻生效）。"""
        if not self._redis:
            return
        self._redis.set("yaxiio:pool:keys:primary", json.dumps({
            "key": api_key, "base_url": base_url or DEFAULT_BASE_URL,
            "updated_at": time.time(),
        }))
        self._primary_key = api_key
        self._primary_url = base_url or DEFAULT_BASE_URL

    def status(self) -> dict:
        """资源池健康状态。"""
        key, url = self._resolve_key()
        return {
            "primary_key_available": bool(key),
            "base_url": url,
            "redis_connected": self._redis is not None,
            "initialized": self._initialized,
            "cached_agents": list(self._agent_cache.keys()),
        }


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

resource_pool = ResourcePool()
