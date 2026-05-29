#!/usr/bin/env python3
"""
Token Budget Controller — 优先级队列上下文裁剪引擎
=====================================================
当 Agent 对话上下文超过 LLM 窗口的 80% 时触发压缩。

裁剪优先级:
  P0: 当前任务指令           → NEVER CLIP
  P1: 关键决策 (keep_last=5)  → 保留最新5个
  P2: 最近3轮对话            → 保留最新3轮
  P3: 超过24h的历史消息      → 丢弃
  P4: 溢出区                 → 全部丢弃

Constitution R1: commander:* 前缀
"""

import json
import os
import time
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple, Union

# dataclasses 兼容（Python 3.6 backport）
try:
    from dataclasses import dataclass, field
except ImportError:
    # Fallback: 轻量 dataclass 装饰器
    def dataclass(cls=None, **kwargs):
        def wrap(c):
            orig_init = c.__init__
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
                if orig_init is not object.__init__:
                    orig_init(self, **kw)
            c.__init__ = __init__
            return c
        return wrap(cls) if cls else wrap
    def field(**kwargs):
        return kwargs

# ── 可选依赖 ──
HAS_TIKTOKEN = False
try:
    import tiktoken
    HAS_TIKTOKEN = True
except ImportError:
    pass

HAS_REDIS = False
try:
    import redis  # type: ignore
    HAS_REDIS = True
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════
# 优先级枚举
# ═══════════════════════════════════════════════════════════════

class MessagePriority(IntEnum):
    """消息优先级（数值越小越重要）。"""
    CRITICAL = 0     # P0: 当前任务指令 — 永不裁剪
    KEY_DECISION = 1 # P1: 关键决策
    RECENT_ROUND = 2 # P2: 最近3轮对话
    HISTORICAL = 3   # P3: 超过24h
    OVERFLOW = 4     # P4: 溢出区（丢弃）


# ═══════════════════════════════════════════════════════════════
# 模型窗口配置
# ═══════════════════════════════════════════════════════════════

MODEL_WINDOWS: Dict[str, int] = {
    "deepseek-chat": 65536,
    "deepseek-reasoner": 65536,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
}

# 默认窗口（未知模型）
DEFAULT_WINDOW = 65536

# 触发阈值: 上下文的 80%
THRESHOLD_RATIO = 0.80
# 压缩目标: 60%
TARGET_RATIO = 0.60

# 保留数量
KEEP_KEY_DECISIONS = 5
KEEP_RECENT_ROUNDS = 3
DISCARD_OLDER_THAN_HOURS = 24


# ═══════════════════════════════════════════════════════════════
# Token 估算器
# ═══════════════════════════════════════════════════════════════

