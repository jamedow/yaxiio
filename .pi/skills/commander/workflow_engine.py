"""Commander 流程引擎 v2.1 — 多子任务编排
===========================================
  L1 感知: MCP关键词 + LLM深度理解 + 动作优先覆盖
  L2 规划: 简单任务→Arsenal匹配, 复杂任务→LLM拆解为子任务DAG
  L3 调度: spawn_neuron + 任务分发 + 轮询等待结果
  L4 执行: 沙箱隔离 + Arsenal调用
  L5 评估: LLM四维评分 + 低分进化标记
"""
import json, time, threading, os, tempfile, subprocess, shutil, uuid
from mcp_bridge import call_layer
from mcp.protocol import MCPClient

# 状态机
from task_state_machine import TaskStateMachine
from trace_logger import TraceLogger
from modules.shared.config import MCP_LAYERS_ENABLED
from tools.hybrid_scorer import HybridScorer
from workflow_snapshot import WorkflowSnapshot, SchemaValidator

MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_CLIENTS = {i: MCPClient(f"http://{MCP_HOST}:{3400+i}") for i in range(1, 6)}

SANDBOX_BASE = "/tmp/yaxiio-sandbox"
SANDBOX_MAX_SIZE_MB = 500
SANDBOX_TIMEOUT = 300
POLL_TIMEOUT = 120
POLL_INTERVAL = 2

# ── 意图映射表 ──
INTENT_TOOL_MAP = {
    "audit":      {"tool": None,   "agent": "审计官",    "desc": "代码审计",   "complex":True},
    "diagnose":   {"tool": None,     "agent": "审计官",    "desc": "问题诊断"},
    "fix":        {"tool": None,     "agent": "审计官",    "desc": "修复代码"},
    "drill":      {"tool": None,    "agent": "审计官",    "desc": "沙箱演习"},
    "evolve":     {"tool": None,      "agent": "审计官",    "desc": "代码进化"},
    "deploy":     {"tool": None,     "agent": "售前经理",  "desc": "构建部署"},
    "build":      {"tool": None,     "agent": "售前经理",  "desc": "项目构建"},
    "translate":  {"tool": None,  "agent": "翻译官",    "desc": "多语翻译"},
    "quote":      {"tool": None,               "agent": "售前经理",  "desc": "报价生成"},
    "search":     {"tool": None,               "agent": "售前经理",  "desc": "产品搜索"},
    "optimize":   {"tool": None,     "agent": "审计官",    "desc": "性能优化"},
    "oom":        {"tool": "llm_diagnose",     "agent": "审计官",    "desc": "内存诊断"},
    "i18n":       {"tool": "translate_start",  "agent": "翻译官",    "desc": "国际化修复"},
    "layout":     {"tool": "fix_codebase",     "agent": "审计官",    "desc": "布局修复"},
    "generate":   {"tool": None,     "agent": "售前经理",  "desc": "内容生成"},
    "design":     {"tool": None,               "agent": "UI/UX设计师","desc": "设计任务"},
    "redesign":   {"tool": None,               "agent": "UI/UX设计师","desc": "重设计",  "complex":True},
    "content_fix":{"tool": None,               "agent": "审计官",   "desc": "内容修复",  "complex":True},
    "fix_codebase":{"tool": None,     "agent": "审计官",    "desc": "代码修复",  "complex":True},
    "translate_all_pages":{"tool": None, "agent": "翻译官",  "desc": "批量翻译",  "complex":True},
    "brand":      {"tool": None,               "agent": "品牌策略师", "desc": "品牌策略"},
    "frontend":   {"tool": None,               "agent": "前端工程师", "desc": "前端开发"},
    "ux":         {"tool": None,               "agent": "UI/UX设计师","desc": "UX设计"},
    "ui":         {"tool": None,               "agent": "UI/UX设计师","desc": "UI设计"},
}

SANDBOX_REQUIRED_ACTIONS = {"site_build", "site_deploy", "site_fix",
                            "build_deploy", "fix_codebase", "evolve_code"}

# ── 复杂任务预设模板 ──
COMPLEX_TASK_TEMPLATES = {}  # LLM自主拆解, 无硬编码模板




