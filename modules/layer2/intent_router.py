"""
SemanticIntentRouter — 基于能力卡片的语义意图路由
====================================================
替代 workflow_engine.INTENT_TOOL_MAP 的 19 条硬编码。
零行业关键词。完全从 Agent 的能力卡片推导意图→Agent 匹配。

核心流程:
  1. 向量搜索匹配 Agent 能力卡片（语义，非关键词）
  2. 多信号融合置信度（向量相似度 + 角色匹配 + Schema 匹配 + Skill 匹配）
  3. 无匹配时建议 L5 新建 Agent 类型

置信度信号:
  - vector_similarity (40%): Chroma 向量相似度
  - role_match (20%): 角色名与任务的词重叠
  - schema_match (25%): input_schema 字段在任务描述中出现
  - skill_match (15%): Skill 名称与任务关键词匹配
"""
import json
import time
from typing import List, Dict, Optional


class SemanticIntentRouter:
    """语义意图路由器 — 零硬编码"""

    def __init__(self, vector_store, redis_client):
        """
        Args:
            vector_store: ChromaVectorStore 或 MemVectorStore 实例
            redis_client: RedisClient 实例
        """
        self.vs = vector_store
        self.redis = redis_client
        self._card_cache: Dict[str, dict] = {}
        self._card_ttl = 300  # 5 分钟缓存
        self._last_cache_time: float = 0

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    def route(self, task_description: str) -> dict:
        """
        语义路由：输入任务描述 → 输出最佳 Agent 匹配

        Args:
            task_description: 自然语言任务描述

        Returns:
            {
                "primary_agent": "翻译官",
                "confidence": 0.92,
                "candidate_agents": [
                    {"name": "翻译官", "confidence": 0.92, "why": "..."},
                ],
                "suggested_subtasks": [
                    {"phase": "prepare", "description": "...", "agent": "翻译官"},
                ],
                "matched_via": "semantic+card",
                "suggestion": null
            }
        """
        self._refresh_card_cache()

        # Step 1: 向量搜索匹配 Agent
        candidates = self._vector_search(task_description)

        # Step 2: 多信号融合置信度
        matched = []
        for c in candidates:
            card = self._load_card(c["agent_name"])
            if not card:
                continue
            confidence = self._compute_confidence(
                task_description, card, c.get("score", 0.5)
            )
            if confidence > 0.35:
                matched.append({
                    "name": card["name"],
                    "confidence": round(confidence, 3),
                    "why": self._explain_match(task_description, card, confidence),
                })

        # Step 3: 无匹配 → 建议新建 Agent
        if not matched:
            return self._suggest_new_agent(task_description)

        matched.sort(key=lambda x: -x["confidence"])
        best = matched[0]

        # Step 4: 推导子任务结构
        suggested_subtasks = self._infer_subtask_structure(
            task_description, self._load_card(best["name"])
        )

        return {
            "primary_agent": best["name"],
            "confidence": best["confidence"],
            "candidate_agents": matched[:5],
            "suggested_subtasks": suggested_subtasks,
            "matched_via": "semantic+card",
            "suggestion": None,
        }

    # ═══════════════════════════════════════════════
    # 向量搜索
    # ═══════════════════════════════════════════════

    def _vector_search(self, query: str) -> List[dict]:
        """向量搜索 Agent 能力卡片"""
        try:
            results = self.vs.search(f"agent:task_match:{query[:500]}", top_k=8)
            parsed = []
            for r in results:
                meta = r.get("meta", r.get("metadata", {}))
                if meta.get("type") == "agent_card":
                    parsed.append({
                        "agent_name": meta.get("name", r.get("key", "")),
                        "score": r.get("score", 0.5),
                    })
            return parsed
        except Exception:
            return self._keyword_fallback(query)

    def _keyword_fallback(self, query: str) -> List[dict]:
        """向量搜索失败时的关键词降级"""
        results = []
        all_cards = self._list_all_cards()
        query_lower = query.lower()
        for agent_name, card in all_cards.items():
            score = 0.0
            role = card.get("role", "").lower()
            desc = card.get("description", card.get("system_prompt", "")).lower()
            # 角色名中的双字词命中
            for word in role.split():
                if len(word) >= 2 and word in query_lower:
                    score += 0.2
            # 描述中的词命中
            for word in query_lower.split():
                if len(word) >= 2 and word in desc[:500]:
                    score += 0.1
            if score > 0:
                results.append({"agent_name": agent_name, "score": min(score, 1.0)})
        results.sort(key=lambda x: -x["score"])
        return results[:8]

    # ═══════════════════════════════════════════════
    # 置信度计算
    # ═══════════════════════════════════════════════

    def _compute_confidence(self, task: str, card: dict,
                            vector_score: float) -> float:
        """多信号融合置信度计算"""
        signals = {
            "vector_similarity": vector_score * 0.4,
            "role_match": self._role_match(task, card) * 0.2,
            "schema_match": self._schema_match(task, card) * 0.25,
            "skill_match": self._skill_match(task, card) * 0.15,
        }
        return round(sum(signals.values()), 3)

    def _role_match(self, task: str, card: dict) -> float:
        """角色名与任务描述的关键词重叠度"""
        role = card.get("role", card.get("name", "")).lower()
        task_lower = task.lower()
        role_words = set(w for w in role.split() if len(w) >= 2)
        if not role_words:
            return 0.3
        task_words = set(task_lower.split())
        overlap = len(role_words & task_words)
        return min(1.0, overlap / len(role_words))

    def _schema_match(self, task: str, card: dict) -> float:
        """能力卡片 input_schema 字段名在任务描述中的出现率"""
        input_props = card.get("input_schema", {}).get("properties", {})
        if not input_props:
            return 0.5
        task_lower = task.lower()
        matched = 0.0
        for prop_name, prop_def in input_props.items():
            if prop_name in task_lower:
                matched += 1.0
            elif isinstance(prop_def, dict):
                desc = prop_def.get("description", "").lower()
                if desc and any(w in task_lower for w in desc.split() if len(w) >= 2):
                    matched += 0.5
        return min(1.0, matched / max(len(input_props), 1))

    def _skill_match(self, task: str, card: dict) -> float:
        """Skill 名称在任务描述中的触发率"""
        skills = card.get("skills", [])
        if not skills:
            return 0.5
        task_lower = task.lower()
        hit = 0
        for skill in skills:
            skill_text = skill.replace("-", " ").replace("_", " ")
            if any(w in task_lower for w in skill_text.split() if len(w) >= 2):
                hit += 1
        return min(1.0, hit / max(len(skills), 1))

    # ═══════════════════════════════════════════════
    # 子任务推导
    # ═══════════════════════════════════════════════

    def _infer_subtask_structure(self, task: str, card: dict) -> list:
        """从能力卡片推导建议的子任务结构"""
        workflow = card.get("standard_workflow", [])
        if workflow:
            return workflow

        input_fields = list(card.get("input_schema", {}).get("properties", {}).keys())
        output_fields = list(card.get("output_schema", {}).get("properties", {}).keys())
        role = card.get("role", card.get("name", "Agent"))
        agent_name = card.get("name", "通用Agent")

        return [
            {
                "phase": "prepare",
                "description": f"准备输入数据，确保包含 {', '.join(input_fields[:3]) if input_fields else '必要信息'}",
                "agent": agent_name,
            },
            {
                "phase": "execute",
                "description": f"使用 {role} 执行核心任务",
                "agent": agent_name,
            },
            {
                "phase": "verify",
                "description": f"验证输出符合 {', '.join(output_fields[:3]) if output_fields else '质量规范'}",
                "agent": "审计官",
            },
        ]

    # ═══════════════════════════════════════════════
    # 无匹配处理
    # ═══════════════════════════════════════════════

    def _suggest_new_agent(self, task_description: str) -> dict:
        """当没有任何 Agent 能处理此任务时"""
        return {
            "primary_agent": None,
            "confidence": 0.0,
            "candidate_agents": [],
            "suggested_subtasks": [],
            "matched_via": "none",
            "suggestion": {
                "action": "create_new_agent",
                "reason": f"No existing agent matches: '{task_description[:100]}'",
                "proposed_capabilities": self._extract_capabilities(task_description),
                "message": "L5 will analyze and suggest new agent type",
            },
        }

    def _extract_capabilities(self, task: str) -> list:
        """从任务描述提取可能需要的能力（简单启发式）"""
        caps = []
        patterns = {
            "translate": ["翻译", "translate", "多语言", "multilingual"],
            "audit": ["审计", "audit", "检查", "review", "合规"],
            "code": ["代码", "code", "开发", "build", "deploy"],
            "content": ["内容", "content", "文案", "写作", "生成"],
            "design": ["设计", "design", "UI", "UX", "界面"],
            "search": ["搜索", "search", "查询", "检索"],
            "data": ["数据", "data", "分析", "analytics"],
        }
        task_lower = task.lower()
        for cap, keywords in patterns.items():
            if any(kw in task_lower for kw in keywords):
                caps.append(cap)
        return caps if caps else ["general"]

    # ═══════════════════════════════════════════════
    # 解释性输出
    # ═══════════════════════════════════════════════

    def _explain_match(self, task: str, card: dict, confidence: float) -> str:
        """生成人类可读的匹配解释"""
        reasons = []
        role = card.get("role", card.get("name", ""))
        if role and any(w in task.lower() for w in role.split() if len(w) >= 2):
            reasons.append(f"角色'{role}'与任务直接相关")
        skills = card.get("skills", [])
        matched_skills = [
            s for s in skills
            if any(w in task.lower() for w in s.replace("-", " ").split() if len(w) >= 2)
        ]
        if matched_skills:
            reasons.append(f"技能匹配: {', '.join(matched_skills[:2])}")
        if confidence > 0.8:
            reasons.insert(0, "高置信度语义匹配")
        return "; ".join(reasons) if reasons else f"语义相似度 {confidence:.2f}"

    # ═══════════════════════════════════════════════
    # 缓存管理
    # ═══════════════════════════════════════════════

    def _refresh_card_cache(self):
        """刷新能力卡片缓存"""
        now = time.time()
        if now - self._last_cache_time < self._card_ttl and self._card_cache:
            return
        try:
            agent_list = self.redis.smembers("agent:registry") or []
            for agent_name in agent_list:
                card_raw = self.redis.get(f"agent:card:{agent_name}")
                if card_raw:
                    self._card_cache[agent_name] = json.loads(card_raw)
        except Exception:
            pass
        self._last_cache_time = now

    def _load_card(self, agent_name: str) -> Optional[dict]:
        """加载单个 Agent 的能力卡片"""
        if agent_name in self._card_cache:
            return self._card_cache[agent_name]
        try:
            card_raw = self.redis.get(f"agent:card:{agent_name}")
            if card_raw:
                card = json.loads(card_raw)
                self._card_cache[agent_name] = card
                return card
        except Exception:
            pass
        return None

    def _list_all_cards(self) -> Dict[str, dict]:
        """列出所有已注册的能力卡片"""
        self._refresh_card_cache()
        return dict(self._card_cache)

    # ═══════════════════════════════════════════════
    # 向量索引维护
    # ═══════════════════════════════════════════════

    def index_agent_card(self, agent_name: str, card: dict):
        """将能力卡片加入向量索引"""
        if not self.vs:
            return
        index_text = (
            f"Agent: {card.get('name', agent_name)}\n"
            f"Role: {card.get('role', '')}\n"
            f"Description: {card.get('system_prompt', card.get('description', ''))[:500]}\n"
            f"Skills: {', '.join(card.get('skills', []))}\n"
            f"Input fields: {', '.join(card.get('input_schema', {}).get('properties', {}).keys())}\n"
            f"Output fields: {', '.join(card.get('output_schema', {}).get('properties', {}).keys())}"
        )
        self.vs.add(
            f"agent_card:{agent_name}",
            index_text,
            {"type": "agent_card", "name": agent_name},
        )

    def index_all_cards(self) -> int:
        """索引所有已注册的能力卡片"""
        self._refresh_card_cache()
        count = 0
        for agent_name, card in self._card_cache.items():
            self.index_agent_card(agent_name, card)
            count += 1
        return count
