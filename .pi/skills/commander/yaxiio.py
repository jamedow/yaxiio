#!/usr/bin/env python3
"""雅溪 Yaxiio — 五层模块化智能调度引擎

职责：
  - Constitution 宪法审查：所有任务必须通过合规检查
  - WorkflowEngine 五层流水线：L1 感知 → L2 规划 → L3 调度 → L4 执行 → L5 进化
  - Arsenal 工具注册表：纯编排，业务逻辑委托给 Agent/脚本
  - Neuron 神经元管理：Agent 进程生命周期

这是 Yaxiio 的核心引擎，不直接对外提供服务。
外部接入通过 gateway.py（WebSocket/HTTP）→ commander.py（任务编排）。
"""
import sys, os, json, time, signal, asyncio, shutil, glob, subprocess, tempfile, threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/opt/commander")
from constitution import YaxiioConstitution, Verdict, get_constitution
from modules.shared.config import LOG_DIR
from workflow_engine import WorkflowEngine
from modules.layer1 import RedisClient, MCPRegistry, SkillLoader, create_vector_store
from modules.layer2 import AgentFactory, LifecycleManager, ModelRouter, AgentRegistry, RAGManager
from modules.layer3 import TaskDecomposer, DependencyAnalyzer, Scheduler, WorkflowSnapshot
from modules.layer4 import AutoScorer, AuditLogger, FailureDetector
from modules.layer5 import PromptOptimizer, WorkflowOptimizer, ABTester, SkillAutoGenerator


# ═══════════════════════════════════════════════════════════════
# 异步执行器 — 统一使用 async_executor.py 中的 AsyncExecutor 单例
# ═══════════════════════════════════════════════════════════════
from async_executor import async_executor
from trace_logger import TraceLogger
from sqlite_store import SQLiteStore, FakeMongoDB

# 向后兼容别名（其他模块可能引用）
async_loop = async_executor


# ═══════════════════════════════════════════════════════════════
# 有界线程池
# ═══════════════════════════════════════════════════════════════
class BoundedThreadPool:
    def __init__(self, max_workers: int = 5, max_queue: int = 20):
        self.max_workers = max_workers
        self.max_queue = max_queue
        self.pending = 0
        self._lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="yaxiio")
        self.active_tasks: dict = {}
        self.completed = 0
        self.rejected = 0
        self._futures_lock = threading.Lock()

    def submit(self, task_id: str, fn, *args, **kwargs) -> bool:
        with self._lock:
            if self.pending >= self.max_queue:
                self.rejected += 1
                return False
            self._lock.acquire(); self.pending += 1; self._lock.release()

        def _wrapper():
            try:
                return fn(*args, **kwargs)
            finally:
                with self._lock:
                    self.pending -= 1
                    self.completed += 1
                with self._futures_lock:
                    self.active_tasks.pop(task_id, None)

        future = self.executor.submit(_wrapper)
        with self._futures_lock:
            self.active_tasks[task_id] = future
        return True

    @property
    def queue_depth(self):
        return self.pending

    def active_count(self):
        with self._futures_lock:
            return len(self.active_tasks)

    def stats(self) -> dict:
        return {
            "queue_depth": self.pending, "active": self.active_count(),
            "completed": self.completed, "rejected": self.rejected,
            "max_queue": self.max_queue, "max_workers": self.max_workers,
        }


