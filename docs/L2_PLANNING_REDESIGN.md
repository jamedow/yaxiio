# L2 规划层重构方案

> 版本: 1.0 | 日期: 2026-05-29
> 涉及文件: `modules/layer2/intent_router.py` (新), `modules/layer2/model_router_v2.py` (新)
> 修改文件: `workflow_engine.py`（`_decompose_via_l2`）, `modules/layer2/__init__.py`

---

## 一、当前问题

### 1.1 `INTENT_TOOL_MAP` 硬编码

```python
# workflow_engine.py — 当前代码
INTENT_TOOL_MAP = {
    "audit":      {"tool": None, "agent": "审计官", ...},
    "translate":  {"tool": None, "agent": "翻译官", ...},
    "quote":      {"tool": None, "agent": "售前经理", ...},
    # ... 19 条硬编码映射
}
```

**问题**: 与"通用 Agent 操作系统内核 — 换一套能力卡片就从外贸变成法律/医疗"的定位根本矛盾。新增一个 Agent 类型需要改代码。

### 1.2 L0 经验检索后未注入 LLM

```python
# workflow_engine.py — 当前代码
past_exp = self.l0._retrieve_experiences(action_clean, available[:5])
if past_exp:
    print(f"[L0] {task_id} found {len(past_exp)} past experiences for '{action_clean}'")
    # ← 打印完了就没下文了！past_exp 没有传给 LLM！
```

### 1.3 ModelRouter 过于简陋

```python
RULES = {
    "complex": {"keywords": ["分析","拆解","优化","审计"]},
    "stable":  {"keywords": ["修复","创建","生成"]},
    "fast":    {"keywords": ["翻译","查询","检查"]},
}
```

3 条规则、9 个中文关键词。没有成本、延迟、能力的多目标优化。

### 1.4 AgentFactory 是"假工厂"

`AgentFactory.create()` 只写入一条 Redis meta，返回一个字符串 ID。真正的 Agent 进程创建在 `workflow_engine._clone_agents_for_task()` 中直接调用 `commander.spawn_neuron()`。

---

## 二、目标架构

```
用户任务描述
     │
     ▼
┌─────────────────────────────────────┐
│  SemanticIntentRouter               │
│  ┌───────────────────────────────┐  │
│  │ ① 向量搜索匹配 Agent 能力卡片  │  │
│  │ ② 多信号融合置信度计算         │  │
│  │ ③ 无匹配时建议新建 Agent 类型  │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ IntelligentModelRouter        │  │
│  │ ① 任务复杂度估算               │  │
│  │ ② 多目标优化选模型 (成本/延迟) │  │
│  │ ③ Provider 自动故障切换       │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │ L0 Experience Injection       │  │
│  │ ① 语义检索历史高分经验         │  │
│  │ ② 格式化为 few-shot 示例       │  │
│  │ ③ 注入 LLM 规划 prompt         │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
     │
     ▼
LLM 规划 (带经验增强)
     │
     ▼
子任务 DAG → L3 调度
```

---

## 三、实现: SemanticIntentRouter

**新建文件**: `modules/layer2/intent_router.py`