class TokenEstimator:
    """Token 计数。优先使用 tiktoken，fallback 到字符/4 估算。"""

    def __init__(self):
        self._mem_stats: Dict[str, Dict] = {}
        self._mem_traces: Dict[str, List[Dict]] = {}
        self._encoding = None
        if HAS_TIKTOKEN:
            try:
                self._encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                pass

    def count(self, text: str) -> int:
        """估算文本 token 数。"""
        if self._encoding:
            return len(self._encoding.encode(text))
        # Fallback: 粗略估算 (中文字符 ≈ 1.5 token, 英文 ≈ 0.25 token)
        return max(1, len(text) // 4)

    def count_messages(self, messages: List[Dict]) -> int:
        """估算消息列表 token 总数。"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.count(content)
            elif isinstance(content, list):
                # 多模态 content
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.count(part.get("text", ""))
            # 每条消息额外 4 token (role + formatting)
            total += 4
        return total


# ═══════════════════════════════════════════════════════════════
# 消息分类器
# ═══════════════════════════════════════════════════════════════

@dataclass
class ClassifiedMessage:
    """分类后的消息。"""
    priority: MessagePriority
    index: int          # 原始位置
    msg: Dict
    age_hours: float
    is_system: bool = False
    round_num: int = 0  # 对话轮次（从1开始，越大越新）


class MessageClassifier:
    """将消息列表按优先级分类。"""

    KEY_DECISION_MARKERS = [
        "decision:", "conclusion:", "selected:", "approved:",
        "确认", "决定", "选择", "最终方案", "报价",
    ]

    def classify(self, messages: List[Dict], current_task: str = "") -> List[ClassifiedMessage]:
        """分类消息列表。

        Args:
            messages: OpenAI 格式的消息列表 [{role, content}, ...]
            current_task: 当前任务描述（用于识别 P0）

        Returns:
            分类后的消息列表（按原始顺序）。
        """
        now = datetime.now()
        classified = []

        # 识别对话轮次（user-assistant 对）
        round_num = 0
        last_role = None
        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            if role == "user" and last_role != "user":
                round_num += 1
            last_role = role

            content = msg.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False)

            # 计算年龄
            age_hours = 0
            ts = msg.get("timestamp") or msg.get("created_at")
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        dt = datetime.fromtimestamp(ts)
                    else:
                        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    age_hours = (now - dt.replace(tzinfo=None)).total_seconds() / 3600
                except Exception:
                    pass

            # 分类
            priority = self._classify_one(msg, content, current_task, round_num, age_hours)

            classified.append(ClassifiedMessage(
                priority=priority,
                index=i,
                msg=msg,
                age_hours=age_hours,
                is_system=(role == "system"),
                round_num=round_num,
            ))

        return classified

    def _classify_one(
        self, msg: Dict, content: str, current_task: str,
        round_num: int, age_hours: float,
    ) -> MessagePriority:
        """分类单条消息。"""
        role = msg.get("role", "")

        # P0: system prompt / 当前任务指令
        if role == "system":
            return MessagePriority.CRITICAL
        if current_task and current_task in content:
            return MessagePriority.CRITICAL

        # P1: 含关键决策标记
        content_lower = content.lower()
        for marker in self.KEY_DECISION_MARKERS:
            if marker.lower() in content_lower:
                return MessagePriority.KEY_DECISION

        # P3: 超过24小时
        if age_hours > DISCARD_OLDER_THAN_HOURS:
            return MessagePriority.HISTORICAL

        # P2: 最新 N 轮（后面由裁剪器筛选）
        return MessagePriority.RECENT_ROUND


# ═══════════════════════════════════════════════════════════════
# 裁剪引擎
# ═══════════════════════════════════════════════════════════════

@dataclass
class ClipResult:
    """裁剪结果。"""
    original_tokens: int
    final_tokens: int
    saved_tokens: int
    messages: List[Dict]
    clipped: bool
    details: Dict = field(default_factory=dict)


class TokenBudgetController:
    """
    Token 预算控制器。

    用法:
        ctrl = TokenBudgetController()
        result = ctrl.check(messages, model="deepseek-chat", current_task="翻译俄语页面")

        if result.clipped:
            print(f"裁剪: {result.original_tokens} → {result.final_tokens} tokens")
            messages = result.messages  # 使用裁剪后的消息
    """

    def __init__(
        self,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_password: str = "",
        threshold: float = THRESHOLD_RATIO,
        target: float = TARGET_RATIO,
    ):
        self.threshold = threshold
        self.target = target
        self.estimator = TokenEstimator()
        self.classifier = MessageClassifier()
        self._redis = None

        if HAS_REDIS:
            try:
                self._redis = redis.Redis(
                    host=redis_host, port=redis_port,
                    password=redis_password, decode_responses=True,
                )
                self._redis.ping()
            except Exception:
                self._redis = None

        self._stats: Dict = {
            "total_saved": 0,
            "total_clips": 0,
            "avg_saved_per_clip": 0,
            "last_clip_at": None,
            "per_agent": {},
        }
        self._load_stats()

    # ── Public API ──

    def check(
        self,
        messages: List[Dict],
        model: str = "deepseek-chat",
        current_task: str = "",
        agent_id: str = "",
    ) -> ClipResult:
        """检查是否需要裁剪，并执行裁剪。

        Args:
            messages: OpenAI 格式消息列表
            model: LLM 模型名称
            current_task: 当前任务描述
            agent_id: Agent ID（用于按Agent统计）

        Returns:
            ClipResult（.messages 是裁剪后的列表）
        """
        original_tokens = self.estimator.count_messages(messages)
        window = self._get_window(model)
        threshold_tokens = int(window * self.threshold)
        target_tokens = int(window * self.target)

        if original_tokens <= threshold_tokens:
            return ClipResult(
                original_tokens=original_tokens,
                final_tokens=original_tokens,
                saved_tokens=0,
                messages=messages,
                clipped=False,
            )

        # ── 执行裁剪 ──
        classified = self.classifier.classify(messages, current_task)

        # 找到每个优先级的最新 N 条
        key_decisions = [c for c in classified if c.priority == MessagePriority.KEY_DECISION]
        recent = [c for c in classified if c.priority <= MessagePriority.RECENT_ROUND]

        # 按轮次排序，保留最新 N 轮
        max_round = max((c.round_num for c in classified), default=0)
        keep_indices: set = set()

        # P0: 永远保留
        for c in classified:
            if c.priority == MessagePriority.CRITICAL:
                keep_indices.add(c.index)

        # P1: 保留最新 KEEP_KEY_DECISIONS 个关键决策
        key_decisions_sorted = sorted(key_decisions, key=lambda x: x.round_num, reverse=True)
        for c in key_decisions_sorted[:KEEP_KEY_DECISIONS]:
            keep_indices.add(c.index)

        # P2: 保留最新 KEEP_RECENT_ROUNDS 轮
        recent_sorted = sorted(recent, key=lambda x: x.round_num, reverse=True)
        for c in recent_sorted:
            if c.round_num > max_round - KEEP_RECENT_ROUNDS and c.priority != MessagePriority.CRITICAL:
                keep_indices.add(c.index)

        # P3 & P4: 丢弃
        clipped = []
        discarded_count = 0
        for c in classified:
            if c.index in keep_indices:
                clipped.append(c.msg)
            else:
                discarded_count += 1

        final_tokens = self.estimator.count_messages(clipped)
        saved = original_tokens - final_tokens

        # ── 如果还没降到 target，再从 P2 边界裁剪 ──
        while final_tokens > target_tokens and len(clipped) > 1:
            # 移除最旧的非 P0 消息
            for i, msg in enumerate(clipped):
                if msg.get("role") != "system":
                    clipped.pop(i)
                    discarded_count += 1
                    break
            else:
                break  # 只剩 system prompt
            final_tokens = self.estimator.count_messages(clipped)
            saved = original_tokens - final_tokens

        # ── 保存统计 ──
        self._save_clip(saved, discarded_count, agent_id)

        return ClipResult(
            original_tokens=original_tokens,
            final_tokens=final_tokens,
            saved_tokens=saved,
            messages=clipped,
            clipped=True,
            details={
                "discarded": discarded_count,
                "kept": len(clipped),
                "threshold": threshold_tokens,
                "window": window,
            },
        )

    def get_window_size(self, model: str) -> int:
        """获取模型窗口大小。"""
        return self._get_window(model)

    def get_stats(self, agent_id: str = "") -> Dict:
        """获取 Token 统计。"""
        if agent_id:
            return self._stats.get("per_agent", {}).get(agent_id, {})
        return dict(self._stats)

    # ── Private ──

    def _get_window(self, model: str) -> int:
        """获取模型窗口（支持模糊匹配）。"""
        if model in MODEL_WINDOWS:
            return MODEL_WINDOWS[model]
        for key in MODEL_WINDOWS:
            if key in model or model in key:
                return MODEL_WINDOWS[key]
        return DEFAULT_WINDOW

    def _save_clip(self, saved: int, discarded: int, agent_id: str):
        """保存裁剪统计到 Redis。"""
        self._stats["total_saved"] += saved
        self._stats["total_clips"] += 1
        self._stats["last_clip_at"] = datetime.now().isoformat()

        if self._stats["total_clips"] > 0:
            self._stats["avg_saved_per_clip"] = round(
                self._stats["total_saved"] / self._stats["total_clips"], 1
            )

        if agent_id:
            per = self._stats.setdefault("per_agent", {}).setdefault(agent_id, {
                "total_saved": 0, "total_clips": 0, "last_clip": None,
            })
            per["total_saved"] += saved
            per["total_clips"] += 1
            per["last_clip"] = datetime.now().isoformat()

        self._dump_stats()

    def _dump_stats(self):
        """持久化统计到 Redis。"""
        if self._redis:
            try:
                self._redis.set(
                    "commander:token:stats",
                    json.dumps(self._stats, ensure_ascii=False),
                )
            except Exception:
                pass

    def _load_stats(self):
        """从 Redis 加载统计。"""
        if self._redis:
            try:
                raw = self._redis.get("commander:token:stats")
                if raw:
                    self._stats = json.loads(raw)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# Commander 集成入口
# ═══════════════════════════════════════════════════════════════

class AgentTokenGuard:
    """
    Agent Token Guard — Commander 集成层。

    在每个 Agent 调用 LLM 前自动执行裁剪，对 Agent 透明。

    用法:
        guard = AgentTokenGuard()
        # 在 Agent 的 chat() 调用前:
        messages = guard.guard(messages, agent_id="商务经理", model="deepseek-chat")
        response = llm.chat(messages)
    """

    def __init__(self, controller: TokenBudgetController = None):
        self.controller = controller or TokenBudgetController()
        self._last_task: Dict[str, str] = {}  # agent_id → task

    def guard(
        self,
        messages: List[Dict],
        agent_id: str = "",
        model: str = "deepseek-chat",
        task: str = "",
    ) -> List[Dict]:
        """Agent 调用 LLM 前的守卫方法。

        Args:
            messages: 当前消息列表
            agent_id: Agent 标识
            model: 当前使用的模型
            task: 当前任务描述

        Returns:
            可能裁剪后的消息列表。
        """
        current_task = task or self._last_task.get(agent_id, "")
        result = self.controller.check(messages, model=model, current_task=current_task, agent_id=agent_id)

        if result.clipped:
            saved_k = round(result.saved_tokens / 1000, 1)
            total_saved_k = round(self.controller.get_stats()["total_saved"] / 1000, 1)
            print(f"[TokenGuard] {agent_id}: {result.original_tokens}→{result.final_tokens} "
                  f"tokens (-{saved_k}K), 累计节省 {total_saved_k}K tokens")

        if task:
            self._last_task[agent_id] = task

        return result.messages

    def set_task(self, agent_id: str, task: str):
        """设置当前任务（用于识别 P0 消息）。"""
        self._last_task[agent_id] = task


# ═══════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 构造模拟对话
    messages = [
        {"role": "system", "content": "你是LightingMetal商务经理，负责接待客户。"},
        {"role": "user", "content": "客户: 我需要光伏支架报价", "timestamp": time.time() - 3600},
        {"role": "assistant", "content": "好的，请问需要哪种规格？", "timestamp": time.time() - 3500},
        {"role": "user", "content": "客户: 热镀锌方案，ISO 1461标准", "timestamp": time.time() - 3000},
        {"role": "assistant", "content": "确认方案：热镀锌 ISO 1461，decision: approved", "timestamp": time.time() - 2900},
    ]

    # 填充大量历史消息模拟超限
    for i in range(500):
        messages.append({
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"历史消息 {i}: " + "技术参数详细说明 " * 20,
            "timestamp": time.time() - 100000 + i * 100,
        })

    # 当前任务
    current_task = "沙特50MW光伏电站螺旋地桩防腐方案报价"

    ctrl = TokenBudgetController()
    result = ctrl.check(messages, model="deepseek-chat", current_task=current_task, agent_id="商务经理")

    print(f"原始: {result.original_tokens} tokens")
    print(f"裁剪: {result.final_tokens} tokens")
    print(f"节省: {result.saved_tokens} tokens ({round(result.saved_tokens/result.original_tokens*100, 1)}%)")
    print(f"保留: {result.details.get('kept')} 条, 丢弃: {result.details.get('discarded')} 条")
    print(f"统计: {json.dumps(ctrl.get_stats(), ensure_ascii=False, indent=2)}")
