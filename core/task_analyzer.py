#!/usr/bin/env python3
"""
优化一：智能任务拆分与去重 — TaskAnalyzer
============================================
计算任务指纹（MD5+关键词），在 Redis 中查重，避免重复劳动。
支持模糊匹配 + 精确指纹双重校验。

Constitution: 所有写入使用 commander:* 前缀，不触碰 page:* / agent:* / lightingmetal:*。
"""

import hashlib
import json
import time
from typing import Optional

import redis

# ── 可替换为外部 NLP 分词器 ──
try:
    import jieba
    HAS_JIEBA = True
except ImportError:
    HAS_JIEBA = False


class TaskAnalyzer:
    """任务去重引擎：关键词提取 → MD5指纹 → Redis查重 → 复用历史摘要"""

    # 中文停用词
    STOPWORDS = {
        "的", "了", "是", "我", "你", "他", "她", "它", "们", "这", "那",
        "吗", "呢", "吧", "啊", "和", "与", "及", "或", "在", "有",
        "被", "把", "从", "到", "对", "为", "让", "给", "向", "以",
        "可以", "需要", "应该", "能够", "可能", "已经", "还要", "不是",
        "一个", "一些", "这个", "那个", "什么", "怎么", "哪", "谁",
    }

    def __init__(self, redis_host: str = "127.0.0.1", redis_port: int = 6379,
                 redis_password: str = "Lt@114514!"):
        self.redis = redis.Redis(
            host=redis_host, port=redis_port,
            password=redis_password, decode_responses=True,
        )

    # ── 关键词提取 ──────────────────────────────────────────

    def _extract_keywords(self, text: str) -> list:
        """提取核心关键词。优先用 jieba，fallback 到简单清洗+切分。"""
        if HAS_JIEBA:
            words = [w.strip() for w in jieba.cut(text) if len(w.strip()) > 1]
        else:
            # 简单中文/英文清洗
            cleaned = (text
                       .replace("，", " ").replace("。", " ").replace("？", " ")
                       .replace("！", " ").replace(",", " ").replace(".", " ")
                       .replace("、", " ").replace("；", " ").replace("：", " "))
            words = [w.strip() for w in cleaned.split() if len(w.strip()) > 1]

        # 去停用词 + 去重保持顺序
        seen = set()
        result = []
        for w in words:
            if w.lower() not in self.STOPWORDS and w.lower() not in seen:
                seen.add(w.lower())
                result.append(w)
        return result[:15]  # 最多15个关键词，平衡精度与性能

    # ── 指纹计算 ─────────────────────────────────────────────

    def compute_fingerprint(self, task_description: str) -> str:
        """计算任务指纹：关键词排序后 MD5。"""
        keywords = self._extract_keywords(task_description)
        canonical = json.dumps(sorted(keywords), ensure_ascii=False)
        return hashlib.md5(canonical.encode("utf-8")).hexdigest()

    # ── 查重 ─────────────────────────────────────────────────

    def check_duplicate(self, task_description: str,
                        expire_hours: int = 24) -> dict:
        """检查是否与近期任务重复。

        Returns:
            {"is_duplicate": True, "original_task_id": ..., "summary": {...}}
            {"is_duplicate": False}
        """
        fingerprint = self.compute_fingerprint(task_description)

        # 精确指纹匹配
        task_id = self.redis.get(f"commander:task:fingerprint:{fingerprint}")
        if task_id:
            # 验证历史任务仍在有效期
            task_time = float(
                self.redis.hget(f"commander:task:memory:{task_id}", "timestamp") or 0
            )
            if time.time() - task_time < expire_hours * 3600:
                summary_raw = self.redis.hget(f"commander:task:memory:{task_id}", "summary")
                summary = json.loads(summary_raw) if summary_raw else {}
                return {
                    "is_duplicate": True,
                    "original_task_id": task_id,
                    "summary": summary,
                }

        # 模糊匹配：按部分关键词查（降低漏网率）
        keywords = self._extract_keywords(task_description)
        if len(keywords) >= 3:
            candidate_ids = set()
            for kw in keywords[:5]:
                ids = self.redis.smembers(f"commander:task:keyword:{kw}")
                candidate_ids.update(ids)
            if candidate_ids:
                for cid in candidate_ids:
                    raw = self.redis.hget(f"commander:task:memory:{cid}", "keywords")
                    if raw:
                        hist_kw = set(json.loads(raw))
                        overlap = len(set(keywords) & hist_kw)
                        if overlap >= max(3, len(hist_kw) * 0.7):
                            task_time = float(
                                self.redis.hget(f"commander:task:memory:{cid}", "timestamp") or 0
                            )
                            if time.time() - task_time < expire_hours * 3600:
                                summary_raw = self.redis.hget(
                                    f"commander:task:memory:{cid}", "summary"
                                )
                                summary = json.loads(summary_raw) if summary_raw else {}
                                return {
                                    "is_duplicate": True,
                                    "original_task_id": cid,
                                    "summary": summary,
                                    "match_type": "fuzzy",
                                }

        return {"is_duplicate": False}

    # ── 缓存 ─────────────────────────────────────────────────

    def cache_task(self, task_id: str, task_description: str,
                   summary: Optional[dict] = None):
        """将新任务写入 Redis 缓存（7天TTL），供后续查重。"""
        fingerprint = self.compute_fingerprint(task_description)
        keywords = self._extract_keywords(task_description)
        now = time.time()

        # 指纹 → task_id 映射（7天）
        self.redis.setex(
            f"commander:task:fingerprint:{fingerprint}",
            86400 * 7,
            task_id,
        )

        # 关键词倒排索引（用于模糊查重）
        pipe = self.redis.pipeline()
        for kw in keywords[:10]:
            pipe.sadd(f"commander:task:keyword:{kw}", task_id)
            pipe.expire(f"commander:task:keyword:{kw}", 86400 * 7)
        pipe.execute()

        # 任务记忆（24小时，与 check_duplicate 默认值一致）
        task_memory = {
            "timestamp": str(now),
            "description": task_description,
            "fingerprint": fingerprint,
            "keywords": json.dumps(keywords, ensure_ascii=False),
            "summary": json.dumps(summary or {}, ensure_ascii=False),
        }
        self.redis.hset(f"commander:task:memory:{task_id}", mapping=task_memory)
        self.redis.expire(f"commander:task:memory:{task_id}", 86400)

    # ── 任务拆分（关键词驱动的启发式拆分）────────────────────

    def suggest_split(self, task_description: str) -> list:
        """根据任务描述建议拆分粒度。

        拆分为不影响并行度的最小独立单元。
        """
        keywords = self._extract_keywords(task_description)
        text = task_description.lower()

        subtasks = []

        # 按关键词模式匹配
        if any(w in text for w in ["翻译", "translate", "translation"]):
            subtasks.append({"type": "translate", "agent_type": "翻译官",
                             "priority": 1, "note": "内容翻译"})
        if any(w in text for w in ["审计", "检查", "audit", "review"]):
            subtasks.append({"type": "audit", "agent_type": "审计官",
                             "priority": 1, "note": "质量审计"})
        if any(w in text for w in ["报价", "报价单", "quote", "pricing"]):
            subtasks.append({"type": "quote", "agent_type": "售前经理",
                             "priority": 2, "note": "生成报价方案"})
        if any(w in text for w in ["客户", "询盘", "inquiry", "需求"]):
            subtasks.append({"type": "inquiry", "agent_type": "商务经理",
                             "priority": 1, "note": "客户需求分析"})

        # 无匹配：通用任务
        if not subtasks:
            subtasks.append({"type": "general", "agent_type": "通用Agent",
                             "priority": 3, "note": task_description[:60]})

        return subtasks