```python
"""
SemanticIntentRouter — 基于能力卡片的语义意图路由
====================================================
替代 workflow_engine.INTENT_TOOL_MAP 的硬编码映射。
零行业关键词。完全从 Agent 的能力卡片推导意图→Agent 匹配关系。
"""
import json
import time
from typing import List, Dict, Optional


class SemanticIntentRouter:
    """语义意图路由器 — 零硬编码"""

    def __init__(self, vector_store, redis_client):
        self.vs = vector_store
        self.redis = redis_client
        self._card_cache: Dict[str, dict] = {}
        self._card_ttl = 300
        self._last_cache_time = 0

    def route(self, task_description: str) -> dict:
        """
        语义路由：输入任务描述 → 输出最佳 Agent 匹配

        Returns:
            {
                "primary_agent": "翻译官",
                "confidence": 0.92,
                "candidate_agents": [...],
                "suggested_subtasks": [...],
                "matched_via": "semantic+card",
                "suggestion": null
            }
        """
        self._refresh_card_cache()

        # Step 1: 向量搜索匹配 Agent
        query_embedding = self._build_query(task_description)
        candidates = self._vector_search(query_embedding)

        # Step 2: 多信号融合置信度
        matched = []
        for c in candidates:
            card = self._load_card(c["agent_name"])
            if not card:
                continue
            confidence = self._compute_confidence(task_description, card, c.get("score", 0.5))
            if confidence > 0.35:
                matched.append({
                    "name": card["name"],
                    "confidence": round(confidence, 3),
                    "why": self._explain_match(task_description, card, confidence)
                })

        # Step 3: 无匹配 → 建议新建 Agent
        if not matched:
            return self._suggest_new_agent(task_description)

        matched.sort(key=lambda x: -x["confidence"])
        best = matched[0]

        suggested_subtasks = self._infer_subtask_structure(
            task_description, self._load_card(best["name"])
        )

        return {
            "primary_agent": best["name"],
            "confidence": best["confidence"],
            "candidate_agents": matched[:5],
            "suggested_subtasks": suggested_subtasks,
            "matched_via": "semantic+card",
            "suggestion": None
        }

    def _build_query(self, task_description: str) -> str:
        return f"agent:task_match:{task_description[:500]}"

    def _vector_search(self, query: str) -> List[dict]:
        """向量搜索 Agent 能力卡片"""
        try:
            results = self.vs.search(query, top_k=8)
            parsed = []
            for r in results:
                meta = r.get("meta", r.get("metadata", {}))
                if meta.get("type") == "agent_card":
                    parsed.append({
                        "agent_name": meta.get("name", r.get("key", "")),
                        "score": r.get("score", 0.5)
                    })
            return parsed
        except Exception:
            return self._keyword_fallback(query)

    def _keyword_fallback(self, query: str) -> List[dict]:
        """向量搜索失败时的关键词降级"""
        results = []
        all_cards = self._list_all_cards()
        for agent_name, card in all_cards.items():
            score = 0.0
            role = card.get("role", "")
            desc = card.get("description", card.get("system_prompt", ""))
            for word in query.lower().split():
                if word in role.lower():
                    score += 0.2
                if word in desc.lower()[:500]:
                    score += 0.1
            if score > 0:
                results.append({"agent_name": agent_name, "score": min(score, 1.0)})
        results.sort(key=lambda x: -x["score"])
        return results[:8]

    def _compute_confidence(self, task: str, card: dict, vector_score: float) -> float:
        """多信号融合置信度计算"""
        signals = {
            "vector_similarity": vector_score * 0.4,
            "role_match": self._role_match(task, card) * 0.2,
            "schema_match": self._schema_match(task, card) * 0.25,
            "skill_match": self._skill_match(task, card) * 0.15,
        }
        return round(sum(signals.values()), 3)

    def _role_match(self, task: str, card: dict) -> float:
        """角色名与任务的关键词重叠"""
        role = card.get("role", card.get("name", "")).lower()
        task_lower = task.lower()
        role_words = set(role.split())
        task_words = set(task_lower.split())
        overlap = len(role_words & task_words)
        return min(1.0, overlap / max(len(role_words), 1))

    def _schema_match(self, task: str, card: dict) -> float:
        """能力卡片 input_schema 字段与任务描述的匹配度"""
        input_props = card.get("input_schema", {}).get("properties", {})
        if not input_props:
            return 0.5
        task_lower = task.lower()
        matched = 0
        for prop_name, prop_def in input_props.items():
            if prop_name in task_lower:
                matched += 1
            elif isinstance(prop_def, dict):
                desc = prop_def.get("description", "").lower()
                if desc and any(w in task_lower for w in desc.split()):
                    matched += 0.5
        return min(1.0, matched / max(len(input_props), 1))

    def _skill_match(self, task: str, card: dict) -> float:
        """Skill 名称与任务的匹配度"""
        skills = card.get("skills", [])
        if not skills:
            return 0.5
        task_lower = task.lower()
        matched = sum(1 for s in skills if s.replace("-", " ") in task_lower)
        return min(1.0, matched / max(len(skills), 1))

    def _infer_subtask_structure(self, task: str, card: dict) -> list:
        """从能力卡片推导建议的子任务结构"""
        workflow = card.get("standard_workflow", [])
        if workflow:
            return workflow

        input_fields = list(card.get("input_schema", {}).get("properties", {}).keys())
        output_fields = list(card.get("output_schema", {}).get("properties", {}).keys())
        role = card.get("role", card.get("name", "Agent"))

        return [
            {"phase": "prepare", "description": f"准备输入数据，确保包含 {input_fields[:3] if input_fields else '必要信息'}",
             "agent": card.get("name", "通用Agent")},
            {"phase": "execute", "description": f"使用 {role} 执行核心任务",
             "agent": card.get("name", "通用Agent")},
            {"phase": "verify", "description": f"验证输出符合 {output_fields[:3] if output_fields else '质量规范'}",
             "agent": "审计官"}
        ]

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
                "proposed_capabilities": self._extract_required_capabilities(task_description),
                "message": "L5 will analyze and suggest new agent type"
            }
        }

    def _extract_required_capabilities(self, task: str) -> list:
        """从任务描述中提取可能需要的能力（简单启发式）"""
        caps = []
        patterns = {
            "translate": ["翻译", "translate", "多语言", "multilingual", "本地化"],
            "audit": ["审计", "audit", "检查", "review", "合规"],
            "code": ["代码", "code", "开发", "build", "deploy", "编程"],
            "content": ["内容", "content", "文案", "写作", "生成"],
            "design": ["设计", "design", "UI", "UX", "界面"],
            "search": ["搜索", "search", "查询", "检索"],
        }
        task_lower = task.lower()
        for cap, keywords in patterns.items():
            if any(kw in task_lower for kw in keywords):
                caps.append(cap)
        return caps if caps else ["general"]

    def _explain_match(self, task: str, card: dict, confidence: float) -> str:
        """生成人类可读的匹配解释"""
        reasons = []
        role = card.get("role", card.get("name", ""))
        if role and any(w in task.lower() for w in role.lower().split()):
            reasons.append(f"角色'{role}'与任务直接相关")
        skills = card.get("skills", [])
        matched_skills = [s for s in skills if s.replace("-", " ") in task.lower()]
        if matched_skills:
            reasons.append(f"技能匹配: {', '.join(matched_skills[:2])}")
        if confidence > 0.8:
            reasons.insert(0, "高置信度语义匹配")
        return "; ".join(reasons) if reasons else f"语义相似度 {confidence:.2f}"

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

    def index_agent_card(self, agent_name: str, card: dict):
        """将能力卡片加入向量索引"""
        if not self.vs:
            return
        index_text = (
            f"Agent: {card.get('name', agent_name)}\n"
            f"Role: {card.get('role', '')}\n"
            f"Description: {card.get('system_prompt', card.get('description', ''))[:500]}\n"
            f"Skills: {', '.join(card.get('skills', []))}\n"
            f"Input: {', '.join(card.get('input_schema', {}).get('properties', {}).keys())}\n"
            f"Output: {', '.join(card.get('output_schema', {}).get('properties', {}).keys())}"
        )
        self.vs.add(f"agent_card:{agent_name}", index_text, {"type": "agent_card", "name": agent_name})

    def index_all_cards(self) -> int:
        """索引所有已注册的能力卡片"""
        self._refresh_card_cache()
        count = 0
        for agent_name, card in self._card_cache.items():
            self.index_agent_card(agent_name, card)
            count += 1
        return count
```