class WorkflowEngine:
    """LLM 驱动的五层流程编排引擎 v2.1 — 支持多子任务编排"""

    def __init__(self, commander=None):
        self.commander = commander
        self.sm = TaskStateMachine()  # 状态机
        self.log = TraceLogger("WorkflowEngine")
        self.active: dict = {}
        self._lock = threading.Lock()
        self.task_count = 0
        self._count_lock = threading.Lock()
        self.score_history: list = []
        self.snapshot = WorkflowSnapshot()  # L3: cross-subtask data relay
        self.hybrid_scorer = HybridScorer()  # Phase 3: human-in-the-loop
        from l0_memory import L0Memory
        self.l0 = L0Memory(redis_client=None)  # Will use Redis from Commander
        from gap_analyzer import GapAnalyzer
        self.gap = GapAnalyzer()

        # ── L2: Semantic Intent Router (replaces INTENT_TOOL_MAP) ──
        from modules.layer2.intent_router import SemanticIntentRouter
        try:
            from modules.layer1.vector_store_chroma import ChromaVectorStore
            _vs = ChromaVectorStore()
        except Exception:
            from modules.layer1.vector_store import MemVectorStore
            _vs = MemVectorStore()
        self.intent_router = SemanticIntentRouter(
            vector_store=_vs,
            redis_client=self.commander.redis if self.commander else None
        )

        # ── L2: Intelligent Model Router ──
        from modules.layer2.model_router_v2 import IntelligentModelRouter
        self.model_router_v2 = IntelligentModelRouter(
            redis_client=self.commander.redis if self.commander else None
        )

        # ── L3: Async Orchestrator + Redis Data Bus ──
        from modules.layer3.async_orchestrator import AsyncOrchestrator
        from modules.layer3.redis_data_bus import RedisDataBus
        self.async_orch = AsyncOrchestrator(
            commander=self.commander,
            max_concurrent=int(os.environ.get("YAXIIO_MAX_CONCURRENT", "10")),
            total_timeout=float(os.environ.get("YAXIIO_TASK_TIMEOUT", "600")),
            subtask_timeout=float(os.environ.get("YAXIIO_SUBTASK_TIMEOUT", "120")),
        )
        self.data_bus = RedisDataBus(
            redis_client=self.commander.redis if self.commander else None
        )

        # ── L5: Experience Flywheel ──
        from modules.layer5.experience_flywheel import ExperienceFlywheel
        try:
            from modules.layer1.vector_store_chroma import ChromaVectorStore
            _fw_vs = ChromaVectorStore()
        except Exception:
            from modules.layer1.vector_store import MemVectorStore
            _fw_vs = MemVectorStore()
        self.flywheel = ExperienceFlywheel(
            redis_client=self.commander.redis if self.commander else None,
            vector_store=_fw_vs
        )

    def _agent_skill_map(self) -> dict:
        """Dynamic agent→skill mapping from Redis capability cards.
        Falls back to hardcoded map if Redis unavailable."""
        if getattr(self, "_cached_skill_map", None):
            return self._cached_skill_map

        _map = {}
        try:
            if self.commander and self.commander.redis:
                agents = self.commander.redis.smembers("agent:registry") or []
                for name in agents:
                    card_raw = self.commander.redis.get(f"agent:card:{name}")
                    if card_raw:
                        card = json.loads(card_raw)
                        skills = card.get("skills", [])
                        _map[name] = skills[0] if skills else ""
        except Exception:
            pass

        # Merge with hardcoded fallbacks for agents not yet in Redis
        _fallback = {
            "UI/UX设计师": "ui-ux-designer",
            "品牌策略师": "strategic-partner",
            "前端工程师": "infrastructure-engineer",
            "翻译官": "translate-engine",
            "审计官": "audit-engine",
            "售前经理": "product-search",
            "商务经理": "product-search",
            "通用Agent": "",
            "修复Agent": "backend-engineer",
            "系统医生": "system-doctor",
            "LM内容工程师": "lm-content-engineer",
        }
        for k, v in _fallback.items():
            if k not in _map:
                _map[k] = v

        self._cached_skill_map = _map
        return _map



    def process(self, task_id: str, payload: dict):
        if MCP_LAYERS_ENABLED.get("L1"):
            return {"mcp_routed": True, "layer": "L1", "phase": "not_implemented", "task_id": task_id}

        """入口: 慢思考策略 → 复杂度判断 → 分流程"""
        action = payload.get("action", "unknown")
        action_clean = action.replace("site_", "").replace("translate_", "")

        # ── 任务侦察: 探测体量、范围、复杂度，决定后续策略 ──
        if not payload.get("_is_sample") and not payload.get("_skip_recon"):
            try:
                from task_recon import TaskReconnaissance
                recon = TaskReconnaissance(self.commander)
                recon_report = recon.scout(task_id, payload)
                payload["_recon"] = recon_report.to_dict()
                recs = recon_report.recommendations
                if recs.get("suggested_timeout"):
                    payload["_suggested_timeout"] = recs["suggested_timeout"]
                if recs.get("max_concurrent"):
                    payload["_suggested_concurrent"] = recs["max_concurrent"]
                if recs.get("chunk_size"):
                    payload["_chunk_size"] = recs["chunk_size"]

                # ── 预热门控: 大任务先小样本试跑找最优策略 ──
                from warmup_gate import WarmupGate
                gate = WarmupGate(self)
                if gate.should_warmup(payload["_recon"]):
                    # 策略锦标赛 (默认) 或 预热 (串行)
                    use_tournament = os.environ.get("YAXIIO_TOURNAMENT", "true").lower() == "true"
                    if use_tournament:
                        warmup_result = gate.tournament(task_id, payload, payload["_recon"])
                    else:
                        warmup_result = gate.warmup(task_id, payload, payload["_recon"])
                    payload["_warmup"] = warmup_result.to_dict()
                    if warmup_result.passed and warmup_result.best_strategy:
                        # 把最优策略注入 payload
                        payload = warmup_result.best_strategy.apply_to_payload(payload)
                        print(f"[WF] {task_id} 预热通过, 策略: {warmup_result.best_strategy.to_dict()}", flush=True)
                    else:
                        print(f"[WF] {task_id} 预热未通过, 仍继续执行 (score={warmup_result.sample_score})", flush=True)
            except Exception as e:
                print(f"[WF] {task_id} 预热异常 ({e}), 回退正常流程", flush=True)

        intent_info = INTENT_TOOL_MAP.get(action_clean) or INTENT_TOOL_MAP.get(action, {})
        is_complex = intent_info.get("complex", False) or len(str(payload.get("task", ""))) > 150 or any(n in str(payload.get("task","")).lower() for n in ["split","batch","parallel","entries","pending"])

        if is_complex:
            return self._process_complex(task_id, payload)
        return self._process_simple(task_id, payload)

    # ═══════════════════════════════════════════════
    # 简单任务流程 (原有逻辑)
    # ═══════════════════════════════════════════════

    def _process_simple(self, task_id: str, payload: dict):
        """单任务 L1→L5 流程"""
        with self._lock:
            self.active[task_id] = {"task_id": task_id, "started_at": time.time()}
        with self._count_lock:
            self.task_count += 1

        action = payload.get("action", "unknown")
        arsenal_tools = payload.get("_arsenal_tools", [])
        state = {"task_id": task_id, "status": "RUNNING", "action": action}

        # 状态机: 创建
        self.sm.create(task_id, action, str(payload.get("task", ""))[:80])
        self.sm.start_layer(task_id, "L1_perception")

        try:
            # L1
            state.update(self._do_L1(task_id, payload))
            self.sm.complete_layer(task_id, "L1_perception",
                                   {"intent": state.get("primary_intent"), "conf": state.get("confidence")})
            self.sm.start_layer(task_id, "L2_planning")

            # L2
            plan = self._do_L2(task_id, payload, state, arsenal_tools)
            state["plan"] = plan
            self.sm.complete_layer(task_id, "L2_planning", {"agent": plan.get("agent")})
            self.sm.start_layer(task_id, "L3_dispatch")

            # L3 + L4
            l4 = self._do_L3_L4(task_id, payload, plan, state)
            state["l4_result"] = l4
            self.sm.complete_layer(task_id, "L3_dispatch")
            self.sm.start_layer(task_id, "L4_execution")
            self.sm.complete_layer(task_id, "L4_execution", {"status": l4.get("status", "?")})
            self.sm.start_layer(task_id, "L5_evaluation")

            # L5
            state["l5_result"] = self._do_L5(task_id, action, plan, l4, state)
            self.sm.complete_layer(task_id, "L5_evaluation",
                                   {"score": state["l5_result"].get("overall")})

            # ── 重试策略 ──
            l5_first = state["l5_result"]
            if l5_first.get("verdict") == "retry" and not state.get("_retried"):
                self.log.warn("_process_simple", "L5触发重试", trace_id=task_id, reason="low_score")
                retry_payload = dict(payload)
                retry_payload["_thinking"] = self._bump_thinking(state.get("_last_thinking", "medium"))
                state["_retried"] = True
                state["_retry_thinking"] = retry_payload["_thinking"]
                l4_retry = self._do_L3_L4(task_id, retry_payload, plan, state)
                state["l4_retry"] = l4_retry
                state["l5_result"] = self._do_L5(task_id, action, plan, l4_retry, state)
                self.sm.complete_layer(task_id, "L4_execution", {"retried": True})
                self.sm.complete_layer(task_id, "L5_evaluation",
                                       {"score": state["l5_result"].get("overall"), "retried": True})
            elif l5_first.get("verdict") == "reject":
                # 研究+重试: 网上找资料 → 增强 → 再跑一次
                self.log.warn("_process_simple", "L5触发研究重试", trace_id=task_id, reason="rejected")
                issues_str = "; ".join(l5_first.get("key_issues", [])[:3])
                try:
                    research = call_layer(5, "research_and_retry",
                                         task_id=task_id, action=action,
                                         agent_name=plan.get("agent", ""),
                                         issues=issues_str,
                                         topic=payload.get("task", action)[:100])
                    if research.get("retry"):
                        retry_payload = dict(payload)
                        retry_payload["_thinking"] = "high"
                        retry_payload["_reference"] = research.get("research_findings", "")[:500]
                        state["_retried"] = True
                        state["_research_backed"] = True
                        l4_retry = self._do_L3_L4(task_id, retry_payload, plan, state)
                        state["l4_retry"] = l4_retry
                        state["l5_result"] = self._do_L5(task_id, action, plan, l4_retry, state)
                        print(f"[WF] {task_id} 研究后重试 L5={state['l5_result'].get('overall','?')}", flush=True)
                    else:
                        state["_needs_doctor"] = True
                        print(f"[WF] {task_id} 研究失败, 标记医生诊断", flush=True)
                except Exception as e:
                    state["_needs_doctor"] = True
                    print(f"[WF] {task_id} 研究异常: {e}", flush=True)

            state["status"] = "DONE"
            self.sm.transition(task_id, "DONE")
        except Exception as e:
            state["status"] = "FAILED"
            state["error"] = str(e)[:500]
            self.sm.transition(task_id, "FAILED", error=str(e)[:200])

        with self._lock:
            self.active.pop(task_id, None)
        state["completed_at"] = time.time()
        print(f"[WF] {task_id} → {state['status']}", flush=True)
        return state

    # ═══════════════════════════════════════════════
    # 复杂任务流程 — 多子任务编排
    # ═══════════════════════════════════════════════

    def _process_complex(self, task_id: str, payload: dict):
        """复杂任务: 拆解→编排→分发→收集→评分"""
        with self._lock:
            self.active[task_id] = {"task_id": task_id, "started_at": time.time()}
        with self._count_lock:
            self.task_count += 1

        action = payload.get("action", "unknown")
        action_clean = action.replace("site_", "").replace("translate_", "")
        state = {"task_id": task_id, "status": "RUNNING", "action": action, "subtask_results": {}}

        # 状态机: 创建任务
        self.sm.create(task_id, action, str(payload.get("task", ""))[:80])
        self.sm.start_layer(task_id, "L1_perception")

        try:
            # L1: 意图识别
            state.update(self._do_L1(task_id, payload))
            self.sm.complete_layer(task_id, "L1_perception",
                                   {"intent": state.get("primary_intent"), "conf": state.get("confidence")})
            self.sm.start_layer(task_id, "L2_planning")

            # L2: 任务拆解 (优先 MCP, 降级 LLM)
            print(f"[WF] {task_id} L2 planning via MCP...", flush=True)
            subtasks = self._decompose_via_l2(task_id, payload)
            state["subtasks"] = subtasks
            state["template_used"] = "l2_mcp" if len(subtasks) > 1 else "llm_fallback"
            self.sm.complete_layer(task_id, "L2_planning",
                                   {"subtask_count": len(subtasks), "template": state["template_used"]})

            # === Agent 克隆窗口: L2完成后、L3调度前 ===
            self.sm.start_layer(task_id, "L3_agent_clone")
            print(f"[WF] {task_id} cloning {len(set(s['agent'] for s in subtasks))} agent types...", flush=True)
            cloned_agents = self._clone_agents_for_task(task_id, subtasks)
            # 将克隆后的内存 key 写回 plan
            for st in subtasks:
                sid = st["id"]
                agent = st["agent"]
                st["session_memory_key"] = f"agent:{agent}:{task_id}:memory"
                st["neuron_spawned"] = cloned_agents.get(agent, False)
            self.sm.complete_layer(task_id, "L3_agent_clone",
                                   {"cloned": len(cloned_agents), "agents": list(cloned_agents.keys())})

            # L3: 通过 MCP 协调层调度
            self.sm.start_layer(task_id, "L3_dispatch")
            print(f"[WF] {task_id} L3 scheduling {len(subtasks)} subtasks via MCP...", flush=True)
            schedule = self._schedule_via_l3(task_id, subtasks)
            state["l3_schedule"] = schedule
            self.sm.complete_layer(task_id, "L3_dispatch",
                                   {"agents": list(set(s["agent"] for s in subtasks)),
                                    "assigned": schedule.get("total_assigned", 0)})

            # L4: 按依赖关系编排执行
            self.sm.start_layer(task_id, "L4_execution")
            print(f"[WF] {task_id} L4 executing {len(subtasks)} subtasks...", flush=True)
            results = self._orchestrate_subtasks(task_id, subtasks, payload)
            state["subtask_results"] = results
            self.sm.complete_layer(task_id, "L4_execution",
                                   {"done": sum(1 for r in results.values() if r.get("ok")),
                                    "total": len(subtasks)})

            # ── 故障检测: 连续失败 > 2 → 派系统医生 ──
            self._check_and_heal(task_id, subtasks, results)

            # ── 子任务重试: 失败的 subtask 升级 thinking 重跑一次 ──
            for st in subtasks:
                sid = st["id"]
                r = results.get(sid, {})
                if not r.get("ok") and not r.get("_retried"):
                    print(f"[WF] {task_id}/{sid} 失败→升级 thinking 重试", flush=True)
                    retry_st = dict(st)
                    retry_st["_retried"] = True
                    retry_payload = dict(payload)
                    retry_payload["_thinking"] = "high"
                    retry_result = self._execute_subtask(task_id, sid, retry_st, retry_payload)
                    if retry_result.get("ok"):
                        results[sid] = retry_result
                        results[sid]["_retried"] = True
                        print(f"[WF] {task_id}/{sid} 重试成功 ✅", flush=True)
                    else:
                        results[sid]["_retried"] = True
                        print(f"[WF] {task_id}/{sid} 重试仍失败 ❌ → 标记医生诊断", flush=True)
                        state["_needs_doctor"] = True

            # === 目标自检循环: 执行→评估→差距→继续 ===
            MAX_ROUNDS = 3
            round_num = 1
            all_results = dict(results)
            all_subtasks = list(subtasks)
            final_score = 0

            while round_num <= MAX_ROUNDS:
                self.sm.start_layer(task_id, "L5_evaluation")

                summary = self._summarize_results(task_id, all_subtasks, all_results)
                state["summary"] = summary

                state["l5_result"] = self._do_L5(task_id, action, {"subtasks": all_subtasks},
                                                 {"results": all_results, "summary": summary}, state)
                score = state["l5_result"].get("overall", 5)
                needs_review = state["l5_result"].get("needs_review", False)
                final_score = score

                self.sm.complete_layer(task_id, "L5_evaluation",
                                       {"score": score, "round": round_num})

                goal_met = score >= 7 and not needs_review
                # Verify: re-run content audit if it was an audit/fix task
                if "audit" in action_clean or "fix" in action_clean:
                    try:
                        import subprocess as _sp
                        audit = _sp.run(["python3", "/opt/commander/tools/multilang_audit.py"],
                                       capture_output=True, text=True, timeout=30)
                        if "混杂" in audit.stdout:
                            import re
                            nums = re.findall(r"混杂(\d+)", audit.stdout)
                            remaining = int(nums[0]) if nums else 0
                            state["remaining_issues"] = remaining
                            if remaining > 100:
                                goal_met = False
                                print("[WF] %s re-audit: %d issues remain -> continue" % (task_id, remaining), flush=True)
                    except Exception as e:
                        print("[WF] %s re-audit error: %s" % (task_id, str(e)[:50]), flush=True)
                print(f"[WF] {task_id} Round {round_num} L5={score} goal_met={goal_met}", flush=True)

                if goal_met or round_num >= MAX_ROUNDS:
                    break

                # 差距分析: 问 L5「任务目标还差什么？」
                print(f"[WF] {task_id} 目标未达成(score={score}), 差距分析...", flush=True)
                gap = self._analyze_gap(task_id, payload, all_results, state["l5_result"])
                if not gap.get("has_gap") or not gap.get("next_actions"):
                    break

                print(f"[WF] {task_id} gap: {gap.get('gap_summary','?')[:120]}", flush=True)

                # L0: Check if web search can help fill knowledge gaps
                web_need = self.l0._should_search_web(state["l5_result"], action_clean)
                if web_need.get("should_search"):
                    print(f"[L0] {task_id} gap detected, searching web...", flush=True)
                    for query in web_need.get("queries", [])[:2]:
                        try:
                            web_result = call_layer(5, "web_research", topic=query[:80], context=str(payload.get("task",""))[:200], depth="quick")
                            if web_result.get("status") == "success":
                                findings = web_result.get("findings", "")[:1000]
                                self.l0._save_web_knowledge(action_clean, query[:40], [findings] if findings else [], domain="tech_spec")
                                gap["_web_findings"] = findings[:500]
                                print(f"[L0] {task_id} web knowledge saved", flush=True)
                        except Exception as e:
                            print(f"[L0] {task_id} web error: {e}", flush=True)

                # 根据差距生成下一轮子任务
                round_num += 1
                next_subtasks = self._gap_to_subtasks(task_id, gap, payload, round_num)
                if not next_subtasks:
                    break

                # 执行下一轮
                print(f"[WF] {task_id} Round {round_num}: {len(next_subtasks)} sub-tasks", flush=True)
                round_results = self._orchestrate_subtasks(task_id, next_subtasks, payload)
                all_subtasks.extend(next_subtasks)
                all_results.update(round_results)

            state["status"] = "DONE"
            state["total_rounds"] = round_num
            state["final_score"] = final_score
            self.sm.transition(task_id, "DONE")
            # Template clone: merge improvements, destroy task memory
            self._cleanup_task(task_id, all_subtasks, final_score)
        except Exception as e:
            state["status"] = "FAILED"
            state["error"] = str(e)[:500]
            self.sm.transition(task_id, "FAILED", error=str(e)[:200])
            import traceback
            state["traceback"] = traceback.format_exc()[-500:]

        with self._lock:
            self.active.pop(task_id, None)
        state["completed_at"] = time.time()

        done_count = sum(1 for r in state.get("subtask_results", {}).values() if r.get("ok"))
        print(f"[WF] {task_id} → {state['status']} ({done_count}/{len(subtasks)} subtasks done)", flush=True)
        return state

    def _orchestrate_subtasks(self, task_id: str, subtasks: list, payload: dict) -> dict:
        if MCP_LAYERS_ENABLED.get("L3"):
            return {"mcp_routed": True, "layer": "L3", "phase": "not_implemented"}

        # ── Async path (feature-flagged) ──
        _use_async = os.environ.get("YAXIIO_ASYNC_ORCHESTRATOR", "true").lower() == "true"
        if _use_async and hasattr(self, "async_orch") and self.async_orch:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(
                    self.async_orch.execute(task_id, subtasks, payload)
                )
                loop.close()
                # Write results to data_bus
                if hasattr(self, "data_bus") and self.data_bus:
                    for sid, r in results.items():
                        self.data_bus.put(task_id, sid, r)
                print(f"[WF] {task_id} AsyncOrchestrator: {len(results)} results", flush=True)
                return results
            except Exception as e:
                print(f"[WF] {task_id} AsyncOrchestrator failed ({e}), fallback to thread pool", flush=True)

        # ── Thread pool fallback (existing logic) ──

        """并行编排: 无依赖的子任务同时发射，依赖满足后立即启动"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results = {}
        pending = {}  # sid -> Future
        completed = set()
        dispatches = {}  # sid -> subtask info

        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="wf-sub") as executor:
            deadline = time.time() + POLL_TIMEOUT

            while len(completed) < len(subtasks) and time.time() < deadline:
                # 找出所有就绪的子任务（依赖满足 + 未分派 + 未完成）
                ready = []
                for st in subtasks:
                    sid = st["id"]
                    if sid in completed or sid in pending:
                        continue
                    deps_met = all(d in completed for d in st.get("depends", []))
                    if deps_met:
                        ready.append(st)

                # 并行发射所有就绪子任务
                for st in ready:
                    sid = st["id"]
                    print(f"[WF] {task_id}/{sid} → {st['agent']} ({st['action']})", flush=True)
                    dispatches[sid] = st
                    future = executor.submit(self._execute_subtask, task_id, sid, st, payload)
                    pending[sid] = future

                # 如果没有就绪任务且也没有在运行的任务，退出
                if not ready and not pending:
                    break

                # 等待任意一个完成
                if pending:
                    done_futures = []
                    poll_start = time.time()
                    for sid, future in list(pending.items()):
                        if future.done():
                            done_futures.append(sid)

                    if not done_futures:
                        # 等最多 3 秒，看有没有完成的
                        try:
                            for future in as_completed(list(pending.values()), timeout=3):
                                pass  # as_completed yields futures as they complete
                        except:
                            pass

                        # 重新检查哪些完成了
                        for sid, future in list(pending.items()):
                            if future.done():
                                done_futures.append(sid)

                    # 收集完成的结果
                    for sid in done_futures:
                        try:
                            result = pending[sid].result(timeout=5)
                            results[sid] = result
                            completed.add(sid)
                            # Save to workflow snapshot for downstream subtasks
                            self.snapshot.put(task_id, sid, result)
                            del pending[sid]
                            status = "✅" if result.get("ok") else "❌"
                            out = str(result.get("output", result.get("error", "")))[:80]
                            print(f"[WF] {task_id}/{sid} {status} {out}", flush=True)
                        except Exception as e:
                            results[sid] = {"ok": False, "error": str(e)[:200]}
                            completed.add(sid)
                            del pending[sid]

                time.sleep(0.5)

            # 超时的标记为失败
            for sid, future in pending.items():
                results[sid] = {"ok": False, "error": "timeout", "agent": dispatches.get(sid, {}).get("agent", "?")}
                completed.add(sid)

        return results

    def _execute_subtask(self, task_id: str, sid: str, subtask: dict, payload: dict) -> dict:
        """Execute a single subtask via L4 MCP dispatch_and_await"""
        agent_name = subtask["agent"]
        
        # Tool type: execute directly
        if agent_name == "_tool_":
            tool_name = subtask.get("tool", "")
            self.sm.subtask_start(task_id, sid, "_tool_", subtask["action"], tool_name)
            if tool_name == "fix_executor":
                r = subprocess.run(["python3", "/opt/commander/tools/fix_executor.py", "/tmp/fix-spec.json"],
                                  capture_output=True, text=True, timeout=120)
                return {"ok": r.returncode == 0, "output": r.stdout[:500], "agent": "_tool_"}
            if tool_name == "deploy_hook":
                r = subprocess.run(["python3", "/opt/commander/tools/deploy_hook.py", "verify", "power"],
                                  capture_output=True, text=True, timeout=120)
                return {"ok": r.returncode == 0, "output": r.stdout[:500], "agent": "_tool_"}
            return {"ok": False, "error": f"unknown tool: {tool_name}", "agent": "_tool_"}

        # Agent type: dispatch via L4 MCP
        # Agent credit-aware selection: prefer higher-scored agents
        agent_skill = self._agent_skill_map().get(agent_name, "")
        try:
            if hasattr(self, "flywheel") and self.flywheel:
                _credit = self.flywheel.get_agent_credit(agent_name)
                if _credit < 5.0:
                    print("[WF] {} agent {} credit={:.1f} (<5), may degrade quality".format(
                        task_id, agent_name, _credit), flush=True)
        except Exception:
            pass
        prompt = subtask.get("prompt", str(payload.get("task", ""))[:500])
        self.sm.subtask_start(task_id, sid, agent_name, subtask["action"], prompt)
        
        # Resolve upstream data
        upstream = {}
        for dep_id in subtask.get("depends", []):
            dep_data = self.snapshot.get(task_id, dep_id)
            if dep_data:
                upstream[f"from_{dep_id}"] = str(dep_data.get("output", ""))[:500]
        
        # Dispatch via L4 MCP
        result = call_layer(4, "dispatch_and_await",
                           agent_name=agent_name, task_id=task_id, sid=sid,
                           action=subtask["action"], prompt=prompt,
                           parent_task=task_id, agent_skill=agent_skill, timeout=60)
        
        ok = result.get("ok", False)
        output = result.get("output", result.get("error", ""))
        if ok:
            self.sm.subtask_done(task_id, sid, output, result.get("elapsed_ms", 0))
            # Save to snapshot
            self.snapshot.put(task_id, sid, {"output": output, "ok": True, "agent": agent_name})
        else:
            self.sm.subtask_timeout(task_id, sid)
        
        return {"ok": ok, "output": output[:1000], "agent": agent_name}


    def _decompose_via_l2(self, task_id: str, payload: dict) -> list:
        if MCP_LAYERS_ENABLED.get("L2"):
            return [{"id": "s1", "action": "mcp_routed", "agent": "审计官", "depends": [], "prompt": "MCP L2 not implemented"}]

        """L2: query L0 experience -> try MCP decompose -> fallback LLM"""
        task_desc = str(payload.get("task", payload.get("action", "")))[:800]
        action = payload.get("action", "unknown")
        action_clean = action.replace("site_", "").replace("translate_", "")
        self._current_intent = action_clean
        available = list(self._agent_skill_map().keys())

        # Semantic Intent Routing (replaces INTENT_TOOL_MAP)
        _primary_agent = None
        try:
            if hasattr(self, "intent_router") and self.intent_router:
                _route = self.intent_router.route(task_desc)
                if _route and _route.get("confidence", 0) > 0.4:
                    _primary_agent = _route.get("primary_agent")
                    print("[WF] {} semantic route: {} (conf={:.2f})".format(
                        task_id, _primary_agent, _route.get("confidence", 0)), flush=True)
        except Exception:
            pass  # semantic router unavailable, fallback to INTENT_TOOL_MAP

        # L0: Retrieve past experiences for this intent
        past_exp = self.l0._retrieve_experiences(action_clean, available[:5])
        experience_context = ""
        if past_exp:
            print(f"[L0] {task_id} found {len(past_exp)} past experiences for '{action_clean}'", flush=True)
            # Format experience for LLM injection
            exp_lines = ["## 历史经验（同类任务参考）"]
            for i, exp in enumerate(past_exp[:3]):
                agents_used = exp.get("agents_involved", [exp.get("agent", "?")])
                subtask_actions = exp.get("subtask_actions", [])
                score = exp.get("score", "?")
                success_mark = "✅" if exp.get("success") else "❌"
                exp_lines.append(f"### 案例{i+1} (评分:{score}/10 {success_mark})")
                exp_lines.append(f"- Agent: {', '.join(agents_used)}")
                if subtask_actions:
                    steps = ' → '.join(str(sa)[:60] for sa in subtask_actions[:5])
                    exp_lines.append(f"- 步骤: {steps}")
            experience_context = "\n".join(exp_lines)
        else:
            # Chroma semantic search fallback
            try:
                from modules.layer1.vector_store_chroma import ChromaVectorStore
                vs = ChromaVectorStore()
                semantic = vs.search(f"task:{task_desc[:200]}", top_k=3)
                if semantic:
                    exp_lines = ["## 语义相似经验"]
                    for i, s in enumerate(semantic):
                        exp_lines.append(f"### 类似任务{i+1}\n{s.get('text','')[:300]}")
                    experience_context = "\n".join(exp_lines)
                    print(f"[L0] {task_id} Chroma 语义: {len(semantic)} 条", flush=True)
            except Exception:
                pass

        try:
            result = call_layer(2, "decompose_task",
                               task_id=task_id, task=task_desc,
                               available_agents=available[:8],
                               experience_context=experience_context[:1500])
            if result and isinstance(result, list) and len(result) > 0:
                subtasks = []
                for i, item in enumerate(result):
                    if isinstance(item, dict):
                        subtasks.append({
                            "id": item.get("id", "s" + str(i+1)),
                            "action": item.get("action", item.get("description", "execute"))[:60],
                            "agent": item.get("agent", item.get("agent_type", "auditor")),
                            "depends": item.get("depends", item.get("depends_on", [])),
                            "prompt": item.get("prompt", item.get("description", task_desc))[:500],
                        })
                if subtasks:
                    print("[WF] %s L2 MCP: %d subtasks" % (task_id, len(subtasks)), flush=True)
                    return subtasks
        except Exception as e:
            print("[WF] %s L2 MCP failed: %s" % (task_id, str(e)[:50]), flush=True)
        return self._llm_decompose(task_id, payload, experience_context, _primary_agent)

    # ==============================================
    # L0 Memory Layer: experience retrieval + web knowledge
    # ==============================================

    def _clone_agents_for_task(self, task_id: str, subtasks: list) -> dict:
        """Pre-L3: Clone agent templates -> session instances"""
        agents_needed = set(s["agent"] for s in subtasks)
        cloned = {}
        for agent_name in agents_needed:
            skill = self._agent_skill_map().get(agent_name, "")
            if self.commander:
                ok = self.commander.spawn_neuron(agent_name, skill, task_id=task_id)
                cloned[agent_name] = ok
                if ok:
                    print("[WF] %s cloned: %s" % (task_id, agent_name), flush=True)
        return cloned

    def _schedule_via_l3(self, task_id: str, subtasks: list) -> dict:
        """L3: Schedule via MCP coordination layer"""
        plan = {
            "task_id": task_id,
            "subtasks": [{
                "id": s["id"],
                "agent_type": s["agent"],
                "action": s["action"],
                "depends": s.get("depends", []),
                "session_memory_key": s.get("session_memory_key", ""),
            } for s in subtasks]
        }
        available = list(self._agent_skill_map().keys())
        try:
            result = call_layer(3, "schedule_agents",
                               plan=json.dumps(plan, ensure_ascii=False),
                               available_agents=json.dumps(available))
            if isinstance(result, dict):
                return result
        except Exception as e:
            print("[WF] %s L3 failed: %s" % (task_id, str(e)[:50]), flush=True)
        assignments = [{"subtask_id": s["id"], "agent_id": s["agent"]} for s in subtasks]
        return {"assignments": assignments, "total_assigned": len(assignments), "method": "fallback"}

    def _check_and_heal(self, task_id: str, subtasks: list, results: dict):
        """故障检测: 同一 Agent 连续失败 > 2 次 → 派系统医生"""
        agent_failures = {}
        for st in subtasks:
            sid = st["id"]
            r = results.get(sid, {})
            if not r.get("ok"):
                agent = st.get("agent", "unknown")
                agent_failures[agent] = agent_failures.get(agent, 0) + 1

        for agent, count in agent_failures.items():
            if count >= 2 and self.commander:
                failure_type = "low_quality"
                # 检查是否是超时
                for st in subtasks:
                    if st.get("agent") == agent and results.get(st["id"], {}).get("error") == "timeout":
                        failure_type = "slow_response"
                        break

                print(f"[WF] 🏥 {agent} 连续失败 {count} 次, 派系统医生 (type={failure_type})", flush=True)
                self.commander.handle_agent_failure(
                    agent, failure_type, task_id,
                    details=f"连续{count}个子任务失败"
                )

    def _cleanup_task(self, task_id: str, subtasks: list, final_score: int):
        """Post-task cleanup: ExperienceFlywheel + destroy memory"""
        agents_used = set(s["agent"] for s in subtasks)
        action = self._current_intent or "general"

        # ── Primary: ExperienceFlywheel ──
        try:
            flywheel = self.flywheel
            flywheel.save_experience(
                task_id=task_id,
                task_description=str(self._current_intent or ""),
                subtasks=subtasks,
                final_score=float(final_score),
                l5_signals={},
                agents_used=agents_used,
                intent=action,
            )
            print(f"[WF] {task_id} flywheel: {len(agents_used)} agents, score={final_score}", flush=True)
        except Exception as _e:
            print(f"[WF] {task_id} flywheel failed, fallback to l0", flush=True)
            # Fallback to legacy L0 storage
            try:
                import redis as _r
                _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                             password=os.environ.get("REDIS_PASSWORD", ""),
                             decode_responses=True)
                self.l0._save_experience(task_id, subtasks, final_score, agents_used, _rd)
            except Exception:
                pass

        # ── Cleanup: destroy task memory ──
        try:
            import redis as _r
            _rd = _r.Redis(protocol=2, host="127.0.0.1", port=6379,
                         password=os.environ.get("REDIS_PASSWORD", ""),
                         decode_responses=True)
            for agent in agents_used:
                _rd.delete(f"agent:{agent}:{task_id}:memory")
            # Cleanup workflow snapshot
            self.snapshot.cleanup(task_id)
        except Exception:
            pass

    def _save_experience(self, task_id, subtasks, final_score, agents_used, r):
        """L0: Save structured experience for future retrieval"""
        intent = self._current_intent or "general"
        for agent in agents_used:
            if agent.startswith("_"): continue
            exp = {
                "task_id": task_id,
                "agent": agent,
                "intent": intent,
                "score": final_score,
                "subtask_count": len(subtasks),
                "ts": time.time(),
                "success": final_score >= 7,
                "agents_involved": list(agents_used),
                "subtask_actions": [s.get("action", "")[:60] for s in subtasks[:5]],
            }
            key = f"exp:{intent}:{agent}"
            r.lpush(key, json.dumps(exp, ensure_ascii=False))
            r.ltrim(key, 0, 49)  # Keep max 50 experiences per intent+agent
        # Also save by intent only (agent-agnostic)
        all_actions = [s.get("action","")[:60] for s in subtasks[:5]]
        intent_exp = {"task_id": task_id, "score": final_score, "agents": list(agents_used),
                      "actions": all_actions, "ts": time.time(), "success": final_score >= 7}
        r.lpush(f"exp:{intent}:all", json.dumps(intent_exp, ensure_ascii=False))
        r.ltrim(f"exp:{intent}:all", 0, 49)
        print(f"[L0] saved experience: {intent} score={final_score} agents={list(agents_used)}", flush=True)

    _current_intent = "general"

    def _summarize_results(self, task_id: str, subtasks: list, results: dict) -> str:
        """汇总所有子任务结果"""
        lines = [f"## 子任务执行汇总 ({task_id})"]
        for st in subtasks:
            sid = st["id"]
            r = results.get(sid, {})
            status = "✅" if r.get("ok") else "❌"
            output = str(r.get("output", r.get("error", "")))[:200]
            lines.append(f"- {status} **{st['action']}** ({st['agent']}): {output}")
        return "\n".join(lines)

    def _llm_decompose(self, task_id: str, payload: dict, experience_context: str = "", primary_agent: str = None) -> list:
        """LLM decompose with data-driven batch parallelism"""
        import re
        task_desc = payload.get("task", json.dumps(payload, ensure_ascii=False)[:500])

        # Data-driven batch detection: extract numbers from task text
        nums = re.findall(r'(\d{3,}).*?(entries|fields|pages|items|records|处|条|项|混杂|语言)', task_desc.lower())
        if not nums:
            nums = re.findall(r'(\d{3,}).*?(entries|fields|pages|items|records|处|条|项|混杂|语言)', 
                            self._current_intent + " " + task_desc)

        if nums:
            total = int(nums[0][0])
            batch_size = max(100, min(500, total // 8))  # 100-500 per agent, aim for 8 agents
            num_batches = max(2, min(10, (total + batch_size - 1) // batch_size))
            print("[WF] %s data-driven batch: %d items -> %d batches x ~%d" % 
                  (task_id, total, num_batches, batch_size), flush=True)

            subtasks = []
            for i in range(num_batches):
                start = i * batch_size + 1
                end = min((i + 1) * batch_size, total)
                count = end - start + 1
                sid = "s%d" % (i + 1)
                # Alternate between agent types for better parallelism
                agent = "LM内容工程师" if i % 2 == 0 else "审计官"
                subtasks.append({
                    "id": sid,
                    "action": "Batch %d/%d: fix entries %d-%d (%d items)" % (i+1, num_batches, start, end, count),
                    "agent": agent,
                    "depends": [],
                    "prompt": "Fix batch %d of %d: handle %d mixed-language entries (entries %d-%d). Query pages, translate Chinese to target language, update MongoDB." % 
                             (i+1, num_batches, count, start, end),
                })
            return subtasks

        # Fallback to LLM decomposition for non-batch tasks
        llm = self._get_llm()
        if not llm:
            return [{"id":"s1","action":"execute","agent":"审计官","depends":[],"prompt":task_desc[:300]}]

        prompt = """Decompose this task into 2-5 subtasks. Output JSON array only.

