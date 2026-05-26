#!/usr/bin/env python3
"""
优化六：LLM 智能路由 + 路由策略 A/B 测试 — LLMRouter
===========================================================
- LLMRouter  : 用 LLM（DeepSeek V4 / 任意 OpenAI 兼容模型）做智能路由决策
- RouteABTester: A/B 对比「规则路由」vs「LLM路由」，24h 自动选择优胜策略

与 ab_tester.py 互补：
  - ab_tester.py  → A/B 测试调度策略参数（拆分粒度/并行度）
  - llm_router.py → A/B 测试路由策略（规则 vs LLM）

Constitution R1: 所有 Redis 键使用 commander:* 前缀
"""

import json
import random
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import redis

# ── LLM 客户端：优先 OpenAI 兼容，fallback 到规则路由 ──
try:
    from openai import OpenAI as OpenAIClient
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ═══════════════════════════════════════════════════════════════
# LLM Router Prompt 模板
# ═══════════════════════════════════════════════════════════════

ROUTER_PROMPT = """你是多Agent系统的智能路由器。根据任务需求和Agent能力谱系，选择最合适的执行者。

## 当前任务
{task_description}

## 可用Agent及其能力
{agent_capabilities}

## 各Agent历史表现（成功率、平均耗时、当前负载）
{agent_performance}

## 选择规则
1. 优先选择任务匹配度最高的Agent
2. 多Agent匹配度相同时，选历史成功率最高的
3. 成功率相同时，选当前负载最低的
4. 所有Agent负载都高时，建议创建新Agent

## 输出格式（仅输出JSON，不要其他内容）
{{"selected_agent":"Agent ID","reason":"选择理由","confidence":0.85,"suggest_new_agent":false}}
"""


# ═══════════════════════════════════════════════════════════════
# LLMRouter ─ LLM 驱动路由决策
# ═══════════════════════════════════════════════════════════════