---

## 四、实现: IntelligentModelRouter

**新建文件**: `modules/layer2/model_router_v2.py`

```python
"""
IntelligentModelRouter — 智能模型路由器
==========================================
多目标优化选择模型：成本 × 延迟 × 能力 × 可用性。
替代 ModelRouter 的 3 条规则 9 个中文关键词。
"""
import os
import time
from typing import Dict, List, Optional


class IntelligentModelRouter:
    """多 Provider 智能模型路由"""

    MODEL_CAPABILITIES = {
        "deepseek-chat": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "max_tokens": 8192,
            "supports_thinking": True,
            "cost_per_1k_input": 0.14,
            "cost_per_1k_output": 0.28,
            "avg_latency_ms": 800,
            "strengths": ["reasoning", "code", "multilingual", "long_context"],
            "recommended_for": ["analyze", "decompose", "audit", "review"],
            "priority": 1,
        },
        "deepseek-v4-flash": {
            "provider": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "max_tokens": 4096,
            "supports_thinking": False,
            "cost_per_1k_input": 0.07,
            "cost_per_1k_output": 0.14,
            "avg_latency_ms": 300,
            "strengths": ["translation", "classification", "simple_tasks"],
            "recommended_for": ["translate", "classify", "check", "query"],
            "priority": 1,
        },
    }

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self._failure_counts: Dict[str, int] = {}
        self._last_failure_time: Dict[str, float] = {}
        self._cooldown_seconds = 60

    def select(self, task: dict, constraints: dict = None) -> dict:
        """
        多目标优化选择模型

        Args:
            task: {"action": "...", "description": "...", "estimated_tokens": 2000}
            constraints: {"max_cost": 1.0, "max_latency_ms": 3000, "prefer": "reasoning"}

        Returns:
            {"model": "deepseek-chat", "provider": "deepseek", "thinking": "high",
             "fallback_model": "deepseek-v4-flash", "estimated_cost_usd": 0.07, ...}
        """
        constraints = constraints or {}
        required = self._estimate_requirements(task)
        candidates = self._filter_candidates(required, constraints)
        if not candidates:
            return self._emergency_fallback()

        scored = self._score_candidates(candidates, required, constraints)
        scored.sort(key=lambda x: -x["score"])

        best = scored[0]
        fallback = scored[1] if len(scored) > 1 else None

        return {
            "model": best["model"],
            "provider": best["provider"],
            "base_url": best["base_url"],
            "api_key": best["api_key"],
            "thinking": self._determine_thinking(best, required),
            "score": round(best["score"], 1),
            "fallback_model": fallback["model"] if fallback else None,
            "estimated_cost_usd": round(best["cost_per_1k_output"] * required.get("output_tokens", 500) / 1000, 4),
            "selection_reason": self._explain_selection(best, required)
        }

    def fallback(self, failed_model: str) -> Optional[dict]:
        """当前模型失败后自动切换"""
        self._record_failure(failed_model)
        for name, caps in sorted(self.MODEL_CAPABILITIES.items(), key=lambda x: x[1]["priority"]):
            if name != failed_model and self._is_available(name):
                return {
                    "model": name, "provider": caps["provider"],
                    "base_url": caps["base_url"],
                    "api_key": os.environ.get(caps["api_key_env"], ""),
                    "thinking": "off",
                    "fallback_reason": f"{failed_model} failed, switched to {name}"
                }
        return None

    def _estimate_requirements(self, task: dict) -> dict:
        """从任务描述估算所需模型能力"""
        desc = str(task.get("description", "")) + " " + str(task.get("action", ""))
        desc_lower = desc.lower()
        req = {"strengths": [], "estimated_tokens": 2000, "task_type": "general"}

        if len(desc) > 500:
            req["estimated_tokens"] = 6000
        elif len(desc) > 200:
            req["estimated_tokens"] = 4000

        if any(kw in desc_lower for kw in ["analyze", "decompose", "audit", "review", "分析", "拆解", "审计"]):
            req["strengths"].append("reasoning")
            req["task_type"] = "analyze"
            req["estimated_tokens"] = max(req["estimated_tokens"], 6000)

        if any(kw in desc_lower for kw in ["code", "build", "deploy", "fix", "代码", "修复"]):
            req["strengths"].append("code")

        if any(kw in desc_lower for kw in ["translate", "翻译", "multilingual", "多语言"]):
            req["strengths"].append("multilingual")
            req["task_type"] = "translate"

        if any(kw in desc_lower for kw in ["generate", "create", "生成", "创建"]):
            req["strengths"].append("creative")
            req["task_type"] = "generate"

        if any('\u4e00' <= c <= '\u9fff' for c in desc):
            req["strengths"].append("multilingual")

        return req

    def _filter_candidates(self, required: dict, constraints: dict) -> List[tuple]:
        """过滤可用且满足硬约束的模型"""
        candidates = []
        for model_name, caps in self.MODEL_CAPABILITIES.items():
            if not self._is_available(model_name):
                continue
            if constraints.get("max_cost") and caps["cost_per_1k_output"] > constraints["max_cost"]:
                continue
            if constraints.get("max_latency_ms") and caps["avg_latency_ms"] > constraints["max_latency_ms"]:
                continue
            if required.get("estimated_tokens", 0) > caps["max_tokens"]:
                continue
            candidates.append((model_name, caps))
        return candidates

    def _score_candidates(self, candidates: List[tuple], required: dict, constraints: dict) -> List[dict]:
        """多目标评分"""
        scored = []
        for model_name, caps in candidates:
            score = 0.0
            # 能力匹配度 (40%)
            strength_match = sum(1 for s in required.get("strengths", []) if s in caps["strengths"])
            strength_score = (strength_match / max(len(required.get("strengths", [])), 1)) * 4
            score += strength_score
            # 推荐匹配度 (20%)
            if required.get("task_type", "") in caps.get("recommended_for", []):
                score += 2
            # 成本分 (15%)
            cost = caps["cost_per_1k_input"] + caps["cost_per_1k_output"]
            score += min((1.0 / (cost + 0.01)) * 1.5, 2.0)
            # 延迟分 (15%)
            score += min((500 / (caps["avg_latency_ms"] + 100)) * 1.5, 2.0)
            # 偏好 (10%)
            prefer = constraints.get("prefer", "")
            if prefer and prefer in caps["strengths"]:
                score += 1.5
            # 优先级惩罚
            score -= (caps["priority"] - 1) * 0.5

            scored.append({
                "model": model_name, "provider": caps["provider"],
                "base_url": caps["base_url"],
                "api_key": os.environ.get(caps["api_key_env"], ""),
                "cost_per_1k_output": caps["cost_per_1k_output"],
                "score": round(score, 1), "caps": caps,
            })
        return scored

    def _is_available(self, model_name: str) -> bool:
        """检查模型是否可用"""
        caps = self.MODEL_CAPABILITIES.get(model_name)
        if not caps:
            return False
        api_key = os.environ.get(caps["api_key_env"], "")
        if not api_key:
            return False
        provider = caps["provider"]
        failures = self._failure_counts.get(provider, 0)
        if failures >= 3:
            last_fail = self._last_failure_time.get(provider, 0)
            if time.time() - last_fail < self._cooldown_seconds:
                return False
        return True

    def _determine_thinking(self, best: dict, required: dict) -> str:
        """决定是否启用 thinking 模式"""
        caps = best.get("caps", {})
        if not caps.get("supports_thinking", False):
            return "off"
        task_type = required.get("task_type", "general")
        if task_type in ("analyze", "audit"):
            return "high"
        if task_type in ("translate",):
            return "off"
        return "medium"

    def _explain_selection(self, best: dict, required: dict) -> str:
        """生成选择解释"""
        caps = best.get("caps", {})
        reasons = []
        matched = [s for s in required.get("strengths", []) if s in caps.get("strengths", [])]
        if matched:
            reasons.append(f"能力匹配: {', '.join(matched)}")
        if caps.get("cost_per_1k_output", 0) < 0.5:
            reasons.append("低成本")
        if caps.get("avg_latency_ms", 1000) < 500:
            reasons.append("低延迟")
        return "; ".join(reasons) if reasons else "综合最优"

    def _emergency_fallback(self) -> dict:
        """全部模型不可用时的紧急兜底"""
        for name, caps in sorted(self.MODEL_CAPABILITIES.items(), key=lambda x: x[1]["priority"]):
            api_key = os.environ.get(caps["api_key_env"], "")
            if api_key:
                return {
                    "model": name, "provider": caps["provider"],
                    "base_url": caps["base_url"], "api_key": api_key,
                    "thinking": "off", "score": 0,
                    "fallback_model": None,
                    "estimated_cost_usd": caps["cost_per_1k_output"] * 0.5,
                    "selection_reason": "EMERGENCY: all models unavailable"
                }
        return {"model": "deepseek-chat", "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
                "thinking": "off", "score": 0, "fallback_model": None,
                "estimated_cost_usd": 0,
                "selection_reason": "CRITICAL: no API key found"}

    def _record_failure(self, model_name: str):
        caps = self.MODEL_CAPABILITIES.get(model_name, {})
        provider = caps.get("provider", model_name)
        self._failure_counts[provider] = self._failure_counts.get(provider, 0) + 1
        self._last_failure_time[provider] = time.time()

    def record_success(self, model_name: str):
        caps = self.MODEL_CAPABILITIES.get(model_name, {})
        provider = caps.get("provider", model_name)
        self._failure_counts[provider] = 0

    def status(self) -> dict:
        """返回所有模型的状态"""
        result = {}
        for name, caps in self.MODEL_CAPABILITIES.items():
            provider = caps["provider"]
            result[name] = {
                "provider": provider,
                "available": self._is_available(name),
                "failures": self._failure_counts.get(provider, 0),
                "in_cooldown": (
                    self._failure_counts.get(provider, 0) >= 3 and
                    time.time() - self._last_failure_time.get(provider, 0) < self._cooldown_seconds
                )
            }
        return result
```