Available agents: 审计官(audit), 品牌策略师(brand/strategy), 翻译官(translate), UI/UX设计师(design), 前端工程师(frontend), LM内容工程师(content engineering)

"""
        if experience_context:
            prompt += experience_context[:1200] + "\n\n"
        if primary_agent:
            prompt += f"Hint: best matching agent is {primary_agent}\n\n"
        prompt += "Task: " + task_desc[:400]

        try:
            resp = llm.chat.completions.create(
                model="deepseek-chat",
                messages=[{"role":"user","content":prompt}],
                temperature=0.3, max_tokens=500,
            )
            content_text = resp.choices[0].message.content
            if "```" in content_text:
                content_text = content_text.split("```")[1]
                if content_text.startswith("json"): content_text = content_text[4:]
            data = json.loads(content_text.strip())
            result = data.get("subtasks", data if isinstance(data, list) else [])
            # Normalize
            normalized = []
            for i, item in enumerate(result):
                if isinstance(item, dict):
                    normalized.append({
                        "id": item.get("id", "s%d"%(i+1)),
                        "action": item.get("action", item.get("description", "execute"))[:60],
                        "agent": item.get("agent", item.get("agent_type", "审计官")),
                        "depends": item.get("depends", item.get("depends_on", [])),
                        "prompt": item.get("prompt", item.get("description", task_desc))[:500],
                    })
            if normalized:
                print("[WF] LLM decompose: %d subtasks" % len(normalized), flush=True)
                return normalized
        except Exception as e:
            print("[WF] LLM decompose failed: %s" % str(e)[:50], flush=True)
        return [{"id":"s1","action":"execute","agent":"审计官","depends":[],"prompt":task_desc[:300]}]

    def _do_L1(self, task_id: str, payload: dict) -> dict:
        """L1 感知"""
        print(f"[WF] {task_id} L1 感知...", flush=True)
        l1_text = {k: v for k, v in payload.items() if not k.startswith("_")}
        l1 = call_layer(1, "analyze_intent", text=json.dumps(l1_text, ensure_ascii=False))
        state = {"l1_result": l1}

        primary = l1.get("primary_intent", "general")
        confidence = l1.get("confidence", 0.5)
        action = payload.get("action", "")
        action_clean = action.replace("site_", "").replace("translate_", "")

        if action_clean in INTENT_TOOL_MAP:
            primary = action_clean
            confidence = 0.99
            state["l1_action_override"] = True
        elif action in INTENT_TOOL_MAP:
            primary = action
            confidence = 0.99
            state["l1_action_override"] = True

        state["primary_intent"] = primary
        state["confidence"] = confidence
        return state

    def _do_L2(self, task_id: str, payload: dict, state: dict, arsenal_tools: list) -> dict:
        """L2 规划"""
        primary = state["primary_intent"]
        confidence = state["confidence"]
        print(f"[WF] {task_id} L2 规划 (intent={primary}, conf={confidence:.2f})...", flush=True)
        return self._build_plan(primary, payload.get("action", ""), payload, arsenal_tools)

    def _do_L3_L4(self, task_id: str, payload: dict, plan: dict, state: dict) -> dict:
        if MCP_LAYERS_ENABLED.get("L4"):
            return {"mcp_routed": True, "layer": "L4", "phase": "not_implemented"}

        """L3 调度 + L4 执行"""
        agent_name = plan.get("agent")
        l3 = {"dispatched": False, "agent": agent_name, "neuron_spawned": False}
        l4 = {}

        if agent_name and self.commander:
            agent_skill = self._agent_skill_map().get(agent_name, "")
            # 支持 thinking 覆盖
            thinking_override = payload.get("_thinking", None)
            spawned = self.commander.spawn_neuron(agent_name, agent_skill, thinking=thinking_override, task_id=task_id)  # Phase 4: 简单任务无 sid, 直接用 task_id
            state["_last_thinking"] = thinking_override or "medium"
            l3["neuron_spawned"] = spawned
            # Wait for neuron to fully initialize and subscribe to Redis channels
            time.sleep(5)

            # Stream 发布任务 (替代 Pub/Sub，消息不丢失)
            try:
                from stream_bridge import StreamBridge
                _bridge = StreamBridge(
                    redis_host="127.0.0.1", redis_port=6379,
                    redis_password=os.environ.get("REDIS_PASSWORD", ""))
                msg = {"type": "task", "taskId": task_id, "from": "workflow",
                       "to": agent_name, "replyTo": "lightingmetal:agent:commander",
                       "payload": {k: v for k, v in payload.items() if not k.startswith("_")}}
                _bridge.publish_task("L4", msg, task_id)
                l3["dispatched"] = True
                l3["method"] = "stream"
                print(f"[WF] {task_id} Stream 发布 → L4 (agent={agent_name})", flush=True)
            except Exception as e:
                l3["error"] = f"Stream发布失败:{str(e)[:80]}"
                # 回退 Pub/Sub
                agent_channel = f"lightingmetal:agent:{agent_name}"
                try:
                    count = self.commander.redis.client.publish(
                        agent_channel, json.dumps(msg, ensure_ascii=False, default=str))
                    l3["dispatched"] = count > 0
                except Exception as e2:
                    l3["error"] = str(e2)[:100]
        state["l3_result"] = l3

        tool_name = plan.get("tool")
        if tool_name and self.commander and self.commander.arsenal.has(tool_name):
            try:
                l4 = {"status": "success", "arsenal_tool": tool_name,
                      "result": self.commander.arsenal.call(tool_name, task_id, payload)}
            except Exception as e:
                l4 = {"status": "error", "error": str(e)[:500]}
        elif l3.get("dispatched"):
            # 等待 neuron 的 LLM 分析结果（Pub/Sub 异步响应）
            l4 = self._wait_for_neuron_response(task_id, agent_name, timeout=POLL_TIMEOUT)
        else:
            l4 = {"status": "error", "error": "无法分发任务到 Agent"}
        return l4

    def _wait_for_neuron_response(self, task_id: str, agent_name: str, timeout: int = 120) -> dict:
        """Stream + Pub/Sub 双重等待 neuron 响应。

        Stream 优先（消息持久化不丢失），Pub/Sub 作为快速通道。
        """
        if not self.commander or not self.commander.redis:
            return {"status": "error", "error": "无法连接 Redis 等待响应"}

        print(f"[WF] 等待 {agent_name} 响应 (task={task_id}, timeout={timeout}s)...", flush=True)
        start = time.time()
        last_progress = 0
        last_progress_ts = time.time()
        progress_stall_timeout = 180  # 进度停滞超过 180s 才算真超时

        # Stream 响应通道
        try:
            from stream_bridge import StreamBridge
            _bridge = StreamBridge(
                redis_host="127.0.0.1", redis_port=6379,
                redis_password=os.environ.get("REDIS_PASSWORD", ""))
            _response_stream = f"yaxiio:stream:L4_response"
            _response_group = f"commander-response"
            _bridge.ensure_group(_response_stream, _response_group)
        except Exception:
            _bridge = None

        # Pub/Sub 快速通道（回退）
        try:
            pubsub = self.commander.redis.client.pubsub()
            pubsub.subscribe("lightingmetal:agent:commander")
        except Exception:
            pubsub = None

        try:
            while time.time() - start < timeout:
                # 0. 心跳检查: 神经元还在跑就延长超时
                try:
                    raw = self.commander.redis.client.get(f"agent:{agent_name}:{task_id}:state")
                    if raw:
                        state = json.loads(raw)
                        pct = state.get("progress", 0)
                        ts = state.get("ts", 0)
                        if pct > last_progress:
                            last_progress = pct
                            last_progress_ts = time.time()
                            # 有进展 → 延长总超时
                            if timeout < 600:
                                timeout = min(600, timeout + 60)
                                print(f"[WF] {agent_name} 进度 {pct}%, 延长超时至 {timeout}s", flush=True)
                        elif time.time() - last_progress_ts > progress_stall_timeout:
                            print(f"[WF] {agent_name} 进度停滞 {progress_stall_timeout}s, 判定超时", flush=True)
                            break
                except Exception:
                    pass

                # 1. 先检查 Stream
                if _bridge:
                    try:
                        results = _bridge.r.xreadgroup(
                            groupname=_response_group,
                            consumername=f"commander-{task_id}",
                            streams={_response_stream: ">"},
                            block=1000, count=10)
                        if results:
                            for stream_name, messages in results:
                                for msg_id, fields in messages:
                                    data = json.loads(fields.get("payload", "{}"))
                                    _bridge.r.xack(_response_stream, _response_group, msg_id)
                                    if data.get("taskId") == task_id:
                                        elapsed = time.time() - start
                                        print(f"[WF] {agent_name} Stream响应 (耗时 {elapsed:.1f}s)", flush=True)
                                        payload = data.get("payload", data)
                                        return {
                                            "agent_id": agent_name,
                                            "status": payload.get("status", "success"),
                                            "stdout": str(payload.get("thought", payload.get("result", "")))[:5000],
                                            "stderr": "",
                                            "exit_code": 0,
                                            "elapsed_ms": int(elapsed * 1000),
                                        }
                    except Exception:
                        pass

                # 2. Pub/Sub 快速通道
                if pubsub:
                    msg = pubsub.get_message(timeout=0.1)
                    if msg and msg["type"] == "message":
                        try:
                            data = json.loads(msg["data"])
                        except json.JSONDecodeError:
                            continue
                        if data.get("taskId") == task_id and data.get("type") == "response":
                            payload = data.get("payload", {})
                            elapsed = time.time() - start
                            print(f"[WF] {agent_name} PubSub响应 (耗时 {elapsed:.1f}s)", flush=True)
                            return {
                                "agent_id": agent_name,
                                "status": payload.get("status", "unknown"),
                                "stdout": str(payload.get("thought", payload.get("result", "")))[:5000],
                                "stderr": "",
                                "exit_code": 0,
                                "elapsed_ms": int(elapsed * 1000),
                            }

                time.sleep(0.5)

        except Exception as e:
            return {"status": "error", "error": f"等待 neuron 响应异常: {str(e)[:200]}"}
        finally:
            if pubsub:
                try:
                    pubsub.close()
                except Exception:
                    pass

        return {"status": "timeout", "error": f"{agent_name} 未在 {timeout}s 内响应"}

    def _do_L5(self, task_id: str, action: str, plan: dict, l4: dict, state: dict) -> dict:
        """L5 scoring — UnifiedScorer primary path + legacy fallback"""
        if MCP_LAYERS_ENABLED.get("L5"):
            return {"mcp_routed": True, "layer": "L5", "phase": "not_implemented"}

        # Extract output text
        output_text = self._extract_output_text(l4)

        # Resolve agent name
        if isinstance(plan, dict) and "agent" in plan:
            agent_name = plan["agent"]
        else:
            agent_name = "unknown"
        if agent_name == "unknown" and isinstance(plan, dict):
            subtasks = plan.get("subtasks", [])
            agents = list(set(s.get("agent", "") for s in subtasks))
            agent_name = ", ".join(agents[:3]) if agents else "unknown"

        # Load agent capability card
        agent_card = None
        try:
            if self.commander and self.commander.redis:
                primary = agent_name.split(",")[0].strip()
                card_raw = self.commander.redis.get(f"agent:card:{primary}")
                if card_raw:
                    agent_card = json.loads(card_raw)
        except Exception:
            pass

        # Determine scoring strategy
        if isinstance(plan, dict):
            subtask_count = len(plan.get("subtasks", []))
        else:
            subtask_count = 1
        if subtask_count <= 1 and len(output_text) < 500:
            strategy = "fast"
        elif subtask_count >= 5:
            strategy = "deep"
        else:
            strategy = "standard"

        # PRIMARY PATH: Commander 动态评分维度
        try:
            from score_registry import ScoreDimensionRegistry
            registry = ScoreDimensionRegistry(self.commander.redis if self.commander else None)
            dim_config = registry.get_dimensions(action, plan if isinstance(plan, dict) else {})
            rule_score = registry.score_output(
                output_text,
                dim_config["dimensions"],
                dim_config["weights"]
            )
            label = f"overall={rule_score['overall']} dims={list(rule_score.get('dimensions',{}).keys())}"
            print(f"[WF] {task_id} L5 Commander评分: {label}", flush=True)
            result = {
                "overall": rule_score["overall"],
                "method": "commander_rule",
                "verdict": "pass" if rule_score["overall"] >= 7 else ("retry" if rule_score["overall"] >= 4 else "reject"),
                "sources_used": ["rule"],
                "dimensions": rule_score.get("dimensions", {}),
                "signals": rule_score.get("signals", {}),
                "needs_review": rule_score["overall"] < 7,
                "needs_evolution": rule_score["overall"] < 5,
            }
            self.score_history.append({"task_id": task_id, "score": result["overall"], "ts": time.time()})
            return result
        except Exception as e:
            print(f"[WF] {task_id} Commander评分失败 ({e}), 回退 UnifiedScorer", flush=True)

        # FALLBACK: UnifiedScorer
        try:
            from modules.layer5.unified_scorer import UnifiedScorer
            scorer = UnifiedScorer(redis_client=self.commander.redis if self.commander else None)
            task_info = {
                "task_id": task_id, "action": action,
                "description": str(state.get("summary", ""))[:500],
                "type": action,
            }
            result_info = {
                "output": output_text[:3000],
                "subtasks": plan.get("subtasks", []) if isinstance(plan, dict) else [],
                "status": "success" if l4.get("results") else "partial",
            }
            result = scorer.score(
                task=task_info, result=result_info,
                strategy=strategy, agent_card=agent_card
            )
            label = f"overall={result.get('overall','?')} verdict={result.get('verdict','?')} sources={result.get('sources_used',[])}"
            print(f"[WF] {task_id} L5 UnifiedScorer: {label}", flush=True)
            self.score_history.append({
                "task_id": task_id,
                "score": result["overall"],
                "ts": time.time()
            })
            return result
        except Exception as e:
            print(f"[WF] {task_id} UnifiedScorer failed ({e}), fallback to legacy L5", flush=True)

        # FALLBACK: legacy scoring
        return self._legacy_l5_score(task_id, action, plan, l4, state, output_text, agent_name)
    def _legacy_l5_score(self, task_id, action, plan, l4, state, output_text, agent_name):
        """Legacy L5 scoring — fallback when UnifiedScorer is unavailable"""
        context = json.dumps({"action": action, "intent": state.get("primary_intent", ""),
                              "total_rounds": state.get("total_rounds", 1)}, ensure_ascii=False)
        # Try LLM deep_score via MCP
        try:
            l5 = call_layer(5, "deep_score",
                           task_id=task_id, action=action,
                           agent_name=agent_name,
                           output=output_text[:3000], context=context)
            if l5.get("method") == "llm":
                result = {
                    "overall": l5.get("overall", 5),
                    "method": "llm_deep_score",
                    "dimensions": {k: l5.get(k, 0) for k in
                                   ["accuracy","completeness","professionalism","actionability","consistency"]},
                    "key_issues": l5.get("key_issues", []),
                    "suggestions": l5.get("suggestions", []),
                    "verdict": l5.get("verdict", "pass"),
                    "needs_review": l5.get("verdict") in ("retry", "reject"),
                    "needs_evolution": l5.get("overall", 5) < 5,
                }
                self.score_history.append({"task_id": task_id, "score": result["overall"], "ts": time.time()})
                return result
        except Exception:
            pass

        # Rule-based fallback — 增强版: 从Agent输出中提取信号
        has_result = bool(output_text and len(output_text) > 50)
        output_len = len(output_text)
        subtask_count = len(l4.get("results", {}))
        
        # 信号检测
        has_code_blocks = "```" in output_text
        has_structured = any(marker in output_text for marker in ("##", "###", "**", "| ", "1.", "2.", "3."))
        has_findings = any(kw in output_text.lower() for kw in ("发现", "问题", "建议", "结论", "finding", "issue", "recommend"))
        has_report_format = output_text.startswith("##") or output_text.startswith("# ")
        
        # 基础分
        completeness = 8 if has_result else (5 if subtask_count > 0 else 3)
        if has_report_format:
            completeness = min(10, completeness + 1)
        
        quality = min(9, 4 + output_len // 500) if has_result else 3
        if has_findings:
            quality = min(10, quality + 1)
        
        professionalism = 6 + (1 if output_len > 1000 else 0) + (1 if has_report_format else 0)
        
        actionability = 6 + (2 if has_code_blocks else 0) + (1 if has_findings else 0)
        
        accuracy = 5 + (2 if subtask_count >= 3 else 0) + (1 if has_structured else 0)
        consistency = 7 + (1 if has_report_format else 0)
        
        base = {
            "accuracy": min(10, accuracy),
            "completeness": min(10, completeness),
            "professionalism": min(10, professionalism),
            "actionability": min(10, actionability),
            "consistency": min(10, consistency),
        }
        base_overall = round(sum(base.values()) / len(base))
        result = {"overall": base_overall, "method": "rule_fallback", "dimensions": base,
                  "needs_review": base_overall < 7, "needs_evolution": base_overall < 5,
                  "verdict": "pass" if base_overall >= 7 else ("retry" if base_overall >= 4 else "reject")}
        self.score_history.append({"task_id": task_id, "score": base_overall, "ts": time.time()})
        return result

    def _extract_output_text(self, l4: dict) -> str:
        """Extract output text from various L4 result formats"""
        output_text = ""
        if l4.get("results") and isinstance(l4["results"], dict):
            parts = []
            for sid, r in sorted(l4["results"].items()):
                out = str(r.get("output", r.get("summary", "")))[:300]
                if out:
                    parts.append(out)
            output_text = "\n---\n".join(parts)
        if not output_text and l4.get("summary"):
            output_text = str(l4["summary"])[:3000]
        if not output_text:
            if isinstance(l4.get("result"), dict):
                output_text = str(l4["result"].get("output", l4["result"].get("summary", "")))
        if not output_text:
            output_text = str(l4.get("stdout", l4.get("output", "")))
        return output_text

    def _build_plan(self, primary_intent: str, action: str,
                    payload: dict, arsenal_tools: list) -> dict:
        if primary_intent in INTENT_TOOL_MAP:
            plan = dict(INTENT_TOOL_MAP[primary_intent])
            plan["match_type"] = "exact"
            plan["intent"] = primary_intent
            return plan
        for ik, ti in INTENT_TOOL_MAP.items():
            if ik in primary_intent or primary_intent in ik:
                plan = dict(ti)
                plan["match_type"] = "fuzzy"
                plan["intent"] = primary_intent
                return plan
        if action in (arsenal_tools or []):
            return {"tool": action, "agent": "审计官", "desc": f"tool:{action}",
                    "match_type": "direct", "intent": primary_intent}
        return {"tool": None, "agent": "审计官", "desc": f"通用:{action}",
                "match_type": "fallback", "intent": primary_intent, "command": f"echo 'task'"}

    def _get_llm(self, task_type: str = "default", task_desc: str = ""):
        """LLM client with IntelligentModelRouter + auto-fallback"""
        if self.commander:
            try:
                # Use IntelligentModelRouter (cost x latency x capability)
                if hasattr(self, "model_router_v2") and self.model_router_v2:
                    task_info = {"action": task_type, "description": task_desc or task_type}
                    cfg = self.model_router_v2.select(task_info)
                    model = cfg.get("model", task_type)
                    thinking = cfg.get("thinking", "medium")
                    print("[WF] model router: {} (thinking={}, score={})".format(
                        model, thinking, cfg.get("score", 0)), flush=True)
                else:
                    model = task_type
                    thinking = "medium"
                return self.commander._get_llm(model, thinking)
            except Exception as _e:
                # Auto-fallback to next provider
                try:
                    if hasattr(self, "model_router_v2") and self.model_router_v2:
                        fb = self.model_router_v2.fallback(model if "model" in dir() else task_type)
                        if fb:
                            print("[WF] model fallback to: {}".format(fb.get("model","?")), flush=True)
                            return self.commander._get_llm(fb["model"], "off")
                except Exception:
                    pass
                try:
                    return self.commander._get_llm()
                except Exception:
                    pass
        return None

    def _call_llm(self, prompt: str, timeout: float = 30.0) -> str:
        llm = self._get_llm()
        if not llm: raise RuntimeError("LLM unavailable")
        if self.commander:
            from yaxiio import async_loop
            return async_loop.run_coro(llm.chat(prompt), timeout=timeout)
        import asyncio
        loop = asyncio.new_event_loop()
        try: return loop.run_until_complete(llm.chat(prompt))
        finally: loop.close()

    @staticmethod
    def _bump_thinking(current: str) -> str:
        """升级 thinking: off→low→medium→high→max"""
        order = ["off", "low", "medium", "high", "max"]
        try:
            idx = order.index(current)
            return order[min(idx + 1, len(order) - 1)]
        except ValueError:
            return "high"

    # ═══════════════════════════════════════════════
    # 目标自检: 差距分析 + 子任务生成
    # ═══════════════════════════════════════════════

    def _analyze_gap(self, task_id: str, payload: dict, results: dict, l5_scores: dict) -> dict:
        """Gap analysis using UniversalGapAnalyzer — zero industry hardcoding"""
        try:
            from modules.layer5.gap_analyzer_v2 import UniversalGapAnalyzer
            analyzer = UniversalGapAnalyzer()

            # Load agent card
            agent_card = None
            try:
                if self.commander and self.commander.redis:
                    primary_agent = l5_scores.get("primary_agent", "\u5ba1\u8ba1\u5b98")
                    card_raw = self.commander.redis.get(f"agent:card:{primary_agent}")
                    if card_raw:
                        agent_card = json.loads(card_raw)
            except Exception:
                pass

            return analyzer.analyze(
                task={"action": payload.get("action", ""),
                      "description": str(payload.get("task", ""))[:300]},
                results=results,
                l5_scores=l5_scores,
                agent_card=agent_card,
            )
        except Exception as e:
            print(f"[WF] UniversalGapAnalyzer failed ({e}), fallback to legacy", flush=True)
            return self.gap.analyze(task_id, payload, results, l5_scores)

    def _detect_content_issues(self, results: dict) -> dict:
        """Parse subtask outputs for concrete content problems"""
        import re
        issues = {"mixed_lang": 0, "empty_fields": 0, "missing_pages": 0, "truncated": 0}
        for sid, res in results.items():
            output = str(res.get("output", ""))
            for line in output.split("\n"):
                if "mixed" in line.lower() or "混杂" in line:
                    nums = re.findall(r"(\d{3,})", line)
                    if nums: issues["mixed_lang"] = max(issues["mixed_lang"], int(nums[0]))
                if "empty" in line.lower() or "空字段" in line:
                    if "empty" in line.lower():
                        nums = re.findall(r"(\d{3,})", line)
                        if nums: issues["empty_fields"] = max(issues["empty_fields"], int(nums[0]))
                if "missing" in line.lower() or "缺页" in line:
                    nums = re.findall(r"(\d+)", line)
                    if nums and int(nums[0]) < 1000:
                        issues["missing_pages"] = max(issues["missing_pages"], int(nums[0]))
        return issues

    def _gap_to_subtasks(self, task_id: str, gap: dict, payload: dict, round_num: int) -> list:
        """Convert gap analysis into executable subtasks"""
        next_actions = gap.get("next_actions", [])
        if not next_actions:
            return []

        subtasks = []
        for i, action in enumerate(next_actions):
            sid = "s%d_%d" % (round_num, i+1)
            agent = action.get("agent", "审计官")
            action_name = action.get("action", action.get("description", "continue"))
            subtasks.append({
                "id": sid,
                "action": action_name[:60],
                "agent": agent,
                "depends": action.get("depends", []),
                "prompt": action.get("prompt", action.get("description", action_name))[:500],
                "tool": action.get("tool", "")
            })

    def stats(self) -> dict:
        with self._lock: active_count = len(self.active)
        with self._count_lock: total = self.task_count
        recent = [s["score"] for s in self.score_history[-20:]]
        return {"active_workflows": active_count, "total_processed": total,
                "avg_score": round(sum(recent)/len(recent),2) if recent else 0,
                "evolution_queue": len(self.evolution_queue())}