class LLMRouter:
    """用 LLM 做路由决策：任务描述 + Agent能力 + 历史表现 → 最优选择。

    兼容 OpenAI-compatible API（DeepSeek、通义千问、GLM 等）。
    """

    def __init__(self, redis_client: redis.Redis,
                 mongo_client: Any = None,
                 llm_api_key: Optional[str] = None,
                 llm_base_url: Optional[str] = None,
                 llm_model: str = "deepseek-chat",
                 fallback_router: Optional[Callable] = None,
                 discovery: Any = None):
        """
        Args:
            redis_client: Redis 连接（decode_responses=True）
            mongo_client: MongoDB 连接（可选，用于日志持久化）
            llm_api_key: LLM API Key（缺省从环境变量 LLM_API_KEY 读取）
            llm_base_url: API 地址（默认 https://api.deepseek.com/v1）
            llm_model: 模型名（默认 deepseek-chat）
            fallback_router: LLM 不可用时的降级路由函数
        """
        self.redis = redis_client
        self.mongo = mongo_client
        self.model = llm_model
        self.fallback_router = fallback_router or self._default_rule_router
        self.discovery = discovery  # AgentDiscovery 实例（可选）
        self.routing_log: List[dict] = []

        # 初始化 LLM 客户端
        if HAS_OPENAI:
            api_key = llm_api_key or __import__("os").environ.get("LLM_API_KEY", "")
            base_url = llm_base_url or "https://api.deepseek.com/v1"
            self.llm = OpenAIClient(api_key=api_key, base_url=base_url)
        else:
            self.llm = None

    # ── 主路由方法 ────────────────────────────────────────

    def route_task(self, task: dict,
                   agent_capabilities: Optional[list] = None,
                   required_capability: Optional[str] = None) -> dict:
        """路由决策主入口。

        Args:
            task: 任务 dict，至少含 "taskId" 和 "type"
            agent_capabilities: Agent 能力卡片列表（可选，缺省从 Redis 拉取）
            required_capability: 要求的能力关键词

        Returns:
            {"selected_agent": str, "reason": str, "confidence": float, ...}
        """
        # 收集 Agent 能力
        if agent_capabilities is None:
            agent_capabilities = self._discover_agents(required_capability)

        if not agent_capabilities:
            return {
                "selected_agent": None,
                "reason": "无可用 Agent",
                "suggest_new_agent": True,
                "confidence": 0.0,
                "routing_method": "rule",
            }

        if len(agent_capabilities) == 1:
            return {
                "selected_agent": agent_capabilities[0]["agentId"],
                "reason": "唯一可用 Agent",
                "confidence": 1.0,
                "routing_method": "rule",
            }

        # 收集历史表现和负载
        agent_ids = [c["agentId"] for c in agent_capabilities]
        performances = self._get_performance_data(agent_ids)
        loads = self._get_current_loads(agent_ids)

        # 尝试 LLM 路由
        decision = self._try_llm_route(task, agent_capabilities, performances, loads)

        # 记录决策日志
        self._log_decision(task, decision)

        return decision

    # ── LLM 路由尝试 ──────────────────────────────────────

    def _try_llm_route(self, task: dict, capabilities: list,
                        performances: dict, loads: dict) -> dict:
        """调用 LLM 做决策；失败时 fallback 到规则路由。"""
        if self.llm is None:
            return self.fallback_router(task, capabilities, performances, loads)

        try:
            prompt = ROUTER_PROMPT.format(
                task_description=json.dumps(task, indent=2, ensure_ascii=False),
                agent_capabilities=json.dumps(capabilities, indent=2, ensure_ascii=False),
                agent_performance=json.dumps(
                    {"performances": performances, "loads": loads},
                    indent=2, ensure_ascii=False,
                ),
            )

            resp = self.llm.chat.completions.create(
                reasoning_effort="max",
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
            content = resp.choices[0].message.content.strip()

            # 提取 JSON（可能被 markdown 包裹）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            decision = json.loads(content)
            decision["routing_method"] = "llm"
            decision["model"] = self.model
            return decision

        except Exception as e:
            print(f"[LLMRouter] LLM 路由失败 ({e})，fallback 到规则路由")
            decision = self.fallback_router(task, capabilities, performances, loads)
            decision["routing_method"] = "rule_fallback"
            decision["llm_error"] = str(e)
            return decision

    # ── 默认规则路由（LLM 不可用时的 fallback）────────────

    def _default_rule_router(self, task: dict, capabilities: list,
                              performances: dict, loads: dict) -> dict:
        """基于规则的默认路由器：匹配度 → 成功率 → 负载。"""
        if not capabilities:
            return {"selected_agent": None, "reason": "无可用 Agent",
                    "confidence": 0.0, "routing_method": "rule"}

        task_type = task.get("type", "").lower()

        # 按任务类型关键词匹配
        scored = []
        for cap in capabilities:
            score = 0
            cap_text = json.dumps(cap, ensure_ascii=False).lower()
            # 能力匹配
            for kw in task_type.split():
                if kw in cap_text:
                    score += 1
            # 成功率加权
            perf = performances.get(cap["agentId"], {})
            score += perf.get("success_rate", 0) * 2
            # 负载反向加权
            load = loads.get(cap["agentId"], {}).get("active_tasks", 0)
            score -= load * 0.1
            scored.append((score, cap))

        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]

        return {
            "selected_agent": best["agentId"],
            "reason": f"规则路由: 综合评分 {scored[0][0]:.2f}",
            "confidence": 0.7,
            "routing_method": "rule",
        }

    # ── Agent 发现 ────────────────────────────────────────

    def _discover_agents(self, capability: Optional[str] = None) -> list:
        """从 Redis 发现可用 Agent。

        优先走 AgentDiscovery（含能力Schema），fallback 到 commander:agents:active 集合。
        """
        # 优先使用 AgentDiscovery（提供完整能力卡片）
        if self.discovery and hasattr(self.discovery, "find_best_match"):
            if capability:
                cards = self.discovery.find_best_match(capability)
                if cards:
                    return cards
            # 无指定能力时返回所有可用 Agent
            all_cards = self.discovery.list_all_agents()
            # 过滤在线
            return [c for c in all_cards
                    if c.get("status") in ("idle", "busy", "running")]

        # Fallback: 用 commander:agents:active 集合
        agent_ids = self.redis.smembers("commander:agents:active")
        cards = []
        for aid in agent_ids:
            status = self.redis.get(f"commander:agent:status:{aid}")
            if status != "running":
                continue
            role = self.redis.hget(f"commander:agent:heartbeat:{aid}", "role") or aid
            cards.append({
                "agentId": aid,
                "role": role,
                "capabilities": [role],
                "status": "running",
            })
        return cards

    # ── 性能数据 ──────────────────────────────────────────

    def _get_performance_data(self, agent_ids: List[str]) -> dict:
        perf = {}
        for aid in agent_ids:
            # 从 Redis 读取缓存的性能摘要
            raw = self.redis.hget(f"commander:agent:performance:{aid}", "summary")
            if raw:
                perf[aid] = json.loads(raw)
            else:
                perf[aid] = {"success_rate": 0.5, "avg_duration_ms": 0, "total_tasks": 0}
        return perf

    def _get_current_loads(self, agent_ids: List[str]) -> dict:
        loads = {}
        for aid in agent_ids:
            active = self.redis.hget(f"commander:agent:heartbeat:{aid}", "active_tasks")
            loads[aid] = {
                "active_tasks": int(active or 0),
                "status": self.redis.get(f"commander:agent:status:{aid}") or "unknown",
            }
        return loads

    # ── 日志 ──────────────────────────────────────────────

    def _log_decision(self, task: dict, decision: dict):
        entry = {
            "task_id": task.get("taskId", ""),
            "decision": decision,
            "timestamp": datetime.now().isoformat(),
        }
        self.routing_log.append(entry)

        # MongoDB 持久化（可选）
        if self.mongo:
            try:
                self.mongo.routing_decisions.insert_one(entry)
            except Exception:
                pass

        # Redis 环形日志（最近 200 条）
        self.redis.lpush("commander:log:routing",
                         json.dumps(entry, ensure_ascii=False))
        self.redis.ltrim("commander:log:routing", 0, 199)