# ═══════════════════════════════════════════════════════════════
# Arsenal — 工具注册表 (Commander 的能力作为可调用工具)
# ═══════════════════════════════════════════════════════════════
class Arsenal:
    """Commander 的工具库。所有直接执行的能力注册在此，由流水线按需调用。"""

    def __init__(self, commander):
        self.commander = commander
        self._register_defaults()

    def _register_defaults(self):
        """注册 Commander 的合规工具（纯编排, 业务逻辑在 Agent/脚本）。

        所有工具必须映射到 Commander 上真实存在的方法或 tools/ 目录中真实存在的脚本。
        """
        c = self.commander
        self._tools = {
            # ── 系统白名单（Commander 直通方法）──
            "agent_export":       lambda tid, p: c._export_agents(),
            "agent_import":       lambda tid, p: c._import_agents(p),
            "skill_export":       lambda tid, p: c._export_skills(),
            "skill_import":       lambda tid, p: c._import_skills(p),
            "cleanup_sandboxes":  lambda tid, p: c._cleanup_sandboxes(),
            "restart_nuxt":       lambda tid, p: c._restart_nuxt(),
            "system_status":      lambda tid, p: c._system_status(),
            # ── 业务工具（通过 _run_tool 代理到 tools/ 脚本）──
            "content_audit":      lambda tid, p: c._run_tool(tid, "tools/multilang_audit.py", p),
            "batch_translate":    lambda tid, p: c._run_tool(tid, "tools/batch_translate.py", p),
            "fast_translate":     lambda tid, p: c._run_tool(tid, "tools/fast_translate.py", p),
            "terminology_check":  lambda tid, p: c._run_tool(tid, "tools/terminology_check.py", p),
            "mongo_query":        lambda tid, p: c._run_tool(tid, "tools/mongo_query.py", p),
            "redis_query":        lambda tid, p: c._run_tool(tid, "tools/redis_query.py", p),
            "hybrid_scorer":      lambda tid, p: c._run_tool(tid, "tools/hybrid_scorer.py", p),
        }

    def call(self, tool_name: str, task_id: str, payload: dict) -> dict:
        """调用注册的工具。未注册返回 error。"""
        if tool_name in self._tools:
            print(f"[Arsenal] 🔧 {tool_name}({task_id})", flush=True)
            return self._tools[tool_name](task_id, payload)
        return {"status": "error", "reason": f"tool '{tool_name}' not registered"}

    def list_tools(self) -> list:
        return sorted(self._tools.keys())

    def has(self, tool_name: str) -> bool:
        return tool_name in self._tools