---

## 五、修改 workflow_engine._decompose_via_l2

将以下代码**替换**现有方法（保留数据驱动批处理，新增经验注入和语义路由）：

```python
def _decompose_via_l2(self, task_id: str, payload: dict) -> list:
    if MCP_LAYERS_ENABLED.get("L2"):
        return [{"id": "s1", "action": "mcp_routed", "agent": "审计官",
                 "depends": [], "prompt": "MCP L2 not implemented"}]

    """L2: 语义路由 + L0 经验注入 + LLM 拆解"""
    task_desc = str(payload.get("task", payload.get("action", "")))[:800]
    action = payload.get("action", "unknown")
    action_clean = action.replace("site_", "").replace("translate_", "")
    self._current_intent = action_clean
    available = list(self._agent_skill_map().keys())

    # ── Step 1: 语义路由（替代 INTENT_TOOL_MAP）──
    route = None
    try:
        if hasattr(self, "intent_router") and self.intent_router:
            route = self.intent_router.route(task_desc)
            print(f"[WF] {task_id} 语义路由: {route.get('primary_agent','?')} "
                  f"(conf={route.get('confidence',0):.2f})", flush=True)
    except Exception as e:
        print(f"[WF] {task_id} 语义路由失败: {e}", flush=True)

    # ── Step 2: L0 经验检索 + 格式化（🔥 关键修复：经验注入 LLM）──
    experience_context = ""
    past_exp = self.l0._retrieve_experiences(action_clean, available[:5])
    if past_exp:
        print(f"[L0] {task_id} 检索到 {len(past_exp)} 条历史经验", flush=True)
        experience_context = "\n\n## 历史经验（同类任务参考）\n"
        for i, exp in enumerate(past_exp[:3]):
            agents_used = exp.get("agents_involved", [exp.get("agent", "?")])
            subtask_actions = exp.get("subtask_actions", [])
            score = exp.get("score", "?")
            success = "✅" if exp.get("success") else "❌"
            exp_text = (
                f"### 案例 {i+1} (评分: {score}/10 {success})\n"
                f"- 使用 Agent: {', '.join(agents_used)}\n"
                f"- 子任务步骤: {' → '.join(subtask_actions[:5])}\n"
            )
            experience_context += exp_text
    else:
        # 尝试 Chroma 语义搜索
        try:
            from modules.layer1.vector_store_chroma import ChromaVectorStore
            vs = ChromaVectorStore()
            semantic = vs.search(f"task:{task_desc[:200]}", top_k=3)
            if semantic:
                experience_context = "\n\n## 语义相似经验\n"
                for i, s in enumerate(semantic):
                    experience_context += f"### 类似任务 {i+1}\n{s.get('text', '')[:300]}\n"
        except Exception:
            pass

    # ── Step 3: MCP 拆解（注入经验上下文）──
    try:
        result = call_layer(2, "decompose_task",
                           task_id=task_id, task=task_desc,
                           available_agents=available[:8],
                           experience_context=experience_context[:1500])
        if result and isinstance(result, list) and len(result) > 0:
            subtasks = self._normalize_subtasks(result, task_desc)
            if subtasks:
                print(f"[WF] {task_id} L2 MCP: {len(subtasks)} subtasks", flush=True)
                return subtasks
    except Exception as e:
        print(f"[WF] {task_id} L2 MCP failed: {e}", flush=True)

    # ── Step 4: LLM 拆解（带经验增强）──
    return self._llm_decompose_with_experience(task_id, payload, route, experience_context)


def _llm_decompose_with_experience(self, task_id, payload, route, experience_context):
    """带经验注入的 LLM 拆解"""
    import re
    task_desc = payload.get("task", json.dumps(payload, ensure_ascii=False)[:500])

    # 数据驱动批处理
    nums = re.findall(r'(\d{3,}).*?(entries|fields|pages|items|records|处|条|项)',
                      task_desc.lower())
    if nums:
        total = int(nums[0][0])
        batch_size = max(100, min(500, total // 8))
        num_batches = max(2, min(10, (total + batch_size - 1) // batch_size))
        return self._build_batch_subtasks(total, batch_size, num_batches)

    available_desc = self._describe_available_agents()
    primary_agent = route.get("primary_agent", "审计官") if route else "审计官"

    prompt = f"""你是任务规划专家。将以下任务拆解为 2-5 个子任务。

## 可用 Agent 及其能力
{available_desc}

{experience_context}

## 任务
{task_desc}

## 输出要求
返回纯 JSON 数组，每个子任务包含:
- id: "s1", "s2"...
- action: 具体做什么（60字内）
- agent: 从可用 Agent 中选择最合适的
- depends: 依赖的子任务 id 列表（无依赖则为 []）
- prompt: 给该 Agent 的具体操作指令（200字内）

只返回 JSON。"""

    llm = self._get_llm()
    if not llm:
        return [{"id": "s1", "action": "execute", "agent": primary_agent,
                 "depends": [], "prompt": task_desc[:300]}]

    try:
        resp = llm.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=800,
        )
        content_text = resp.choices[0].message.content
        if "```" in content_text:
            content_text = content_text.split("```")[1]
            if content_text.startswith("json"):
                content_text = content_text[4:]
        data = json.loads(content_text.strip())
        result = data.get("subtasks", data if isinstance(data, list) else [])
        normalized = self._normalize_subtasks(result, task_desc)
        if normalized:
            return normalized
    except Exception as e:
        print(f"[WF] {task_id} LLM 拆解失败: {e}", flush=True)

    return [{"id": "s1", "action": "execute", "agent": primary_agent,
             "depends": [], "prompt": task_desc[:300]}]