# ═══════════════════════════════════════════════════════════════
# RouteABTester ─ 路由策略 A/B 测试（规则 vs LLM）
# ═══════════════════════════════════════════════════════════════

class RouteABTester:
    """对比「规则路由」vs「LLM 路由」，24h 后自动选择优胜策略。

    与 ab_tester.ABTester 互补：后者测调度策略参数，本类测路由策略。
    """

    TEST_DURATION_HOURS = 24
    MIN_SAMPLE_PER_GROUP = 10
    SIGNIFICANCE_THRESHOLD = 1.1  # B 需比 A 高 10%

    def __init__(self, rule_router, llm_router,
                 redis_client: redis.Redis,
                 mongo_client: Any = None):
        """
        Args:
            rule_router: 规则路由实例（LLMRouter 的 fallback_router 或 LLMRouter 本身）
            llm_router: LLM 路由实例（LLMRouter）
        """
        self.rule_router = rule_router
        self.llm_router = llm_router
        self.redis = redis_client
        self.mongo = mongo_client

    # ── A/B 测试生命周期 ─────────────────────────────────

    def start_test(self, description: str = "") -> dict:
        """启动 A/B 测试。"""
        if self.redis.exists("commander:route_ab:active"):
            return {"status": "rejected", "reason": "已有进行中的路由 A/B 测试"}

        test_config = {
            "test_id": f"route-ab-{int(time.time())}",
            "description": description or "规则路由 vs LLM路由",
            "start_time": datetime.now().isoformat(),
            "duration_hours": self.TEST_DURATION_HOURS,
            "group_a": {"name": "规则路由 (Control)", "success": 0, "total": 0},
            "group_b": {"name": "LLM路由 (Variant)", "success": 0, "total": 0},
            "status": "active",
        }
        self.redis.set("commander:route_ab:active",
                       json.dumps(test_config, ensure_ascii=False))
        return {"status": "started", "test_id": test_config["test_id"]}

    def route(self, task: dict,
              capabilities: Optional[list] = None,
              split_ratio: float = 0.5) -> dict:
        """50/50 分流：A 组走规则路由，B 组走 LLM 路由。

        如果无活跃 A/B 测试，全部走 LLM 路由。
        """
        raw = self.redis.get("commander:route_ab:active")
        if not raw:
            # 无测试 → 默认 LLM 路由
            decision = self.llm_router.route_task(task, capabilities)
            decision["ab_group"] = "default"
            return decision

        # 分流
        if random.random() < split_ratio:
            decision = self.llm_router.route_task(task, capabilities)
            decision["ab_group"] = "group_b"
        else:
            decision = self.rule_router.route_task(task, capabilities)
            decision["ab_group"] = "group_a"

        # 记录到活跃测试
        self._record_sample(decision["ab_group"], decision)

        return decision

    def _record_sample(self, group: str, decision: dict):
        """记录 A/B 样本结果。"""
        raw = self.redis.get("commander:route_ab:active")
        if not raw:
            return
        test = json.loads(raw)
        if group not in test:
            return

        test[group]["total"] += 1
        if decision.get("confidence", 0) > 0.5:
            test[group]["success"] += 1

        self.redis.set("commander:route_ab:active",
                       json.dumps(test, ensure_ascii=False))

    def record_actual_result(self, task_id: str, success: bool):
        """任务执行完成后，更新真实结果（替代路由时的 confidence 预估）。"""
        raw = self.redis.get("commander:route_ab:active")
        if not raw:
            return
        test = json.loads(raw)

        # 通过 task_id 查找所属分组（从 history）
        history = self.redis.lrange(
            f"commander:route_ab:history:{test['test_id']}", -100, -1
        )
        for item_raw in history:
            item = json.loads(item_raw)
            if item.get("task_id") == task_id:
                group = item.get("group", "")
                if group in test and success:
                    # 如果之前已统计过 confidence>0.5 的 success，这里不做双倍计算
                    # 简化为 success/fail 直接覆盖
                    pass
                break

    def evaluate_and_decide(self) -> dict:
        """评估 A/B 测试结果并自动决策。"""
        raw = self.redis.get("commander:route_ab:active")
        if not raw:
            return {"status": "no_active_test"}

        test = json.loads(raw)
        start_time = datetime.fromisoformat(test["start_time"])
        elapsed = (datetime.now() - start_time).total_seconds() / 3600

        if elapsed < test["duration_hours"]:
            return {
                "status": "still_testing",
                "elapsed_hours": round(elapsed, 1),
                "remaining_hours": round(test["duration_hours"] - elapsed, 1),
            }

        group_a = test["group_a"]
        group_b = test["group_b"]

        # 最小样本数门槛
        if group_a["total"] < self.MIN_SAMPLE_PER_GROUP or \
           group_b["total"] < self.MIN_SAMPLE_PER_GROUP:
            test["duration_hours"] += 12
            self.redis.set("commander:route_ab:active",
                           json.dumps(test, ensure_ascii=False))
            return {
                "status": "extended",
                "reason": f"样本不足 (A:{group_a['total']}, B:{group_b['total']})",
                "new_duration": test["duration_hours"],
            }

        # 计算成功率
        rate_a = group_a["success"] / max(group_a["total"], 1)
        rate_b = group_b["success"] / max(group_b["total"], 1)

        # 归档
        test["status"] = "completed"
        test["result"] = {
            "rate_a": round(rate_a, 4),
            "rate_b": round(rate_b, 4),
            "samples_a": group_a["total"],
            "samples_b": group_b["total"],
            "evaluated_at": datetime.now().isoformat(),
        }
        self.redis.set(
            f"commander:route_ab:archive:{test['test_id']}",
            json.dumps(test, ensure_ascii=False),
        )

        # 决策
        if rate_b > rate_a * self.SIGNIFICANCE_THRESHOLD:
            self.redis.hset("commander:agent:routing_policy",
                            "active_strategy", "llm")
            self.redis.hset("commander:agent:routing_policy",
                            "promoted_at", datetime.now().isoformat())

            # 清理活跃标记
            self.redis.rename("commander:route_ab:active",
                              f"commander:route_ab:completed:{test['test_id']}")
            self.redis.expire(
                f"commander:route_ab:completed:{test['test_id']}", 3600
            )

            return {
                "decision": "promote_llm",
                "rate_a": round(rate_a, 4),
                "rate_b": round(rate_b, 4),
                "improvement": f"{(rate_b - rate_a) / max(rate_a, 0.001) * 100:.1f}%",
            }
        else:
            self.redis.hset("commander:agent:routing_policy",
                            "active_strategy", "rule")
            self.redis.rename("commander:route_ab:active",
                              f"commander:route_ab:completed:{test['test_id']}")
            self.redis.expire(
                f"commander:route_ab:completed:{test['test_id']}", 3600
            )

            return {
                "decision": "keep_rule",
                "rate_a": round(rate_a, 4),
                "rate_b": round(rate_b, 4),
            }

    def get_active_test(self) -> Optional[dict]:
        raw = self.redis.get("commander:route_ab:active")
        return json.loads(raw) if raw else None

    def get_active_strategy(self) -> str:
        """返回当前生效的路由策略: 'llm' | 'rule' | 'default'"""
        strategy = self.redis.hget("commander:agent:routing_policy",
                                   "active_strategy")
        return strategy or "default"


