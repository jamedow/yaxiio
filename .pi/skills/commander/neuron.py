#!/usr/bin/env python3
"""
雅溪 神经元 Neuron v1.0 — Agent 的真正运行时
=============================================
每个神经元是一个独立进程，拥有:
  - 感知器: Redis Pub/Sub 频道监听
  - 大脑:   LLM (DeepSeek) + Skill (system prompt)
  - 双手:   工具调用 (browser, file, subprocess)
  - 嘴巴:   结果发布回 Redis

生命周期:
  SUBSCRIBE → RECEIVE → LOAD_SKILL → LLM_THINK → EXECUTE → RESPOND → LEARN

用法:
  AGENT_NAME=UI/UX设计师 AGENT_SKILL=ui-ux-designer LLM_MODEL=deepseek-chat python3 neuron.py
"""

import json, os, sys, time, re, traceback, threading, subprocess, tempfile, shlex
from datetime import datetime

# 确保可以导入 Commander 模块
sys.path.insert(0, "/opt/commander")
sys.path.insert(0, "/opt/yaxiio/.pi/skills/commander")

# ── Stream 支持 ──
try:
    from stream_bridge import StreamBridge
    HAS_STREAM = True
except ImportError:
    HAS_STREAM = False

# ── Redis ──
try:
    from trace_logger import TraceLogger
    import redis as _redis
    HAS_REDIS = True
    nlog = TraceLogger("Neuron")
except ImportError:
    HAS_REDIS = False

# ── LLM ──
try:
    from openai import OpenAI
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

# ── 配置 ──
AGENT_NAME   = os.environ.get("AGENT_NAME", "neuron")
AGENT_ROLE   = os.environ.get("AGENT_ROLE", AGENT_NAME)
AGENT_SKILL  = os.environ.get("AGENT_SKILL", "")
SKILL_DIR    = os.environ.get("SKILL_DIR", "/opt/yaxiio/.pi/skills")
CHANNEL      = f"lightingmetal:agent:{AGENT_NAME}"
CONTROL_CH   = "lightingmetal:agent:commander"

REDIS_HOST   = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT   = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS   = os.environ.get("REDIS_PASSWORD", "$REDIS_PASSWORD")

LLM_API_KEY  = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL    = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_THINKING = os.environ.get("LLM_THINKING", "medium")  # thinking mode: off/low/medium/high/max
TASK_ID      = os.environ.get("TASK_ID", "")

# Template clone: each task gets fresh isolated memory
MEMORY_KEY   = f"agent:{AGENT_NAME}:{TASK_ID}:memory" if TASK_ID else f"agent:{AGENT_NAME}:memory"
PROMPT_KEY   = f"agent:{AGENT_NAME}:prompt"

log_prefix = f"[{AGENT_NAME}]"


def log(msg: str):
    print(f"{log_prefix} {msg}", flush=True)


# ═══════════════════════════════════════════════════════════════
# Neuron 核心
# ═══════════════════════════════════════════════════════════════