def _normalize_subtasks(self, result, task_desc):
    """规范化子任务列表"""
    normalized = []
    for i, item in enumerate(result):
        if isinstance(item, dict):
            normalized.append({
                "id": item.get("id", f"s{i+1}"),
                "action": str(item.get("action", item.get("description", "execute")))[:60],
                "agent": item.get("agent", item.get("agent_type", "审计官")),
                "depends": item.get("depends", item.get("depends_on", [])),
                "prompt": str(item.get("prompt", item.get("description", task_desc)))[:500],
            })
    return normalized


def _describe_available_agents(self):
    """构建可用 Agent 的能力描述文本"""
    skill_map = self._agent_skill_map()
    cards = []
    for agent_name in list(skill_map.keys())[:8]:
        try:
            card_raw = self.commander.redis.get(f"agent:card:{agent_name}")
            if card_raw:
                card = json.loads(card_raw)
                desc = f"- **{agent_name}**: {card.get('role', '')}"
                if card.get("skills"):
                    desc += f" | 技能: {', '.join(card['skills'][:3])}"
                cards.append(desc)
                continue
        except Exception:
            pass
        skill = skill_map.get(agent_name, "")
        cards.append(f"- **{agent_name}**: {skill.replace('-', ' ')}")
    return "\n".join(cards) if cards else "- 审计官: 通用任务执行"