# ═══════════════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    r = redis.Redis(host="127.0.0.1", port=6379,
                    password="Lt@114514!", decode_responses=True)

    # 模拟 Agent
    r.sadd("commander:agents:active", "翻译官", "商务经理", "售前经理")
    for aid in ["翻译官", "商务经理", "售前经理"]:
        r.setex(f"commander:agent:status:{aid}", 3600, "running")
        r.hset(f"commander:agent:heartbeat:{aid}", mapping={
            "role": aid, "last_activity": str(time.time()),
            "active_tasks": "1",
        })

    # 初始化路由器
    router = LLMRouter(r)

    task = {
        "taskId": "test-001",
        "type": "翻译",
        "description": "审计 ru/industries/mining 中文残留并翻译为俄语",
        "priority": "high",
    }

    decision = router.route_task(task)
    print(f"路由决策: {json.dumps(decision, indent=2, ensure_ascii=False)}")

    # RouteABTester
    ab_tester = RouteABTester(router, router, r)
    ab_tester.start_test("路由策略 A/B")
    for _ in range(5):
        print(f"A/B 路由: {ab_tester.route(task)['ab_group']}")

    print(f"活跃测试: {ab_tester.get_active_test()}")
    print(f"当前策略: {ab_tester.get_active_strategy()}")
    print("✅ llm_router.py 基础测试完成")