class Commander:
    def __init__(self):
        self.redis = RedisClient()
        self.store = SQLiteStore()        # SQLite 替代 MongoDB
        self.mongo = FakeMongoDB(self.store)  # 兼容旧代码的 mongo 引用
        self.task_count = 0
        self.running = True
        self.start_time = time.time()

        # ── 资源池: 统一管理 LLM API Key + 模型分配 ──
        try:
            from resource_pool import resource_pool
            resource_pool.bootstrap(self.redis.client)
        except Exception as e:
            self.log.warn("__init__", "ResourcePool初始化失败", error=str(e))

        # ── 宪法 ──
        self.constitution = get_constitution(self.redis)

        # ── 武器库 ──
        self.arsenal = Arsenal(self)

        # ── 能力层 ──
        self.mcp = MCPRegistry()
        self.vector = create_vector_store()
        self.skills = SkillLoader(self.vector)
        self.model_router = ModelRouter()
        self.agent_factory = AgentFactory(self.redis, self.model_router)
        self.lifecycle = LifecycleManager(self.agent_factory, self.redis)
        self.rag = RAGManager(self.vector, self.redis)
        self.registry = AgentRegistry(self.redis)
        self.scheduler = Scheduler(self.agent_factory, self.lifecycle)
        self.snapshot = WorkflowSnapshot()
        self.scorer = AutoScorer()
        self.audit = AuditLogger(self.mongo)
        self.detector = FailureDetector()
        self.prompt_opt = PromptOptimizer()
        self.workflow_opt = WorkflowOptimizer()
        self.ab = ABTester()
        self.skill_gen = SkillAutoGenerator()

        self.pool = None
        self.workflow = None
        self.log = TraceLogger("Commander")

    # ═══════════════════════════════════════════════
    # 宪法审查 + 任务路由 (v1.06 核心改动)
    # ═══════════════════════════════════════════════
    def handle_task(self, data: dict):
        tid = data.get("taskId", f"auto-{int(time.time())}")
        p = data.get("payload", {})
        a = p.get("action", "unknown")

        # ── Trace ID (贯穿全链路) ──
        import uuid
        trace_id = data.get("trace_id") or str(uuid.uuid4())[:12]
        data["trace_id"] = trace_id
        p["_trace_id"] = trace_id

        # ── ⚖️ 宪法审查 ──
        verdict, reason = self.constitution.review(a, p)
        stats = self.pool.stats() if self.pool else {}
        self.log.info("handle_task", "宪法审查", trace_id=trace_id, action=a, verdict=verdict.value, reason=reason[:60], queue_depth=stats.get("queue_depth",0) if stats else 0)

        if verdict == Verdict.REJECTED:
            # 严重违宪 — 拒绝执行
            self._publish_result(tid, data, {
                "error": f"宪法拒绝: {reason}",
                "verdict": "rejected",
                "constitution_advice": "该操作被宪法拒绝。请通过 Dashboard 或 API 提交任务，系统将自动走 L1→L5 流水线。如有疑问，查看 /opt/yaxiio/docs/CONSTITUTION.md"
            }, "rejected")
            return

        if verdict == Verdict.ALLOWED:
            # 系统白名单 — 直通（仅限纯管理操作）
            self._run_allowed(tid, a, p, data)
            return

        # ── Verdict.DELEGATED 或 Verdict.DEGRADED ──
        # 必须走五层流水线
        if verdict == Verdict.DEGRADED:
            p["force_sandbox"] = True

        self._run_delegated(tid, a, p, data)

    def _run_allowed(self, tid, action, payload, data):
        """执行白名单操作（直通，不经过流水线）"""
        def _run():
            try:
                # 只有宪法明确授权的系统操作
                sys_actions = {
                    "session_end":       self._cleanup_sandboxes,
                    "agent_export":      self._export_agents,
                    "agent_import":      lambda: self._import_agents(payload),
                    "skill_export":      self._export_skills,
                    "skill_import":      lambda: self._import_skills(payload),
                    "status":            lambda: self._system_status(),
                }
                if action in sys_actions:
                    result = sys_actions[action]() if action == "session_end" else sys_actions[action]()
                else:
                    result = {"status": "error", "reason": f"未注册的系统操作: {action}"}

                self._publish_result(tid, data, result, "success")
                try: self.task_count += 1
                except: pass
                self.log.info("_run_allowed", "白名单直通完成", trace_id=trace_id, action=action)
            except Exception as e:
                self.log.error("_run_allowed", "白名单异常", trace_id=tid, action=action, error=str(e)[:200])
                self._publish_result(tid, data, {"error": str(e)[:500]}, "error")

        if self.pool:
            self.pool.submit(tid, _run)
        else:
            _run()

    def _run_delegated(self, tid, action, payload, data):
        """通过五层 MCP 流水线执行（宪法强制路由）。

        如果 Neuron 需要异步处理，workflow 返回 "pending" 状态，
        Commander 不等待——回到主循环继续处理其他任务，
        Neuron 完成后通过 Pub/Sub 回传结果触发后续流程。
        """
        def _run():
            try:
                self.log.info("_run_delegated", "路由到流水线", trace_id=trace_id, action=action)

                enriched_payload = dict(payload)
                enriched_payload["_arsenal_tools"] = self.arsenal.list_tools()
                enriched_payload["_force_sandbox"] = payload.get("force_sandbox", False)

                result = self.workflow.process(tid, enriched_payload)

                l4_status = result.get("l4_result", {}).get("status", "")
                if l4_status == "pending":
                    # 异步模式: Neuron 在处理中, 保存上下文待回调
                    self._save_pending_task(tid, data, result)
                    self.log.info("_run_delegated", "Neuron异步处理", trace_id=tid, action=action, status="pending")
                    return

                self._publish_result(tid, data, result, "success")
                try: self.task_count += 1
                except: pass
                l5 = result.get("l5_result", {})
                score = l5.get("overall", "?")
                self.log.info("_run_delegated", "流水线完成", trace_id=trace_id, action=action, l5_score=score)
            except Exception as e:
                self.log.error("_run_delegated", "流水线异常", trace_id=tid, action=action, error=str(e)[:200])
                self._publish_result(tid, data, {"error": str(e)[:500]}, "error")

        if self.pool:
            self.pool.submit(tid, _run)
        else:
            _run()

    def _save_pending_task(self, tid: str, original_data: dict, partial_result: dict):
        """保存等待 Neuron 回调的任务上下文。"""
        ctx = {
            "task_id": tid,
            "original_data": original_data,
            "partial_result": partial_result,
            "saved_at": time.time(),
        }
        try:
            self.redis.client.setex(
                f"yaxiio:pending:{tid}",
                600,  # 10 分钟超时
                json.dumps(ctx, ensure_ascii=False, default=str),
            )
        except Exception as e:
            print(f"[雅溪] ⚠️ 保存 pending 任务失败: {e}", flush=True)

    def _resume_pending_task(self, tid: str, neuron_response: dict):
        """Neuron 回调到达，继续执行 L5 评分。"""
        try:
            raw = self.redis.client.get(f"yaxiio:pending:{tid}")
            if not raw:
                print(f"[雅溪] ⚠️ 找不到 pending 任务: {tid}", flush=True)
                return
            ctx = json.loads(raw)
            state = ctx["partial_result"]
            original_data = ctx["original_data"]

            # 用 Neuron 的响应替换 L4 结果
            pl = neuron_response.get("payload", {})
            # 汇总 neuron 的所有输出: thought + result.executed_commands
            stdout_parts = []
            thought = str(pl.get("thought", ""))
            result_data = pl.get("result", {})
            if isinstance(result_data, dict):
                for cmd in result_data.get("executed_commands", []):
                    if isinstance(cmd, dict):
                        stdout_parts.append(f"[cmd:{cmd.get('command','')}]:\n{cmd.get('stdout','')}\n")
            full_output = thought + "\n\n" + "\n".join(stdout_parts)
            state["l4_result"] = {
                "agent_id": neuron_response.get("from", "unknown"),
                "status": pl.get("status", "unknown"),
                "stdout": full_output[:20000],
                "stderr": "",
                "exit_code": 0,
                "elapsed_ms": int((time.time() - ctx["saved_at"]) * 1000),
            }

            # L5 评分
            action = state.get("action", "unknown")
            plan = state.get("plan", {})
            l5 = self.workflow._do_L5(tid, action, plan, state["l4_result"], state)
            state["l5_result"] = l5

            self.log.info("_resume_pending", "异步任务完成", trace_id=tid, action="callback", l5_score=l5.get("overall", "?"))
            self._publish_result(tid, original_data, state, "success")
            try: self.task_count += 1
            except: pass

            # 清理 pending 标记
            self.redis.client.delete(f"yaxiio:pending:{tid}")
        except Exception as e:
            print(f"[雅溪] ❌ 恢复 pending 任务失败 {tid}: {e}", flush=True)

    # ═══════════════════════════════════════════════
    # 结果回调
    # ═══════════════════════════════════════════════
    def _publish_result(self, task_id: str, data: dict, result, status: str = "success"):
        reply_to = data.get("replyTo", "")
        from_who = data.get("from", "unknown")

        response = {
            "type": "response",
            "taskId": task_id,
            "from": "yaxiio",
            "trace_id": data.get("trace_id", ""),
            "to": from_who,
            "payload": {
                "status": status,
                "result": result if isinstance(result, dict) else {"data": str(result)[:2000]},
                "completed_at": time.time()
            }
        }

        if reply_to:
            try:
                self.redis.publish(reply_to, response)
                print(f"[雅溪] 📤 → {reply_to} | {task_id}", flush=True)
            except Exception as e:
                print(f"[雅溪] ⚠️ 回复失败: {e}", flush=True)

        try:
            task_key = f"yaxiio:task:{task_id}"
            state = {
                "task_id": task_id, "status": "DONE" if status == "success" else status.upper(),
                "result": result if isinstance(result, dict) else {"data": str(result)[:2000]},
                "completed_at": time.time()
            }
            self.redis.client.setex(task_key, 86400, json.dumps(state, ensure_ascii=False, default=str))
        except Exception as e:
            print(f"[雅溪] ⚠️ 状态机: {e}", flush=True)

    # ═══════════════════════════════════════════════
    # 系统状态
    # ═══════════════════════════════════════════════
    def _system_status(self) -> dict:
        return {
            "version": "0.2.6",
            "uptime": time.time() - self.start_time,
            "tasks": self.task_count,
            "pool": self.pool.stats() if self.pool else {},
            "constitution": self.constitution.stats(),
            "arsenal_tools": self.arsenal.list_tools(),
            "redis": "healthy" if self.redis.client.ping() else "unhealthy",
            "sqlite": self.store.stats(),
            "neurons": self._list_neurons(),
        }

    # ═══════════════════════════════════════════════
    # 神经网络 — 神经元生命周期管理
    # ═══════════════════════════════════════════════

    def spawn_neuron(self, name: str, skill: str, model: str = None, thinking: str = None, task_id: str = "", **kwargs) -> bool:
        """启动一个神经元进程。model/thinking 为 None 时自动从 ModelConfig 取值。
        接受 **kwargs 以兼容容器内 workflow_engine 的扩展参数。
        """
        # Template clone: each task gets a fresh neuron
        if task_id:
            existing = self._find_neuron_by_task(name, task_id)
            if existing:
                print(f"[雅溪] 🧠 神经元 {name}/{task_id} 已在运行 (PID {existing})", flush=True)
                return True
        else:
            existing = self._find_neuron(name)
            if existing:
                print(f"[雅溪] 🧠 神经元 {name} 已在运行 (PID {existing})", flush=True)
                return True

        # 自动选择模型
        if model is None or thinking is None:
            try:
                from model_router_v2 import ModelConfig
                mc = ModelConfig(self.redis)
                cfg = mc.get_agent_config(name, "")
                if model is None:
                    model = cfg.get("model", "deepseek-chat")
                if thinking is None:
                    thinking = cfg.get("thinking", "medium")
            except:
                if model is None:
                    model = "deepseek-chat"
                if thinking is None:
                    thinking = "medium"

        # 从 ResourcePool 获取配置（Redis 统一管理）
        try:
            from resource_pool import resource_pool
            cfg = resource_pool.get_config(name, action or "")
            if model is None:
                model = cfg.get("model", "deepseek-chat")
            if thinking is None:
                thinking = cfg.get("thinking", "medium")
        except:
            if model is None:
                model = "deepseek-chat"
            if thinking is None:
                thinking = "medium"

        # Phase 5: 能力卡片 → AGENT_CONFIG 环境变量
        agent_config_path = ""
        try:
            card_raw = self.redis.client.get(f"agent:card:{name}")
            if card_raw:
                import tempfile, json as _json
                card = _json.loads(card_raw) if isinstance(card_raw, str) else card_raw
                # 注入运行时参数
                card["task_id"] = task_id
                card["model"] = model
                card["thinking"] = thinking
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, prefix=f"agent-{name}-") as tf:
                    _json.dump(card, tf, ensure_ascii=False)
                    agent_config_path = tf.name
        except Exception:
            pass  # 能力卡片不存在时静默降级

        env = {
            **os.environ,
            "AGENT_NAME": name,
            "AGENT_ROLE": name,
            "AGENT_SKILL": skill,
            "TASK_ID": task_id,
            "LLM_MODEL": model,
            "LLM_THINKING": thinking,
            "LLM_API_KEY": os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", "")),
            "TRACE_ID": task_id,
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "REDIS_PASSWORD": os.environ.get("REDIS_PASSWORD", ""),
            "AGENT_CONFIG": agent_config_path,
        }

        print(f"[雅溪] 🧠 神经元 {name} 已激活 (model={model}, thinking={thinking}, skill={skill})", flush=True)

        try:
            proc = subprocess.Popen(
                [sys.executable, "/opt/commander/neuron.py"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            print(f"[雅溪] 🧠 神经元 {name} 已激活 (PID {proc.pid}, skill={skill}, model={model})", flush=True)
            return True
        except Exception as e:
            print(f"[雅溪] ❌ 神经元 {name} 启动失败: {e}", flush=True)
            return False

    def _find_neuron_by_task(self, name: str, task_id: str):
        """Find neuron by name AND task_id"""
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"AGENT_NAME={name}.*TASK_ID={task_id}"],
                capture_output=True, text=True, timeout=3
            )
            pids = result.stdout.strip().split()
            return int(pids[0]) if pids else None
        except:
            return None

    def _find_neuron(self, name: str):
        """查找指定神经元进程 PID"""
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"AGENT_NAME={name}"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().split('\n')[0])
        except:
            pass
        return None

    def _list_neurons(self) -> list:
        """列出所有活跃神经元"""
        neurons = []
        try:
            active = self.redis.client.smembers("commander:agents:active")
            for name in active:
                hb = self.redis.client.get(f"commander:agent:heartbeat:{name}")
                neurons.append({
                    "name": name,
                    "last_heartbeat": float(hb) if hb else 0,
                    "alive": (time.time() - float(hb)) < 120 if hb else False,
                })
        except:
            pass
        return neurons

    def _recover_inflight(self):
        """断点恢复: 重启后扫描未完成任务，重新调度"""
        from modules.shared.foolproof import safe_default, validate_in_range
        from task_state_machine import TaskStateMachine
        sm = TaskStateMachine()
        inflight = sm.list_inflight()
        if not inflight:
            return
        print(f"[雅溪] 🔄 断点恢复: 发现 {len(inflight)} 个未完成任务", flush=True)
        for task in inflight:
            tid = task["task_id"]
            recoverable = sm.get_recoverable_subtasks(tid)
            if recoverable:
                print(f"[雅溪] 🔄 {tid}: 重新调度 {len(recoverable)} 个子任务", flush=True)
                for st in recoverable:
                    agent = st.get("agent", "审计官")
                    skill = self.workflow._agent_skill_map().get(agent, "")
                    self.spawn_neuron(agent, skill)
            else:
                sm.transition(tid, "FAILED", error="Commander restarted, task lost")
                print(f"[雅溪] ❌ {tid}: 无法恢复，已标记失败", flush=True)

    def handle_agent_failure(self, agent_name: str, failure_type: str = "unknown",
                             task_id: str = "", details: str = ""):
        """
        Agent 故障处置 — Commander 不自己修, 派系统医生 Agent
        failure_type: crash | low_quality | slow_response | prompt_drift
        """
        print(f"[雅溪] 🏥 Agent 故障: {agent_name} type={failure_type}", flush=True)

        # 简单重启: Commander 可以直接做 (不涉及业务逻辑)
        if failure_type == "crash":
            skill = self.workflow._agent_skill_map().get(agent_name, "")
            self.spawn_neuron(agent_name, skill)
            print(f"[雅溪] 🔄 {agent_name} 已重启", flush=True)
            return

        # 复杂故障: 派系统医生
        doctor_name = "系统医生"
        self.spawn_neuron(doctor_name, "system-doctor")
        time.sleep(1.5)

        doctor_msg = {
            "type": "task",
            "taskId": f"doctor-{agent_name}-{int(time.time())}",
            "from": "commander",
            "to": doctor_name,
            "replyTo": "lightingmetal:agent:commander",
            "payload": {
                "action": "diagnose_and_fix",
                "patient_agent": agent_name,
                "failure_type": failure_type,
                "details": details[:500],
                "parent_task": task_id,
            }
        }
        try:
            count = self.redis.client.publish(
                f"lightingmetal:agent:{doctor_name}",
                json.dumps(doctor_msg, ensure_ascii=False, default=str)
            )
            if count > 0:
                print(f"[雅溪] 🏥 系统医生已派出 → {agent_name} ({failure_type})", flush=True)
            else:
                print(f"[雅溪] ⚠️ 系统医生无响应, {agent_name} 等待人工介入", flush=True)
        except Exception as e:
            print(f"[雅溪] ❌ 派医生失败: {e}", flush=True)

    # ═══════════════════════════════════════════════
    # 白名单系统操作实现
    # ═══════════════════════════════════════════════
    def _export_agents(self) -> dict:
        agents = {}
        try:
            for key in self.redis.client.keys("agent:meta:*"):
                aid = key.decode() if isinstance(key, bytes) else key
                aid = aid.split(":")[-1]
                ktype = self.redis.client.type(key)
                if ktype == "hash":
                    data = self.redis.client.hgetall(key)
                    agents[aid] = {k: v for k, v in data.items()} if data else {}
                else:
                    data = self.redis.client.get(key)
                    agents[aid] = data or ""
        except Exception as e:
            print(f"[雅溪] export agents error: {e}", flush=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/agents-export-{ts}.json"
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        with open(rp, "w") as f:
            json.dump({"exported_at": ts, "agents": agents}, f, ensure_ascii=False, indent=2, default=str)
        return {"status": "success", "file": rp, "count": len(agents)}

    def _import_agents(self, p: dict) -> dict:
        data = p.get("agents", p.get("data", {}))
        if isinstance(data, str):
            data = json.loads(data)
        count = 0
        for aid, config in data.get("agents", data).items():
            try:
                for k, v in config.items():
                    self.redis.client.hset(f"agent:meta:{aid}", k, str(v))
                count += 1
            except: pass
        return {"status": "success", "imported": count}

    def _export_skills(self) -> dict:
        skills = {}
        for name, skill in self.skills.skills.items():
            skills[name] = {"name": name, "path": skill.get("path", ""), "doc": skill.get("doc", "")[:500]}
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/skills-export-{ts}.json"
        os.makedirs(os.path.dirname(rp), exist_ok=True)
        with open(rp, "w") as f:
            json.dump({"exported_at": ts, "skills": skills}, f, ensure_ascii=False, indent=2)
        return {"status": "success", "file": rp, "count": len(skills)}

    def _import_skills(self, p: dict) -> dict:
        data = p.get("skills", p.get("data", {}))
        if isinstance(data, str): data = json.loads(data)
        count = 0
        for name, config in data.get("skills", data).items():
            skill_dir = os.path.join(
                self.skills.SKILL_DIR if hasattr(self.skills, "SKILL_DIR") else "/opt/commander/skills", name
            )
            os.makedirs(skill_dir, exist_ok=True)
            with open(os.path.join(skill_dir, "SKILL.md"), "w") as f:
                f.write(config.get("doc", config.get("description", "")))
            count += 1
        try: self.skills._load()
        except: pass
        return {"status": "success", "imported": count}

    def _cleanup_sandboxes(self) -> dict:  # DinD SandboxManager
        from sandbox_manager import SandboxManager; return SandboxManager().cleanup_expired()

    def _run_tool(self, tid, script_path: str, payload: dict) -> dict:
        """运行 tools/ 目录下的脚本"""
        full_path = f"/opt/commander/{script_path}"
        if not os.path.exists(full_path):
            return {"status": "error", "reason": f"tool not found: {script_path}"}
        try:
            args = [sys.executable, full_path]
            mode = payload.get("mode", "")
            target = payload.get("target", payload.get("industry", ""))
            if mode: args.append(mode)
            if target: args.append(target)
            proc = subprocess.run(args, capture_output=True, text=True, timeout=120)
            return {"status": "success" if proc.returncode == 0 else "error",
                    "output": proc.stdout[-2000:], "stderr": proc.stderr[-500:]}
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}

    def _restart_nuxt(self) -> dict:
        """部署操作：SSH 到服务器重启 Nuxt。

        凭证从 Redis 沙箱密钥库读取 (`yaxiio:config:deploy:*`)，
        只在 L4 沙箱执行期间注入，执行后不残留。
        支持 Docker 和 PM2 两种部署模式。
        """
        try:
            # 从 Redis 沙箱密钥库读取凭证
            deploy_host = ""
            deploy_password = ""
            deploy_mode = "pm2"  # pm2 或 docker
            try:
                raw = self.redis.client.get("yaxiio:config:deploy:host")
                deploy_host = raw.strip() if raw else ""
                raw = self.redis.client.get("yaxiio:config:deploy:password")
                deploy_password = raw.strip() if raw else ""
                raw = self.redis.client.get("yaxiio:config:deploy:mode")
                deploy_mode = raw.strip() if raw else "pm2"
            except Exception:
                pass

            if not deploy_host or not deploy_password:
                return {"status": "error",
                        "error": "部署凭证未配置: redis-cli SET yaxiio:config:deploy:host|password"}

            # 构建重启命令
            if deploy_mode == "docker":
                restart_cmd = "docker restart nuxt-app && echo RESTART_OK"
            else:
                restart_cmd = "cd /root/customer-portal && git pull && pm2 restart lightingmetal && echo RESTART_OK"

            proc = subprocess.run(
                ["sshpass", "-p", deploy_password, "ssh",
                 "-o", "StrictHostKeyChecking=no",
                 f"root@{deploy_host}", restart_cmd],
                capture_output=True, text=True, timeout=30
            )
            ok = "RESTART_OK" in proc.stdout
            print(f"[雅溪] Nuxt 部署: {'✅' if ok else '❌'} (mode={deploy_mode})", flush=True)
            return {"status": "success" if ok else "error",
                    "output": proc.stdout[-500:], "stderr": proc.stderr[-200:]}
        except Exception as e:
            return {"status": "error", "error": str(e)[:200]}

    # ═══════════════════════════════════════════════
    # Arsenal 工具实现 (由流水线按需调用，Commander 不主动执行)
    # ═══════════════════════════════════════════════

    # ═══════════════════════════════════════════════
    # LLM 客户端
    # ═══════════════════════════════════════════════
    def _get_llm(self, task_type: str = "default", task_desc: str = ""):
        """获取 LLM 客户端 — 委托给 commander_llm"""
        from commander_llm import get_llm_client
        return get_llm_client(self.redis, self.workflow, task_type, task_desc)

    # ═══════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════
    def run(self):
        # 单实例锁: 防止双守护各启一个 Commander
        import redis as _rl
        _r = _rl.Redis(host="127.0.0.1", protocol=2, port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
        if not _r.setnx("yaxiio:commander:lock", str(os.getpid())):
            print("[雅溪] ⚠️ 已有 Commander 运行, 退出", flush=True)
            return
        _r.expire("yaxiio:commander:lock", 120)

        # 锁续期线程: 每 30 秒续期一次, 防止长时间运行后锁过期
        def _renew_lock():
            import redis as _rl2
            _rr = _rl2.Redis(host="127.0.0.1", protocol=2, port=6379,
                            password=os.environ.get("REDIS_PASSWORD", ""),
                            decode_responses=True, socket_connect_timeout=3)
            while self.running:
                try:
                    current = _rr.get("yaxiio:commander:lock")
                    if current and str(current) == str(os.getpid()):
                        _rr.expire("yaxiio:commander:lock", 120)
                except Exception:
                    pass
                time.sleep(30)
        threading.Thread(target=_renew_lock, daemon=True, name="lock-renewal").start()

        self.pool = BoundedThreadPool(max_workers=5, max_queue=20)
        self.workflow = WorkflowEngine(commander=self)
        self.task_count = 0
        async_executor.start()

        for layer in ["L1_perception","L2_planning","L3_coordination","L4_execution","L5_evolution"]:
            try:
                subprocess.Popen(
                    [sys.executable, f"/opt/commander/layers/{layer}/mcp_server.py"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
                )
            except: pass

        # ── 断点恢复: 重启后重新调度未完成的任务 ──
        self._recover_inflight()

    def _recover_inflight(self):
        """Breakpoint resume: scan Redis for EXECUTING tasks and re-dispatch"""
        try:
            inflight = []
            for key in self.redis.client.keys("yaxiio:task:*"):
                try:
                    data = json.loads(self.redis.client.get(key) or "{}")
                    result = data.get("result", data)
                    status = data.get("status", result.get("status", "?"))
                    if status in ("EXECUTING", "SCORING", "RUNNING"):
                        tid = key.replace("yaxiio:task:", "")
                        action = result.get("action", data.get("action", "unknown"))
                        payload = data.get("payload", data.get("result", {}).get("payload", {}))
                        inflight.append((tid, action, payload))
                except: pass
            if inflight:
                print("[雅溪] Found %d incomplete tasks, resuming..." % len(inflight), flush=True)
                for tid, action, payload in inflight:
                    print("[雅溪] Resuming: %s (%s)" % (tid, action), flush=True)
                    self._run_delegated(tid, action, payload, {"taskId": tid, "from": "recovery"})
        except Exception as e:
            print("[雅溪] Recovery error: %s" % str(e)[:100], flush=True)

        self.log.info("run", "Commander启动", version="0.2.6")
        self.log.info("run", "宪法激活", whitelist_ops=6, arsenal_tools=12)
        self.log.info("run", "订阅频道", channels="yaxiio:agent:commander,lightingmetal:agent:commander")

        cycle = 0
        while self.running:
            pubsub = None
            try:
                try: self.redis.client.ping()
                except: self.redis = RedisClient()

                pubsub = self.redis.client.pubsub()
                pubsub.subscribe("yaxiio:agent:commander", "lightingmetal:agent:commander")
                deadline = time.time() + 55

                while time.time() < deadline:
                    msg = pubsub.get_message(timeout=2.0)
                    if msg and msg["type"] == "message":
                        try:
                            raw = msg["data"]
                            if isinstance(raw, bytes): raw = raw.decode("utf-8")
                            data = json.loads(raw)
                            msg_type = data.get("type", "")
                            if msg_type == "task":
                                self.handle_task(data)
                            elif msg_type == "response":
                                # Neuron 异步回调 → 继续流水线
                                tid = data.get("taskId", "")
                                if self.redis.client.exists(f"yaxiio:pending:{tid}"):
                                    self._resume_pending_task(tid, data)
                        except json.JSONDecodeError: pass
                        except Exception as e:
                            print(f"[雅溪] Task error: {e}", flush=True)

                cycle += 1
                if cycle % 10 == 0:
                    stats = self.pool.stats()
                    cstats = self.constitution.stats()
                    print(f"[雅溪] Cycle {cycle}, tasks: {self.task_count}, "
                          f"q={stats['queue_depth']}/{stats['max_queue']} "
                          f"⚖️cmp={cstats['compliance_rate']:.0%} "
                          f"viol={cstats['violations']}", flush=True)

            except Exception as e:
                print(f"[雅溪] Cycle error: {e}", flush=True)
                time.sleep(3)
            finally:
                try: pubsub.close()
                except: pass


def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    os.makedirs(LOG_DIR, exist_ok=True)
    Commander().run()


if __name__ == "__main__":
    main()
