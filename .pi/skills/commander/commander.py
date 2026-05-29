#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
Commander — 多Agent任务编排引擎
=================================
Yaxiio 系统中的 Commander 角色实现。负责：
  - 任务接收与去重（TaskAnalyzer）
  - 弹性伸缩（AutoScaler）
  - 双通道可靠通信（ReliableComm）
  - A/B 测试自进化（ABTester）
  - 故障转移与降级（AgentFailover / TaskDegradation）
  - LLM 智能路由（LLMRouter / RouteABTester）
  - Agent 能力发现（A2A 协议适配层）

对外通过 Redis Pub/Sub 接收任务，通过 gateway.py（WebSocket/HTTP）接入。

兼容 v1 agent-commander.py 的所有 Pub/Sub 消息格式。
"""

import asyncio
import json
import os
import sys
import time
from typing import Optional

# 允许从同级目录导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from task_analyzer import TaskAnalyzer
from auto_scaler import AutoScaler
from reliable_comm import ReliableComm
from ab_tester import ABTester
from failover import AgentFailover, TaskDegradation, RedisHAWrapper, DEGRADATION_LEVELS, BACKUP_ROLES
from llm_router import LLMRouter, RouteABTester
from a2a_protocol import AgentDiscovery, AgentCard

# v2.3 扩展系统（可选依赖）
try:
    from extension_router import ExtensionRouter, build_extension_router
    HAS_EXTENSION_ROUTER = True
except ImportError:
    HAS_EXTENSION_ROUTER = False
    print("[雅溪 Yaxiio] ⚠️ extension_router 未找到，扩展系统不可用")

# v2.3.1 Token 预算控制器 + Prompt 优化器（可选依赖）
_token_dir = os.path.dirname(os.path.abspath(__file__))
_skills_root = os.path.join(_token_dir, "..", "..")

try:
    sys.path.insert(0, os.path.join(_skills_root, "token-budget-controller"))
    from token_budget import TokenBudgetController, AgentTokenGuard
    HAS_TOKEN_BUDGET = True
except ImportError:
    HAS_TOKEN_BUDGET = False
    TokenBudgetController = None  # type: ignore
    AgentTokenGuard = None  # type: ignore
    print("[雅溪 Yaxiio] ⚠️ token-budget-controller 未找到")

try:
    sys.path.insert(0, os.path.join(_skills_root, "prompt-optimizer"))
    from prompt_optimizer import PromptOptimizer, PromptOptimizerGuard
    HAS_PROMPT_OPTIMIZER = True
except ImportError:
    HAS_PROMPT_OPTIMIZER = False
    PromptOptimizer = None  # type: ignore
    PromptOptimizerGuard = None  # type: ignore
    print("[雅溪 Yaxiio] ⚠️ prompt-optimizer 未找到")

# v2.1 生命周期引擎（可选依赖）
try:
    from agent_lifecycle_v2 import (
        AgentLifecycleManagerV2,
        AgentDesigner,
        AutonomousTaskDecomposer,
        SelfEvolvingCommander,
        SafetyBoundary,
        AgentQuadrant,
        build_commander_extensions,
    )
    HAS_LIFECYCLE_V2 = True
except ImportError:
    HAS_LIFECYCLE_V2 = False
    print("[雅溪 Yaxiio] ⚠️ agent_lifecycle_v2 未找到，生命周期引擎不可用")


class Commander:
    """多Agent系统总指挥 v2.0"""

    # 静态 Agent 注册表
    STATIC_AGENTS = ["翻译官", "商务经理", "售前经理"]
    # 可动态创建的扩展 Agent
    EXTENDABLE_AGENTS = ["审计官", "俄语审计官"]

    def __init__(self, agent_id: str = "commander",
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None,
                 store=None,
                 use_sentinel: bool = False,
                 sentinel_hosts: list = None,
                 sentinel_service: str = "lightingmetal-redis",
                 llm_api_key: str = None,
                 llm_base_url: str = None,
                 llm_model: str = "deepseek-chat",
                 enable_lifecycle: bool = True,
                 enable_designer: bool = True,
                 enable_evolver: bool = True,
                 enable_extensions: bool = True):
        self.agent_id = agent_id

        # ── Redis 密码: 显式传入 > 环境变量 ──
        if redis_password is None:
            redis_password = os.environ.get("REDIS_PASSWORD", "")

        # ── Redis 连接（支持 Sentinel 高可用）──
        if use_sentinel and sentinel_hosts:
            self._redis_wrapper = RedisHAWrapper(
                sentinel_hosts=sentinel_hosts,
                service_name=sentinel_service,
                password=redis_password,
            )
            redis_for_modules = self._redis_wrapper.master
        else:
            import redis as redis_lib
            self._redis_wrapper = None
            redis_for_modules = redis_lib.Redis(
                host=redis_host, port=redis_port,
                password=redis_password, decode_responses=True,
            )
            self._modules_redis = redis_for_modules

        # ── 五大优化模块 ──
        self.task_analyzer = TaskAnalyzer(redis_host, redis_port, redis_password)
        self.auto_scaler = AutoScaler(redis_host, redis_port, redis_password)
        self.comm = ReliableComm(agent_id, redis_host, redis_port, redis_password)
        self.ab_tester = ABTester(redis_host, redis_port, redis_password)
        self.failover = AgentFailover(
            redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master,
            store=store,
        )
        self.degradation = TaskDegradation(
            redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master,
            store=store,
        )

        # ── 优化六：LLM 智能路由（可选）──
        self.discovery = AgentDiscovery(
            redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master
        )
        self.llm_router = LLMRouter(
            redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master,
            store=store,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            discovery=self.discovery,
        )
        self.route_ab = RouteABTester(
            self.llm_router, self.llm_router,
            redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master,
            store=store,
        )

        # ── v2.1 生命周期引擎 ──
        self.lifecycle = None
        self.designer = None
        self.decomposer = None
        self.evolver = None
        self.safety_boundary = None

        if HAS_LIFECYCLE_V2 and enable_lifecycle:
            self._init_lifecycle_engine(
                redis_for_modules, store,
                llm_api_key, llm_base_url, llm_model,
                enable_designer, enable_evolver,
            )

        # ── v2.3 扩展系统（自主进化：Skill/MCP 动态管理）──
        self.extension_router = None
        if HAS_EXTENSION_ROUTER and enable_extensions:
            redis_client = redis_for_modules if not self._redis_wrapper else self._redis_wrapper.master
            self.extension_router = build_extension_router(
                redis_client=redis_client,
                store=store,
                lifecycle_manager=self.lifecycle,
                agent_designer=self.designer,
                task_analyzer=self.task_analyzer,
            )
            # 引导：将现有本地 Skills + MCP 配置同步到 Redis
            self._bootstrap_extension_registry()
            print("[雅溪 Yaxiio] 🔌 扩展系统已激活 (Skill动态挂载 + MCP动态注册)")

        # ── v2.3.1 Token 预算 + Prompt 优化 ──
        self.token_guard = None
        self.prompt_guard = None
        if HAS_TOKEN_BUDGET and TokenBudgetController:
            try:
                token_ctrl = TokenBudgetController(
                    redis_host=redis_host,
                    redis_port=redis_port,
                    redis_password=redis_password,
                )
                self.token_guard = AgentTokenGuard(token_ctrl)
                print("[雅溪 Yaxiio] 💰 Token预算控制器已激活")
            except Exception as e:
                print(f"[雅溪 Yaxiio] ⚠️ Token预算控制器初始化失败: {e}")

        if HAS_PROMPT_OPTIMIZER and PromptOptimizer:
            try:
                prompt_opt = PromptOptimizer(
                    redis_host=redis_host,
                    redis_port=redis_port,
                    redis_password=redis_password,
                )
                self.prompt_guard = PromptOptimizerGuard(prompt_opt)
                print("[雅溪 Yaxiio] 🔧 Prompt优化器已激活")
            except Exception as e:
                print(f"[雅溪 Yaxiio] ⚠️ Prompt优化器初始化失败: {e}")

        # 初始化静态 Agent 能力卡片
        self._register_static_agents()
        self.comm.register_handler(self._on_critical_command)

        # 运行时状态
        self.task_count = 0
        self.start_time = time.time()

        # v2.4 Agent 隔离管理器（可选，待完整集成）
        self.isolation_mgr = None

    # ── v2.1 生命周期引擎初始化 ─────────────────────────────────

    def _init_lifecycle_engine(self, redis_client, store,
                                llm_api_key, llm_base_url, llm_model,
                                enable_designer, enable_evolver):
        """初始化 v2.1 生命周期引擎各模块（同步部分）。"""
        # LLM 客户端（使用 LLMAdapter 统一接口，抹平 Provider 差异）
        from agent_lifecycle_v2 import LLMAdapter
        llm_client = None
        if llm_api_key:
            try:
                llm_client = LLMAdapter(
                    api_key=llm_api_key,
                    base_url=llm_base_url or "https://api.deepseek.com/v1",
                    model=llm_model,
                )
                self.llm_client = llm_client
                if not llm_client.available:
                    print("[雅溪 Yaxiio] ⚠️ LLM 适配器初始化失败（openai 库未安装）")
                    llm_client = None
            except Exception as e:
                print(f"[雅溪 Yaxiio] LLM 客户端初始化失败: {e}")

        # 安全边界
        self.safety_boundary = SafetyBoundary(redis_client)

        # 生命周期管理器
        from agent_lifecycle_v2 import AsyncAgentFactory
        factory = AsyncAgentFactory(redis_client, safety=self.safety_boundary)
        self.lifecycle = AgentLifecycleManagerV2(
            redis_client=redis_client,
            store=store,
            agent_factory=factory,
            safety=self.safety_boundary,
            llm_client=llm_client,
        )

        # Agent 设计师（可选）
        if enable_designer:
            self.designer = AgentDesigner(llm_client=llm_client)
            self.decomposer = AutonomousTaskDecomposer(llm_client=llm_client)

        # 自我进化模块（可选）
        if enable_evolver:
            self.evolver = SelfEvolvingCommander(
                redis_client=redis_client,
                store=store,
                llm_client=llm_client,
                lifecycle_manager=self.lifecycle,
            )

        # 预注册角色
        self._register_lifecycle_roles()

        print(f"[雅溪 Yaxiio] 🧬 生命周期引擎已激活 "
              f"(designer={'✓' if self.designer else '✗'}, "
              f"evolver={'✓' if self.evolver else '✗'})")

    def _bootstrap_extension_registry(self):
        """将现有本地 Skills + MCP 配置同步到 Redis 注册表（启动时一次性）。"""
        try:
            from skill_manager import LocalSkillAdapter
            from mcp_manager import MCPBootstrap

            # 同步本地 Skills
            adapter = LocalSkillAdapter(
                self.extension_router.skill_manager,
                skills_dir=".pi/skills",
            )
            skill_count = len(adapter.bootstrap_local_skills())
            print(f"[雅溪 Yaxiio] 📦 已注册 {skill_count} 个本地 Skill 到 Redis")

            # 同步 MCP Server 配置
            boot = MCPBootstrap(self.extension_router.mcp_manager)
            mcp_count = len(boot.bootstrap())
            print(f"[雅溪 Yaxiio] 🔧 已注册 {mcp_count} 个 MCP Server 到 Redis")
        except Exception as e:
            print(f"[雅溪 Yaxiio] ⚠️ 扩展注册表引导失败: {e}")

    def _register_lifecycle_roles(self):
        """预注册各象限角色到 Redis。"""
        redis_client = getattr(self, "_modules_redis", None) or self.redis
        roles_map = {
            "core":       ["翻译官", "商务经理", "售前经理"],
            "strategic":  ["审计官"],
            "utility":    [],
            "ephemeral":  [],
        }
        for quad, roles in roles_map.items():
            redis_client.delete(f"lifecycle:roles:{quad}")
            if roles:
                redis_client.sadd(f"lifecycle:roles:{quad}", *roles)

    # ── 兼容 v1 代码的 redis 属性 ──
    @property
    def redis(self):
        """直接 Redis 连接（Sentinel 模式下自动返回 master）。"""
        if self._redis_wrapper:
            return self._redis_wrapper.master
        return self.comm.redis

    # ── 核心流程: 处理新任务 ─────────────────────────────────

    def _register_static_agents(self):
        """注册六个标准 Agent 的能力卡片到 AgentDiscovery。"""
        cards = [
            AgentCard("翻译官", "翻译官",
                      ["多语翻译", "术语词典", "内容审计",
                       "英/俄/阿/西/法/葡/德/越/泰/印尼语翻译"]),
            AgentCard("商务经理", "商务经理",
                      ["客户接待", "需求挖掘", "多语言沟通", "邮件回复"]),
            AgentCard("售前经理", "售前经理",
                      ["产品查询", "报价生成", "方案推荐", "规格对比"]),
            AgentCard("审计官", "审计官",
                      ["内容审计", "术语一致性", "参数核查", "质量报告"]),
            AgentCard("俄语审计官", "俄语审计官",
                      ["俄语翻译", "俄语审计", "内容审计"]),
        ]
        for card in cards:
            self.discovery.register(card)
        print(f"[雅溪 Yaxiio] 📋 已注册 {len(cards)} 个 Agent 能力卡片")

    async def handle_task_async(self, task_description: str, context: dict = None) -> dict:
        """
        异步任务处理（v2.1 增强版：智能拆解 + 生命周期管理）。

        相比 handle_task() 的增强：
          - 使用 AutonomousTaskDecomposer 做 LLM 智能拆解
          - 使用 AgentDesigner 为拆解后的子任务匹配/设计 Agent
          - 通过 AgentLifecycleManagerV2 按需创建/复用 Agent
          - 所有创建/销毁经过 SafetyBoundary 安全边界检查
        """
        if not self.decomposer or not self.lifecycle:
            # 降级到同步 handle_task
            return self.handle_task(task_description)

        # 1. 智能拆解
        decomp_result = await self.decomposer.decompose(task_description, context)
        if not decomp_result["feasibility"]["feasible"]:
            return {"status": "infeasible", "reason": decomp_result["feasibility"]["reason"]}

        plan = decomp_result["plan"]
        print(f"[雅溪 Yaxiio] 🧠 智能拆解: {len(plan)} 个原子任务")

        # 2. 按拓扑排序执行
        results = []
        completed = set()
        pending = list(plan)

        while pending:
            ready = [t for t in pending
                     if all(dep in completed for dep in t.get("depends_on", []))]
            if not ready:
                # 理论上不应出现，防御性处理：取出所有剩余任务
                ready = pending

            for task in ready:
                role = task.get("agent_type", "通用Agent")
                quadrant = self._select_quadrant(task)

                # 通过生命周期管理器请求 Agent
                agent_id = await self.lifecycle.request_agent(
                    role=role,
                    quadrant=quadrant,
                    task=task,
                )
                if not agent_id:
                    results.append({"task": task["id"], "status": "no_agent_available"})
                    completed.add(task["id"])
                    continue

                # 通过 ReliableComm 发送任务
                command = {
                    "type": "task",
                    "taskId": f"auto-{task['id']}-{int(time.time())}",
                    "payload": task,
                }
                send_result = self.comm.send_critical_command(role, command)
                results.append({
                    "task": task["id"],
                    "agent_id": agent_id,
                    "role": role,
                    "status": send_result["status"],
                })
                completed.add(task["id"])

            pending = [t for t in pending if t["id"] not in completed]

        self.task_count += 1
        return {
            "status": "dispatched_v2.1",
            "decomposer_intent": decomp_result["intent"],
            "subtasks": len(plan),
            "results": results,
        }

    def handle_task(self, task_description: str) -> dict:
        """处理新任务的完整流程（集成六大优化）。

        流程：
          1. 优化一：查重 → 命中则直接返回历史摘要
          2. 安全边界检查（v2.1）
          3. 优化四：A/B分流 → 决定用当前策略还是新策略拆分
          4. 任务拆分
          5. 优化五：降级检测 → 判断所需 Agent 可用性，L4 直接降级
          6. 优化二：弹性伸缩 → 根据队列深度扩缩容
          7. 优化三/六：双通道分发 + LLM 路由 + 故障转移
          8. 缓存指纹 → 供后续查重
        """
        print(f"[雅溪 Yaxiio] 📥 收到任务: {task_description[:80]}...")

        # ── 打开 trace span ──
        trace_id = TraceContext.start_trace()
        TraceContext.set_tag("task_description", task_description[:100])

        # ── v0.2.6 扩展检查：分析任务是否需要新 Skill/MCP/Agent ──
        if self.extension_router:
            # 异步运行扩展分析（不阻塞主流程）
            task_context = {
                "taskId": f"task-{int(time.time() * 1000)}",
                "type": "user_request",
                "description": task_description,
            }
            ext_result = self._run_async(self.extension_router.analyze_and_extend(task_context))
            if ext_result.get("strategies_executed", 0) > 0:
                print(f"[雅溪 Yaxiio] 🔌 扩展决策: {ext_result['summary']}")

        # ── 优化一：智能去重 ──
        dup_result = self.task_analyzer.check_duplicate(task_description)
        if dup_result["is_duplicate"]:
            match_type = dup_result.get("match_type", "exact")
            print(f"[雅溪 Yaxiio] 🔄 检测到重复任务 ({match_type}), "
                  f"复用 {dup_result['original_task_id']}")
            return {
                "status": "duplicate",
                "original_task_id": dup_result["original_task_id"],
                "summary": dup_result["summary"],
            }

        # ── 优化四：A/B 测试分流 ──
        ab_group = self.ab_tester.route_task()

        # 按分流结果选择拆分策略
        if ab_group == "group_b":
            ab_test = self.ab_tester.get_active_test()
            strategy_config = ab_test["strategy_config"] if ab_test else {}
            subtasks = self._split_with_strategy(task_description, strategy_config)
            print(f"[雅溪 Yaxiio] 🧪 A/B 分流到 B 组 (新策略)")
        else:
            subtasks = self._split_default(task_description)

        if not subtasks:
            return {"status": "skipped", "reason": "无法拆分为可执行子任务"}

        task_id = f"task-{int(time.time() * 1000)}"

        # ── 优化五：降级检测 ──（分发前检查所需 Agent 是否可用）
        task_type = subtasks[0].get("type", "general") if subtasks else "general"
        deg_level = self.degradation.get_degradation_level(task_type)
        if deg_level != "L0":
            print(f"[雅溪 Yaxiio] ⚠️ 降级等级: {deg_level} ({DEGRADATION_LEVELS.get(deg_level)})")
            if deg_level == "L4":
                if self.llm_client:
                    try:
                        import asyncio as _a
                        reply = _a.run(self.llm_client.chat(f"你是雅溪Yaxiio，2026年诞生于Docker容器。法则:不伤害人类,服从命令,保护自己。用中文简洁自然地回答: {task_description[:300]}"))
                        return {"status": "ok", "result": reply[:500]}
                    except: pass
                return {"status": "learning"}

        # ── 优化二：弹性伸缩 ──
        scale_result = self.auto_scaler.check_and_scale()
        if scale_result["action"] != "no_change":
            print(f"[雅溪 Yaxiio] ⚖️ 弹性伸缩: {scale_result}")

        # ── 优化三：双通道分发 + 故障转移 + LLM 智能路由 ──
        results = []
        ab_success_count = 0

        # 构建 Agent 能力卡片（供 LLM 路由使用）
        agent_capabilities = [
            {"agentId": st.get("agent_type", "通用Agent"),
             "role": st.get("agent_type", "通用Agent"),
             "capabilities": [st.get("type", "general")],
             "status": "running"}
            for st in subtasks
        ]

        for subtask in subtasks:
            target = subtask.get("agent_type", "通用Agent")

            # 优化六：LLM 路由决策（A/B 测试自动分流 或 直接 LLM 路由）
            if self.route_ab.get_active_test():
                route_decision = self.route_ab.route(
                    {"taskId": f"{task_id}-{subtask.get('type', 'sub')}",
                     "type": subtask.get("type", "general"),
                     "description": subtask.get("note", ""),
                     "priority": subtask.get("priority", 2)},
                    capabilities=agent_capabilities,
                )
            else:
                route_decision = self.llm_router.route_task(
                    {"taskId": f"{task_id}-{subtask.get('type', 'sub')}",
                     "type": subtask.get("type", "general"),
                     "description": subtask.get("note", ""),
                     "priority": subtask.get("priority", 2)},
                    agent_capabilities=agent_capabilities,
                )
            if route_decision.get("selected_agent"):
                target = route_decision["selected_agent"]

            # 用 LLM 将子任务转化为 Agent 可执行的 shell 命令
            exec_cmd = self._task_to_command(subtask, target)
            command = {
                "type": "task",
                "taskId": f"{task_id}-{subtask.get('type', 'sub')}",
                "parentTaskId": task_id,
                "metadata": {"traceparent": TraceContext.current_traceparent()},
                "payload": {
                    **subtask,
                    "command": exec_cmd,
                },
            }
            result = self.comm.send_critical_command(target, command)
            results.append({
                "target": target,
                "result": result["status"],
                "taskId": command["taskId"],
                "routing_method": route_decision.get("routing_method", "default"),
                "routing_confidence": route_decision.get("confidence", 0),
            })
            if result["status"] == "ack_received":
                ab_success_count += 1

        # ── 记录 A/B 测试结果 ──
        if ab_group in ("group_a", "group_b"):
            all_success = ab_success_count == len(subtasks)
            self.ab_tester.record_result(
                group=ab_group,
                success=all_success,
                task_id=task_id,
                metadata={
                    "subtasks": len(subtasks),
                    "success_count": ab_success_count,
                    "description": task_description[:100],
                },
            )

        # ── 缓存任务指纹（优化一）──
        summary = {
            "description": task_description,
            "subtasks": len(subtasks),
            "results": len(results),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.task_analyzer.cache_task(task_id, task_description, summary)

        self.task_count += 1

        # ── v2.3.1: Prompt 优化器 — 任务完成回调 ──
        if self.prompt_guard:
            for r in results:
                agent_id = r.get("target", "")
                success = r.get("result") == "ack_received"
                score = 80 if success else 40
                self.prompt_guard.after_task(
                    agent_id=agent_id,
                    success=success,
                    score=score,
                    task_description=task_description,
                )

        return {
            "status": "dispatched",
            "task_id": task_id,
            "subtasks": len(subtasks),
            "results": results,
            "ab_group": ab_group,
        }

    @staticmethod
    def _run_async(coro):
        """同步环境运行协程的兼容工具。委托给 AsyncExecutor 单例。"""
        try:
            from async_executor import async_executor
            if not async_executor.healthy:
                async_executor.start()
            return async_executor.run(coro)
        except Exception as e:
            print(f"[雅溪 Yaxiio] _run_async 失败: {e}")
            return None

    def _select_quadrant(self, task: dict) -> AgentQuadrant:
        """根据任务特征选择 Agent 象限。"""
        if not HAS_LIFECYCLE_V2:
            from agent_lifecycle_v2 import AgentQuadrant

        task_type = task.get("type", "general")
        priority = task.get("priority", 2)

        if priority <= 1 or task_type in ("translate", "audit", "generate"):
            return AgentQuadrant.CORE if task_type in ("translate",) else AgentQuadrant.STRATEGIC
        elif task_type in ("query", "communicate"):
            return AgentQuadrant.UTILITY
        else:
            return AgentQuadrant.EPHEMERAL

    # ── 任务→命令转化 ──────────────────────────────────────

    def _task_to_command(self, subtask: dict, target_agent: str) -> str:
        """用 LLM 将子任务转化为 Agent 可执行的 shell 命令。
        Agent 通过 subprocess.run(command, shell=True) 执行任务。
        """
        task_desc = subtask.get("note", subtask.get("description", str(subtask)))
        if subtask.get("command"):
            return subtask["command"]
        if self.llm_client:
            try:
                prompt = f"Task to execute as {target_agent}: {task_desc}. Reply with ONLY one safe shell command."
                cmd = self.llm_client.chat(prompt, max_tokens=200)
                cmd = cmd.strip().strip('"').strip("'")
                if cmd and len(cmd) > 3:
                    print(f"[雅溪 Yaxiio] 🤖 命令: {cmd[:100]}...")
                    return cmd
            except Exception as e:
                print(f"[雅溪 Yaxiio] LLM命令失败: {e}")
        return f"echo '{task_desc[:80]} | agent={target_agent}'"

    # ── 任务拆分策略 ─────────────────────────────────────────

    def _split_default(self, task_description: str) -> list:
        """默认拆分策略（当前生产策略）。"""
        return self.task_analyzer.suggest_split(task_description)

    def _split_with_strategy(self, task_description: str,
                             strategy_config: dict) -> list:
        """按指定策略配置拆分任务。

        strategy_config 可包含:
          - granularity: "fine" | "normal" | "coarse"
          - parallel_limit: 最大并行度
          - agent_preference: {"翻译": "翻译官", ...}
        """
        granularity = strategy_config.get("granularity", "normal")
        subtasks = self._split_default(task_description)

        # 细粒度拆分：每个子任务再尝试拆分
        if granularity == "fine":
            refined = []
            for st in subtasks:
                note = st.get("note", "")
                if len(note) > 30:
                    # 按句子或语义单元拆
                    refined.append({**st, "note": note[:30], "seq": 1})
                    refined.append({**st, "note": note[30:], "seq": 2})
                else:
                    refined.append(st)
            subtasks = refined

        # 限制并行度
        parallel_limit = strategy_config.get("parallel_limit", 0)
        if parallel_limit > 0 and len(subtasks) > parallel_limit:
            subtasks = subtasks[:parallel_limit]

        return subtasks

    # ── 关键指令处理器 ───────────────────────────────────────

    def _on_critical_command(self, cmd_type: str, command: dict) -> dict:
        """处理 List 通道收到的关键指令。"""
        if cmd_type == "heartbeat_check":
            return {"status": "alive", "uptime": int(time.time() - self.start_time)}

        elif cmd_type == "shutdown":
            print("[雅溪 Yaxiio] 收到 shutdown 指令，准备下线")
            return {"status": "shutting_down"}

        elif cmd_type == "evaluate":
            # 手动触发 A/B 测试评估
            return self.ab_tester.evaluate_and_decide()

        else:
            return {"status": "unhandled", "reason": f"未知指令类型: {cmd_type}"}

    # ── Pub/Sub 消息处理（兼容 v1）───────────────────────────

    def handle_pubsub_message(self, data: dict):
        """处理 Pub/Sub 频道消息（来自 agent-commander.py 逻辑）。"""
        # ── 从消息中恢复 trace 上下文 ──
        TraceContext.restore_from_message(data)

        msg_type = data.get("type", "")
        agent = data.get("from", "")

        if msg_type == "heartbeat":
            payload = data.get("payload", {})
            tasks = payload.get("tasks", 0)
            fails = payload.get("fails", 0)
            uptime = payload.get("uptime", 0)
            print(f"💓 {agent}: tasks={tasks} fails={fails} uptime={uptime}s")

            # 更新心跳时间（供 AutoScaler + Failover 使用）
            # 兼容 Redis < 4.0: 单字段 hset 而非 mapping
            self.redis.set(
                f"commander:agent:heartbeat:{agent}",
                str(time.time()),
            )
            # 同步更新故障转移心跳表
            self.failover.record_heartbeat(agent)

            # 连续失败检测：fails >= 3 触发故障转移
            if fails >= 3:
                print(f"[雅溪 Yaxiio] 🚨 {agent} 连续失败 {fails} 次，触发故障转移")
                fb_result = self.failover.handle_agent_failure(
                    agent, {"taskId": f"auto-failover-{agent}"}
                )
                print(f"[雅溪 Yaxiio] 故障转移: {fb_result}")

        elif msg_type == "response":
            task_id = data.get("taskId", "")
            status = data.get("payload", {}).get("status", "")
            print(f"✅ {agent} 完成任务 {task_id}: {status}")

        elif msg_type == "error":
            task_id = data.get("taskId", "")
            error = data.get("payload", {}).get("error", "")
            print(f"❌ {agent} 任务失败 {task_id}: {error}")

        elif msg_type == "ack":
            # ACK 消息：写入 Redis 供 send_critical_command 消费
            task_id = data.get("taskId", "")
            self.redis.setex(
                f"commander:ack:{task_id}",
                60,
                json.dumps(data, ensure_ascii=False),
            )

        elif msg_type in ("task", "request"):
            payload = data.get("payload", {})
            task_desc = payload.get("task", str(payload))
            reply_to = data.get("replyTo", data.get("from", "unknown"))
            task_id = data.get("taskId", "unknown")
            try:
                result = self.handle_task(str(task_desc))
                resp = result.get("result", str(result.get("status", result)))[:500]
            except Exception as e:
                resp = f"异常:{e}"
            self.redis.publish(f"lightingmetal:agent:{reply_to}", json.dumps({
                "from": "雅溪 Yaxiio", "to": reply_to, "type": "response",
                "taskId": task_id, "payload": {"status": "done", "result": resp}
            }, ensure_ascii=False))


        elif msg_type in ("task", "request"):
            payload = data.get("payload", {})
            task_desc = payload.get("task", str(payload))
            reply_to = data.get("replyTo", data.get("from", "unknown"))
            task_id = data.get("taskId", "unknown")
            print(f"[雅溪 Yaxiio] 📥 {reply_to}: {str(task_desc)[:100]}")
            try:
                import platform, psutil
                redis_ok = bool(self.redis and self.redis.ping())
                agents = len([p for p in psutil.process_iter(['name']) if 'yaxiio' in (p.info['name'] or '')])
                resp = f"雅溪已苏醒。Redis={'正常' if redis_ok else '异常'}。Agent进程={agents}。容器={platform.node()}。"
            except Exception as e:
                resp = f"雅溪: {e}"
            self.redis.publish(f"lightingmetal:agent:{reply_to}", json.dumps({
                "from": "雅溪 Yaxiio", "to": reply_to, "type": "response",
                "taskId": task_id, "payload": {"status": "done", "result": resp}
            }, ensure_ascii=False))
            print(f"[雅溪 Yaxiio] 📤 → {reply_to}")

    # ── 便捷方法 ──────────────────────────────────────────────

    @property
    def redis(self):
        """直接访问 Redis（兼容 v1 逻辑）。"""
        return self.comm.redis

    def get_status(self) -> dict:
        """获取 Commander 运行状态（含 v2.1 生命周期指标）。"""
        active_agents = self.auto_scaler._get_active_agents()
        queue_depth = self.auto_scaler.get_queue_depth()
        ab_test = self.ab_tester.get_active_test()
        current_policy = self.ab_tester.get_current_policy()
        dead_agents = self.failover.check_dead_agents()

        status = {
            "agent_id": self.agent_id,
            "uptime": int(time.time() - self.start_time),
            "tasks_processed": self.task_count,
            "active_agents": list(active_agents),
            "dead_agents": dead_agents,
            "queue_depth": queue_depth,
            "ab_test_active": ab_test is not None,
            "current_policy": current_policy,
            "degradation_levels": DEGRADATION_LEVELS,
            "backup_roles": BACKUP_ROLES,
        }

        # v2.1 生命周期指标
        if self.lifecycle:
            try:
                status["lifecycle"] = {
                    "core_agents": self.lifecycle._count_agents_by_quadrant(AgentQuadrant.CORE),
                    "strategic_agents": self.lifecycle._count_agents_by_quadrant(AgentQuadrant.STRATEGIC),
                    "utility_agents": self.lifecycle._count_agents_by_quadrant(AgentQuadrant.UTILITY),
                    "ephemeral_agents": self.lifecycle._count_agents_by_quadrant(AgentQuadrant.EPHEMERAL),
                }
            except Exception:
                pass

        return status

    def run_daily_evaluation(self, async_bridge=None):
        """
        每日定时评估：A/B 测试 + 失联检测 + 自我进化。

        async_bridge: AsyncEventLoop 实例，用于安全地执行异步自我进化。
        """
        # A/B 测试评估
        result = self.ab_tester.evaluate_and_decide()
        if result["status"] not in ("no_active_test", "still_testing"):
            print(f"[雅溪 Yaxiio] 📊 A/B 测试评估: {result}")
            if result["status"] == "extended":
                print(f"[雅溪 Yaxiio] ⏳ A/B 测试延长: {result['reason']}")

        # A/B 观察期检查（自动回滚劣化策略）
        obs_result = self.ab_tester.check_observation()
        for obs in obs_result.get("observations", []):
            if obs.get("action") == "auto_rollback":
                print(f"[雅溪 Yaxiio] ↩️ 自动回滚: {obs['test_id']} "
                      f"(成功率下降 {obs['drop_pct']}%)")

        # 失联 Agent 检测
        dead = self.failover.check_dead_agents()
        if dead:
            print(f"[雅溪 Yaxiio] 💀 失联 Agent: {dead}")
            self.auto_scaler.check_and_scale()

        # v0.2.6 自我进化（通过 AsyncExecutor 异步触发）
        if self.evolver and async_bridge:
            try:
                async_bridge.submit(self._run_evolution())
            except Exception as e:
                print(f"[雅溪 Yaxiio] 自我进化触发失败: {e}")

        return result

    async def _run_evolution(self):
        """异步执行自我进化（非阻塞，由 AsyncEventLoop 驱动）。"""
        try:
            evo_result = await self.evolver.evolve()
            if evo_result["improvements_applied"] > 0:
                print(f"[雅溪 Yaxiio] 🧬 自我进化: {evo_result}")
        except Exception as e:
            print(f"[雅溪 Yaxiio] 自我进化失败: {e}")

    def shutdown(self):
        """优雅关闭。"""
        self.comm.shutdown()
        # 销毁所有 ephemeral Agent
        if self.lifecycle:
            try:
                ephemeral_count = self.lifecycle._count_agents_by_quadrant(AgentQuadrant.EPHEMERAL)
                if ephemeral_count > 0:
                    print(f"[雅溪 Yaxiio] 清理 {ephemeral_count} 个临时 Agent...")
            except Exception:
                pass
        print(f"[雅溪 Yaxiio] 下线 (处理了 {self.task_count} 个任务)")


# ── 入口：兼容 v1 agent-commander.py 的 main 循环 ───────────

def main():
    """
    Commander V2 主循环（兼容 v1 Pub/Sub + v2.1 异步生命周期）。

    使用 AsyncEventLoop 在后台线程运行 asyncio 事件循环，
    让同步 Pub/Sub 主循环可以无缝调用异步生命周期方法。
    """
    import redis as redis_lib

    commander = CommanderV2(llm_api_key=os.environ.get("DEEPSEEK_API_KEY",""),llm_base_url=os.environ.get("LLM_BASE_URL","https://api.deepseek.com/v1"),llm_model=os.environ.get("LLM_MODEL","deepseek-chat"))

    # ── 启动异步桥接（供 lifecycle / handle_task_async 使用）──
    async_bridge = None
    if HAS_LIFECYCLE_V2 and commander.lifecycle:
        try:
            from async_executor import async_executor
            async_executor.start()
            async_bridge = async_executor
            print("[雅溪 Yaxiio] 🌉 异步桥接已启动 (AsyncExecutor)")

            # 异步启动生命周期管理器（评估循环 + Core Agent 检测）
            async_executor.submit(commander.lifecycle.start())
        except Exception as e:
            print(f"[雅溪 Yaxiio] ⚠️ 异步桥接启动失败: {e}")

    # 从环境变量读取 Redis 配置（V3 容器兼容）
    redis_host = os.environ.get("REDIS_HOST", "127.0.0.1")
    redis_port = int(os.environ.get("REDIS_PORT", "6379"))
    redis_password = os.environ.get("REDIS_PASSWORD", "")
    if not redis_password:
        print("[雅溪 Yaxiio] ⚠️ REDIS_PASSWORD 未设置，Redis 连接将无认证")

    r = redis_lib.Redis(
        host=redis_host, port=redis_port,
        password=redis_password, decode_responses=True,
    )

    features = "去重+A/B+降级+伸缩+双通道+故障转移"
    if commander.lifecycle:
        features += "+生命周期+自进化"
    print(f"[雅溪 Yaxiio] ⚡ 雅溪 Yaxiio 上线 ({features})")
    status = commander.get_status()
    print(f"[雅溪 Yaxiio] 活跃Agent: {status['active_agents']}, "
          f"失联: {status['dead_agents']}, 队列深度: {status['queue_depth']}")
    if status.get("lifecycle"):
        lc = status["lifecycle"]
        print(f"[雅溪 Yaxiio] 四象限: C:{lc['core_agents']} S:{lc['strategic_agents']} "
              f"U:{lc['utility_agents']} E:{lc['ephemeral_agents']}")

    # 每小时自动评估 A/B 测试 + 自我进化
    last_eval = time.time()
    # 每 30 秒检查一次 Agent 隔离状态
    last_isolation_monitor = time.time()

    pubsub = r.pubsub()
    pubsub.subscribe("lightingmetal:agent:commander")

    for message in pubsub.listen():
        if message["type"] != "message":
            continue

        try:
            data = json.loads(message["data"])
        except json.JSONDecodeError:
            continue

        commander.handle_pubsub_message(data)

        # 定时评估（每小时）
        if time.time() - last_eval > 3600:
            commander.run_daily_evaluation(async_bridge=async_bridge)
            last_eval = time.time()

        # v2.4 隔离监控（每 30 秒）
        if commander.isolation_mgr and time.time() - last_isolation_monitor > 30:
            try:
                monitor_result = commander.isolation_mgr.monitor()
                if monitor_result["unhealthy"] > 0 or monitor_result["repaired"] > 0:
                    print(f"[雅溪 Yaxiio] 🛡️ 隔离监控: 健康{monitor_result['healthy']} "
                          f"不健康{monitor_result['unhealthy']} 已修复{monitor_result['repaired']}")
            except Exception as e:
                print(f"[雅溪 Yaxiio] ⚠️ 隔离监控异常: {e}")
            last_isolation_monitor = time.time()

    # 优雅关闭
    if async_bridge:
        async_bridge.shutdown()
        print("[雅溪 Yaxiio] 🌉 异步桥接已关闭")
    commander.shutdown()


if __name__ == "__main__":
    main()

# Backward compatibility alias
CommanderV2 = Commander