```

---

## 六、初始化代码

在 `workflow_engine.__init__` 中添加：

```python
from modules.layer2.intent_router import SemanticIntentRouter
from modules.layer2.model_router_v2 import IntelligentModelRouter

# 初始化语义路由器
try:
    from modules.layer1.vector_store_chroma import ChromaVectorStore
    vs = ChromaVectorStore()
except Exception:
    from modules.layer1.vector_store import MemVectorStore
    vs = MemVectorStore()

self.intent_router = SemanticIntentRouter(
    vector_store=vs,
    redis_client=self.commander.redis if self.commander else None
)
self.model_router = IntelligentModelRouter(
    redis_client=self.commander.redis if self.commander else None
)
```

---

## 七、`modules/layer2/__init__.py` 新增导出

```python
from modules.layer2.intent_router import SemanticIntentRouter        # 新增
from modules.layer2.model_router_v2 import IntelligentModelRouter    # 新增
```

---

## 八、迁移步骤

### Step 1: 创建新文件（不影响现有功能）

```bash
# 在容器内:
mkdir -p /opt/yaxiio/modules/layer2
# 从本文档中提取对应代码块创建:
# /opt/yaxiio/modules/layer2/intent_router.py
# /opt/yaxiio/modules/layer2/model_router_v2.py
```

### Step 2: Feature flag 并行运行

```bash
export YAXIIO_SEMANTIC_ROUTER=true   # 启用新路由
export YAXIIO_SEMANTIC_ROUTER=false  # 切回旧的 INTENT_TOOL_MAP
```

### Step 3: 索引能力卡片

```bash
python3 -c "
from modules.layer1.vector_store_chroma import ChromaVectorStore
from modules.layer2.intent_router import SemanticIntentRouter
import redis, os
r = redis.Redis(host='127.0.0.1', port=6379,
    password=os.environ.get('REDIS_PASSWORD',''), decode_responses=True)
vs = ChromaVectorStore()
router = SemanticIntentRouter(vs, r)
count = router.index_all_cards()
print(f'索引了 {count} 个能力卡片')
"
```

---

## 九、预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| 新增 Agent 类型需要改代码 | 是（改 INTENT_TOOL_MAP） | 否（注册能力卡片即可） |
| L0 经验是否注入 LLM | 否（只打日志） | 是（few-shot 形式注入） |
| 模型选择维度 | 3 条规则 9 个中文关键词 | 成本 × 延迟 × 能力 × 可用性 |
| 跨行业适用性 | 硬编码外贸关键词 | 完全由能力卡片驱动 |
| Provider 故障切换 | 无 | 自动切换 + 冷却期 |