class Neuron:
    def __init__(self):
        self.name = AGENT_NAME
        self.role = AGENT_ROLE
        self.skill_name = AGENT_SKILL
        self.running = True
        self.task_count = 0
        self.system_prompt = ""
        self.memory: list = []  # 最近的对话记忆

        # ── 1. 连接 Redis (感知器) ──
        self.redis = None
        if HAS_REDIS:
            try:
                self.redis = _redis.Redis(
                    host=REDIS_HOST, port=REDIS_PORT,
                    password=REDIS_PASS, decode_responses=True,
                    socket_connect_timeout=5
                )
                self.redis.ping()
                log("🧠 感知器已连接 (Redis)")
            except Exception as e:
                log(f"⚠️ Redis 连接失败: {e}")

        # ── 2. 连接 LLM (大脑) ──
        self.llm = None
        self._model_used = LLM_MODEL
        self._thinking_used = LLM_THINKING

        # 多模型 API Key 查找链: Agent专属 → 能力卡片 → 全局 → 环境变量
        api_key = ""
        api_base = LLM_BASE_URL
        if self.redis:
            try:
                # 1. Agent 专属 Key (agent:apikey:审计官)
                agent_key = self.redis.get(f"agent:apikey:{self.name}")
                if agent_key:
                    api_key = agent_key
                    # Agent 专属 base_url (可选)
                    agent_base = self.redis.get(f"agent:baseurl:{self.name}")
                    if agent_base:
                        api_base = agent_base
                # 2. 能力卡片里的 key
                if not api_key and self.card:
                    api_key = self.card.get("api_key", "")
                    if self.card.get("base_url"):
                        api_base = self.card["base_url"]
                # 3. 全局 fallback
                if not api_key:
                    api_key = self.redis.get("yaxiio:config:llm_api_key") or ""
            except:
                pass
        # 4. 环境变量 (最后兜底)
        if not api_key:
            api_key = LLM_API_KEY

        if HAS_LLM and api_key:
            try:
                self.llm = OpenAI(api_key=api_key, base_url=api_base)
                log(f"🧠 大脑已连接 ({LLM_MODEL}, thinking={LLM_THINKING}, provider={api_base})")
            except Exception as e:
                log(f"⚠️ LLM 连接失败: {e}")
        else:
            log(f"🚨 严重: 无可用 API Key! LLM调用将全部失败. 请检查Redis yaxiio:config:llm_api_key")

        # 3. Load Skill
        self._load_skill()
        # 3b. Load Capability Card (Agent System v2)
        self.card = self._load_capability_card()
        # 4. Stream 桥接
        self.stream = None
        if HAS_STREAM:
            try:
                self.stream = StreamBridge(
                    redis_host=REDIS_HOST, redis_port=REDIS_PORT,
                    redis_password=REDIS_PASS)
            except Exception:
                pass
        self.state = "IDLE"
        self.state_since = time.time()
        self.retry_count = 0
        self.task_start_time = 0
        self.task_timeout = 300
        self.max_retries = 3
        if self.card:
            self.task_timeout = self.card.get("lifecycle", {}).get("task_timeout", 300)
            self.max_retries = self.card.get("lifecycle", {}).get("max_retries", 3)
            log("CARD: " + self.card.get("name","?") + " v" + self.card.get("version","?"))
        # 4. Load Memory
        self._load_memory()

        # ── 5. 注册心跳 ──
        self._register()

    def _load_capability_card(self) -> dict:
        config_path = os.environ.get("AGENT_CONFIG", "")
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    return json.load(f)
            except:
                pass
        if self.redis:
            try:
                raw = self.redis.get(f"agent:card:{self.name}")
                if raw:
                    return json.loads(raw)
            except:
                pass
        return {}

    def _set_state(self, new_state: str):
        old = self.state
        self.state = new_state
        self.state_since = time.time()
        if self.redis:
            try:
                self.redis.setex(f"agent:{self.name}:state", 300,
                    json.dumps({"state": new_state, "since": self.state_since, "retries": self.retry_count}))
            except:
                pass
        log("STATE: " + old + " -> " + new_state)
        if self.redis and new_state == "EXECUTING":
            self.redis.setex(f"agent:{self.name}:{os.environ.get('TASK_ID','')}:state", 3600, json.dumps({"progress": 0, "ts": time.time()}))

    def _report_progress(self, task_id: str, pct: int, msg: str = ""):
        """向 Commander 汇报执行进度 — 防止误判超时"""
        if self.redis:
            try:
                self.redis.setex(f"agent:{self.name}:{task_id}:state", 3600,
                    json.dumps({"progress": pct, "ts": time.time(), "msg": msg}))
            except:
                pass
        if msg:
            log(f"PROGRESS {task_id} {pct}%: {msg}")

    def _save_private_notes(self, task_id: str, action: str, thought, result, elapsed: float):
        """保存私有笔记"""
        if not self.redis: return
        try:
            note = json.dumps({"task_id":task_id,"action":action,"ts":time.time(),"elapsed_s":round(elapsed,1),
                "thinking":LLM_THINKING,"model":LLM_MODEL,"summary":str(thought)[:500],
                "tools":[c.get("cmd","")[:80] for c in result.get("executed_commands",[])[:5]]},
                ensure_ascii=False,default=str)
            key = f"agent:{self.name}:notes:{action}"
            self.redis.lpush(key, note)
            self.redis.ltrim(key, 0, 49)
            log(f"NOTES saved")
        except Exception as e:
            log(f"NOTES error: {e}")

    def _load_private_notes(self, action: str, limit: int = 3) -> list:
        """加载私有笔记"""
        if not self.redis: return []
        try:
            raw = self.redis.lrange(f"agent:{self.name}:notes:{action}", 0, limit-1)
            return [json.loads(r) for r in raw if r]
        except: return []

    def _load_skill(self):
        """加载 Skill 作为 system prompt"""
        if not self.skill_name:
            self.system_prompt = f"You are {self.name}, an AI assistant for ExampleCorp B2B platform."
            return

        skill_path = os.path.join(SKILL_DIR, self.skill_name, "SKILL.md")
        if os.path.exists(skill_path):
            try:
                with open(skill_path) as f:
                    self.system_prompt = f.read()
                log(f"📚 已加载 Skill: {self.skill_name} ({len(self.system_prompt)} chars)")
            except Exception as e:
                log(f"⚠️ Skill 加载失败: {e}")
        else:
            log(f"⚠️ Skill 文件不存在: {skill_path}")

        # 加载经验数据
        exp_dir = os.path.join(SKILL_DIR, self.skill_name, "experience")
        if os.path.isdir(exp_dir):
            for fn in os.listdir(exp_dir):
                if fn.endswith(".json"):
                    try:
                        with open(os.path.join(exp_dir, fn)) as f:
                            exp = json.load(f)
                        log(f"📚 经验: {fn} ({len(str(exp))} chars)")
                    except:
                        pass

    def _load_memory(self):
        """从 Redis 加载记忆"""
        if not self.redis:
            return
        try:
            raw = self.redis.get(MEMORY_KEY)
            if raw:
                self.memory = json.loads(raw)
                log(f"🧠 记忆加载: {len(self.memory)} 条")
        except:
            pass

    def _save_memory(self):
        """保存记忆到 Redis"""
        if not self.redis:
            return
        try:
            # 只保留最近 20 条
            if len(self.memory) > 20:
                self.memory = self.memory[-20:]
            self.redis.setex(MEMORY_KEY, 86400, json.dumps(self.memory, ensure_ascii=False))
        except:
            pass

    def _register(self):
        """注册到 Redis"""
        if not self.redis:
            return
        try:
            self.redis.sadd("commander:agents:active", self.name)
            self.redis.setex(f"commander:agent:heartbeat:{self.name}", 120, str(time.time()))
            log("📡 已注册到神经网络")
        except:
            pass

    def _heartbeat(self):
        """心跳"""
        if self.redis:
            try:
                self.redis.setex(f"commander:agent:heartbeat:{self.name}", 120, str(time.time()))
            except:
                pass

    def _publish(self, channel: str, data: dict):
        """发布消息"""
        if self.redis:
            try:
                self.redis.publish(channel, json.dumps(data, ensure_ascii=False, default=str))
            except Exception as e:
                log(f"⚠️ 发布失败: {e}")

    # ═══════════════════════════════════════════════
    # 思考 → 行动 循环
    # ═══════════════════════════════════════════════

    def think_and_act(self, task: dict) -> dict:
        trace_id = os.environ.get("TRACE_ID", task.get("taskId", ""))
        """Core loop with state machine (v2) — 主动汇报进度"""
        self._set_state("EXECUTING")
        self.task_start_time = time.time()
        task_id = task.get("taskId", f"auto-{int(time.time())}")
        payload = task.get("payload", {})
        action = payload.get("action", "unknown")
        reply_to = task.get("replyTo", CONTROL_CH)

        log(f"RECV {task_id} action={action}")
        self._report_progress(task_id, 5, "开始分析任务")

        context = {
            "agent": self.name,
            "role": self.role,
            "task_id": task_id,
            "action": action,
            "payload": {k: v for k, v in payload.items()
                       if not k.startswith("_") and k != "context"},
            "recent_memory": self.memory[-5:] if self.memory else [],
        }

        # Step 1: LLM think
        self._report_progress(task_id, 20, "LLM 思考中...")
        thought = self._llm_think(context)
        self._report_progress(task_id, 50, "LLM 思考完成")

        # Step 2: Execute commands from thought
        self._report_progress(task_id, 60, "执行命令...")
        result = self._execute(thought, context)

        # Step 3: TOOL FEEDBACK LOOP
        cmd_outputs = result.get("executed_commands", [])
        final_thought = thought
        if cmd_outputs:
            self._report_progress(task_id, 75, "工具反馈分析中...")
            feedback_context = dict(context)
            feedback_context["tool_results"] = cmd_outputs
            feedback_context["previous_thought"] = str(thought)[:500]
            try:
                analysis = self._llm_analyze_results(feedback_context)
                if analysis and len(analysis) > 20:
                    final_thought = analysis
                    result2 = self._execute(analysis, context)
                    if result2.get("executed_commands"):
                        result["executed_commands"].extend(result2["executed_commands"])
            except Exception as e:
                log(f"Tool feedback error: {e}")

        # Step 4: Save to memory
        self.memory.append({
            "ts": time.time(),
            "task_id": task_id,
            "action": action,
            "summary": str(final_thought)[:300],
        })
        self._save_memory()

        # Step 5: Respond
        response = {
            "type": "response",
            "taskId": task_id,
            "from": self.name,
            "to": task.get("from", "commander"),
            "payload": {
                "status": "success",
                "agent": self.name,
                "thought": str(final_thought)[:500] if final_thought else "",
                "result": result,
                "completed_at": time.time(),
            }
        }

        # Stream 发布响应（优先，消息持久化不丢失）
        if self.stream:
            try:
                self.stream.publish_task('L4_response', response, task_id)
            except Exception:
                pass
        # Pub/Sub 回退
        if reply_to:
            self._publish(reply_to, response)

        self.task_count += 1
        elapsed = time.time() - self.task_start_time

        # ── 保存私有笔记 (Agent 经验积累) ──
        self._save_private_notes(task_id, action, final_thought, result, elapsed)
        if elapsed > self.task_timeout:
            log(f"TIMEOUT {task_id} ({elapsed:.0f}s > {self.task_timeout}s)")
            self._set_state("TIMEOUT")
            if self.retry_count < self.max_retries:
                self.retry_count += 1
                self._set_state("RECOVERING")
                time.sleep(2 ** self.retry_count)  # exponential backoff
            else:
                self._set_state("FAULT")
        else:
            self._set_state("IDLE")
            self.retry_count = 0
        log(f"DONE {task_id}")
        return result

    def _load_mcp_tools(self) -> str:
        """Load MCP server tools from registry for this agent"""
        if not self.redis or not self.card:
            return ""
        servers = self.card.get("mcp_servers", [])
        if not servers:
            return ""
        lines = ["## MCP Tools (network-capable)", ""]
        for srv in servers:
            try:
                meta = self.redis.hget("mcp:registry", srv)
                if meta:
                    info = json.loads(meta)
                    tools = info.get("tools", [])
                    lines.append("- %s: %s" % (srv, ", ".join(tools)))
            except:
                pass
        return "\n".join(lines) if len(lines) > 2 else ""

    def _load_tool_descriptions(self) -> str:
        """Load tool descriptions from Redis registry (hot-pluggable)"""
        tools = self._discover_tools()
        if not tools:
            return ""
        lines = ["## Available Tools", "", "Run via bash: python3 /opt/commander/tools/<name>.py [args]", ""]
        for t in tools:
            lines.append("- **%s**: %s" % (t.get("name","?"), t.get("desc","")[:80]))
            if t.get("usage"):
                lines.append("  `%s`" % t["usage"])
        return "\n".join(lines)

    def _discover_tools(self) -> list:
        """Discover available tools from Redis registry"""
        tools = []
        if not self.redis:
            return tools
        try:
            # Get tools assigned to this agent
            tool_names = self.redis.smembers("tools:agent:%s" % self.name)
            if not tool_names:
                # Fallback to capability card
                card_tools = self.card.get("tools", [])
                tool_names = set(card_tools)
            for name in tool_names:
                desc_raw = self.redis.hget("tools:registry", name)
                if desc_raw:
                    tools.append(json.loads(desc_raw))
        except:
            pass
        return tools

    def _llm_think(self, context: dict) -> str:
        """LLM analysis with tool access"""
        if not self.llm:
            return self._mock_think(context)

        sys_prompt = self.system_prompt[:3000]
        tools_desc = self._load_tool_descriptions()
        mcp_desc = self._load_mcp_tools()
        if mcp_desc:
            tools_desc += "\n" + mcp_desc

        user_prompt = f"""Task: {json.dumps(context, ensure_ascii=False, default=str)[:2000]}

{tools_desc}

As {self.name}, analyze this task and provide a concrete output.
IMPORTANT: You have REAL tools. To use them, put bash commands in ```bash blocks.
- Audit: python3 /opt/commander/tools/multilang_audit.py
- Query MongoDB: python3 /opt/commander/tools/mongo_query.py --industry power --lang en
- Query Redis: python3 /opt/commander/tools/redis_query.py --key page:industries:power:*
- Check terms: python3 /opt/commander/tools/terminology_check.py --industry power
- Fix: python3 /opt/commander/tools/fix_executor.py
- Verify: python3 /opt/commander/tools/verify_page.py [url]
- Sync: python3 /opt/commander/tools/content_sync.py full

RUN the tools to get real data. Then analyze the results. Do NOT just describe what you WOULD do."""

        try:
            extra = {}
            if LLM_THINKING and LLM_THINKING != "off":
                extra["reasoning_effort"] = LLM_THINKING

            resp = self.llm.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                extra_body=extra if extra else None,
            )
            return resp.choices[0].message.content
        except Exception as e:
            log(f"LLM call failed: {e}")
            return self._mock_think(context)

    def _llm_analyze_results(self, context: dict) -> str:
        """Feed tool execution results back to LLM for analysis"""
        if not self.llm:
            return ""
        try:
            tool_outputs = json.dumps(context.get("tool_results", [])[:3], ensure_ascii=False, default=str)[:1500]
            prev = context.get("previous_thought", "")[:300]
            
            prompt = f"""Previous plan: {prev}

TOOL EXECUTION RESULTS:
{tool_outputs}

Based on these REAL results, provide your final analysis and findings.
- Include specific data from the tool outputs (numbers, paths, counts).
- If the tool found issues, list them with severity.
- If the tool output is empty/error, explain what you would check.
- Output in Chinese with structured findings.
- If you need to run MORE tools, include their bash commands in ```bash blocks."""
            
            extra = {}
            if LLM_THINKING and LLM_THINKING != "off":
                extra["reasoning_effort"] = LLM_THINKING
            
            resp = self.llm.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": self.system_prompt[:2000]},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1500,
                extra_body=extra if extra else None,
            )
            return resp.choices[0].message.content
        except Exception as e:
            log(f"Analyze error: {e}")
            return ""

    def _mock_think(self, context: dict) -> str:
        """无 LLM 时的模拟思考"""
        action = context.get("action", "unknown")
        return f"[模拟模式] 收到 {action} 任务，Agent {self.name} 已处理。技能: {self.skill_name}"

    def _execute_mcp_tool(self, server: str, tool: str, args: dict) -> dict:
        """Execute an MCP tool via the MCPManager"""
        try:
            sys.path.insert(0, "/app/.pi/skills/commander")
            from mcp_manager import MCPManager
            mgr = MCPManager(self.redis)
            # Build command from registry
            meta_raw = self.redis.hget("mcp:registry", server)
            if not meta_raw:
                return {"error": "server not found"}
            meta = json.loads(meta_raw)
            cmd = [meta["command"]] + meta.get("args", [])
            # Pass tool+args via stdin
            import subprocess
            proc = subprocess.run(cmd, input=json.dumps({"tool": tool, "args": args}),
                                capture_output=True, text=True, timeout=30)
            return {"stdout": proc.stdout[:500], "stderr": proc.stderr[:200], "ok": proc.returncode == 0}
        except Exception as e:
            return {"error": str(e)[:200]}

    def _execute(self, thought: str, context: dict) -> dict:
        """执行 LLM 生成的方案"""
        # 检查是否包含可执行命令
        commands = self._extract_commands(thought)

        result = {
            "agent": self.name,
            "output": thought[:1000] if thought else "",
            "executed_commands": [],
        }

        for cmd in commands:
            try:
                safe_cmd = shlex.split(cmd) if isinstance(cmd, str) else cmd
                proc = subprocess.run(safe_cmd, shell=False, capture_output=True,
                                      text=True, timeout=30, cwd="/tmp")
                result["executed_commands"].append({
                    "cmd": cmd,
                    "stdout": proc.stdout[:500],
                    "stderr": proc.stderr[:200],
                    "exit": proc.returncode,
                })
            except Exception as e:
                result["executed_commands"].append({"cmd": cmd, "error": str(e)})

        return result

    def _extract_commands(self, thought: str) -> list:
        """从 LLM 输出中提取 shell 命令"""
        cmds = []
        # 匹配 ```bash ... ``` 代码块
        for match in re.finditer(r'```(?:bash|sh|shell)?\n(.*?)```', thought, re.DOTALL):
            for line in match.group(1).strip().split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    cmds.append(line)
        return cmds[:3]  # 最多执行3条命令

    # ═══════════════════════════════════════════════
    # 主循环: 订阅频道，处理任务
    # ═══════════════════════════════════════════════

    def run(self):
        log(f"⚡ 神经元激活 | 频道: {CHANNEL} | 技能: {self.skill_name or '无'}")

        if not self.redis:
            log("❌ 无 Redis，无法运行")
            return

        # 启动心跳线程
        def heartbeat_loop():
            while self.running:
                self._heartbeat()
                time.sleep(30)
        threading.Thread(target=heartbeat_loop, daemon=True).start()

        # 消息循环
        cycle = 0
        while self.running:
            pubsub = None
            try:
                # Stream 优先消费 (消息持久化，不丢失)
                if self.stream:
                    try:
                        stream_tasks = self.stream.consume_task(
                            self.name, layer="L4", block_ms=1000, count=1)
                        for task in stream_tasks:
                            data = task.get("payload", task)
                            if isinstance(data, str):
                                data = json.loads(data)
                            data["_stream"] = task.get("_stream", "")
                            data["_stream_id"] = task.get("_stream_id", "")
                            data["_group"] = task.get("_group", "")
                            try:
                                self.think_and_act(data)
                                self.stream.ack_task(self.name, task)
                            except Exception as e:
                                log(f"Stream 任务异常: {e}")
                    except Exception:
                        pass

                # Pub/Sub 回退
                pubsub = self.redis.pubsub()
                pubsub.subscribe(CHANNEL, CONTROL_CH)
                deadline = time.time() + 55

                while time.time() < deadline and self.running:
                    msg = pubsub.get_message(timeout=1.0)
                    if not msg or msg["type"] != "message":
                        continue

                    channel = msg.get("channel", "")
                    raw = msg.get("data", "")
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")

                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = data.get("type", "")

                    # ── 控制消息 ──
                    if msg_type == "shutdown":
                        log("🛑 收到关闭指令")
                        self.running = False
                        break
                    elif msg_type == "heartbeat_check":
                        self._heartbeat()
                        continue

                    # ── 任务消息 ──
                    if msg_type in ("task", "request"):
                        # 过滤: 只处理发给自己的
                        to = data.get("to", "")
                        if to and to != self.name and to != "*" and to != "all":
                            continue
                        try:
                            self.think_and_act(data)
                        except Exception as e:
                            log(f"❌ 任务处理异常: {e}")
                            traceback.print_exc()

                cycle += 1
                if cycle % 20 == 0:
                    log(f"💓 Cycle {cycle}, tasks: {self.task_count}")

            except Exception as e:
                log(f"⚠️ 循环异常: {e}")
                time.sleep(3)
            finally:
                try:
                    pubsub.close()
                except:
                    pass

        # 清理
        if self.redis:
            try:
                self.redis.srem("commander:agents:active", self.name)
                self.redis.delete(f"commander:agent:heartbeat:{self.name}")
            except:
                pass
        log(f"👋 神经元下线, 共处理 {self.task_count} 个任务")


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    import signal as _signal
    _signal.signal(_signal.SIGTERM, lambda *_: sys.exit(0))
    _signal.signal(_signal.SIGINT, lambda *_: sys.exit(0))

    neuron = Neuron()
    neuron.run()


if __name__ == "__main__":
    main()
