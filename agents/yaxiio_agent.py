#!/usr/bin/env python3
"""
雅溪 Yaxiio Agent v1.0 — LLM 深度集成执行循环
================================================
自进化 Agent：收到任务 → LLM思考 → 生成方案 → 执行 → LLM评估 → 自我优化

循环:
  RECEIVE → THINK → PLAN → EXECUTE → EVALUATE → LEARN → RESPOND

LLM 集成:
  通过 OpenAI-compatible API (DeepSeek) 调用 LLM
  每次任务执行前后都经过 LLM 推理

配置:
  LLM_API_KEY    — API Key
  LLM_BASE_URL   — API 地址 (默认 DeepSeek)
  LLM_MODEL      — 模型名
  AGENT_NAME     — Agent 标识
  AGENT_ROLE     — Agent 角色 (影响 LLM system prompt)
"""

import json
import os
import time
import subprocess
import sys
import traceback
from datetime import datetime

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    from openai import OpenAI
    HAS_LLM = True
except ImportError:
    HAS_LLM = False


# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

AGENT_NAME = os.environ.get("AGENT_NAME", "yaxiio-agent")
AGENT_ROLE = os.environ.get("AGENT_ROLE", "general")
CHANNEL = f"lightingmetal:agent:{AGENT_NAME}"
CONTROL_CHANNEL = "lightingmetal:agent:commander"
RESULT_CHANNEL = "agent:result"

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD")  # 127.0.0.1 only

LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")

MEMORY_KEY = f"agent:{AGENT_NAME}:memory"
PROMPT_KEY = f"agent:{AGENT_NAME}:prompt"


# ═══════════════════════════════════════════════════════════════
# YaxiioAgent — LLM 深度集成
# ═══════════════════════════════════════════════════════════════

class YaxiioAgent:
    """雅溪 Agent — 收任务 → LLM思考 → 执行 → 评估 → 进化。"""

    def __init__(self):
        # Redis
        self.redis = None
        if HAS_REDIS:
            try:
                self.redis = redis_lib.Redis(
                    host=REDIS_HOST, port=REDIS_PORT,
                    password=REDIS_PASS if REDIS_PASS else None, decode_responses=True
                )
                self.redis.ping()
            except Exception:
                pass

        # LLM
        self.llm = None
        if HAS_LLM and LLM_API_KEY:
            try:
                self.llm = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)
            except Exception:
                pass

        # 状态
        self.running = True
        self.task_count = 0
        self.fail_count = 0
        self.start_time = time.time()
        self.role_prompt = self._load_role_prompt()

    def log(self, msg, level="INFO"):
        print(f"[{AGENT_NAME}] [{level}] {msg}", flush=True)

    def _load_role_prompt(self) -> str:
        """加载或生成角色提示词。"""
        if self.redis:
            saved = self.redis.get(PROMPT_KEY)
            if saved:
                return saved

        prompts = {
            "翻译官": "你是专业翻译专家。收到文本后翻译为指定语言。输出简洁准确。",
            "商务经理": "你是外贸商务经理。分析客户需求，输出结构化需求清单。",
            "售前经理": "你是售前技术经理。根据产品规格生成报价方案。",
        }
        return prompts.get(AGENT_ROLE, f"你是网站审计专家。你拥有浏览器自动化工具。在commands数组中使用简单指令: browser_navigate URL / browser_get_title / browser_extract_text / browser_evaluate CODE / browser_extract_html / browser_extract_links / browser_screenshot / browser_close。示例: browser_navigate https://example.com")

    # ═══════════════════════════════════════════════════════════
    # LLM 推理循环
    # ═══════════════════════════════════════════════════════════

    def think(self, task: str, context: dict = None) -> dict:
        """🟡 THINK: LLM 分析任务，生成执行思路。"""
        if not self.llm:
            return {"reasoning": "no-llm", "plan": [task]}

        prompt = self.role_prompt + chr(10) + chr(10)
        prompt += "当前任务: " + task[:500] + chr(10)
        prompt += "上下文: " + json.dumps(context or {}, ensure_ascii=False)[:300] + chr(10)
        prompt += "历史经验: " + self._get_memory_summary() + chr(10) + chr(10)
        prompt += "请分析这个任务，输出JSON格式: {reasoning: 分析思路, plan: [步骤], commands: [browser_navigate URL 或 shell命令]}" + chr(10)
        prompt += "重要: 网页审计任务请用 browser_navigate/browser_get_title/browser_extract_text/browser_evaluate 等浏览器指令，不要只用curl。"

        try:
            resp = self.llm.chat.completions.create(
            reasoning_effort="high",
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000, temperature=0.3,
            )
            text = resp.choices[0].message.content.strip()
            if "{" in text:
                return json.loads(text[text.index("{"):text.rindex("}") + 1])
            return {"reasoning": text[:200], "plan": [task], "commands": []}
        except Exception as e:
            self.log(f"THINK 失败: {e}", "WARN")
            return {"reasoning": f"LLM错误: {e}", "plan": [task], "commands": []}

    def _call_browser_tool(self, tool_name, args):
        import subprocess as _sp
        init_req = json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "yaxiio", "version": "1.0"}}})
        call_req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": args}})
        try:
            proc = _sp.Popen(["python3", "/app/.pi/skills/commander/mcp_servers/browser_harness.py"], stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True)
            proc.stdin.write(init_req + chr(10))
            proc.stdin.readline()
            proc.stdin.write(call_req + chr(10))
            proc.stdin.flush()
            line = proc.stdout.readline()
            proc.terminate()
            if line:
                resp = json.loads(line)
                text = resp.get("result", {}).get("content", [{}])[0].get("text", "{}")
                return json.loads(text) if text else {"error": "empty"}
            return {"error": "no response"}
        except Exception as e:
            return {"error": str(e)}

    def execute(self, commands: list) -> dict:
        """🟢 EXECUTE: 执行命令序列（支持 shell 命令和浏览器工具指令）。"""
        results = []
        BROWSER_TOOLS = ["browser_navigate", "browser_click", "browser_type", "browser_screenshot",
                         "browser_extract_text", "browser_extract_links", "browser_extract_html",
                         "browser_evaluate", "browser_wait", "browser_scroll",
                         "browser_get_url", "browser_get_title", "browser_close"]
        for cmd in commands:
            if not cmd or cmd.startswith("#"):
                continue
            # 检查是否是浏览器工具指令
            is_browser = False
            for bt in BROWSER_TOOLS:
                if cmd.strip().startswith(bt):
                    is_browser = True
                    parts = cmd.strip().split(None, 1)
                    tool_name = parts[0]
                    arg_str = parts[1] if len(parts) > 1 else ""
                    # 简单解析参数: browser_navigate URL 或 browser_evaluate CODE
                    if tool_name == "browser_navigate":
                        args = {"url": arg_str.strip()}
                    elif tool_name == "browser_evaluate":
                        args = {"code": arg_str.strip()}
                    elif tool_name == "browser_extract_text":
                        args = {"selector": arg_str.strip()} if arg_str.strip() else {}
                    elif tool_name == "browser_get_title" or tool_name == "browser_get_url" or tool_name == "browser_close":
                        args = {}
                    else:
                        args = {"selector": arg_str.strip()} if arg_str.strip() else {}
                    self.log(f"🌐 浏览器工具: {tool_name}")
                    result = self._call_browser_tool(tool_name, args)
                    results.append({
                        "command": cmd[:200],
                        "success": "error" not in result,
                        "stdout": json.dumps(result, ensure_ascii=False)[:1000],
                        "exit_code": 0 if "error" not in result else 1,
                    })
                    break
            if is_browser:
                continue
            try:
                proc = subprocess.run(cmd, shell=True, capture_output=True,
                                      text=True, timeout=120)
                results.append({
                    "command": cmd[:200],
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout[:1000],
                    "stderr": proc.stderr[:300],
                    "success": proc.returncode == 0,
                })
            except subprocess.TimeoutExpired:
                results.append({"command": cmd[:200], "success": False, "error": "timeout"})
            except Exception as e:
                results.append({"command": cmd[:200], "success": False, "error": str(e)})

        success = all(r.get("success", False) for r in results)
        return {"success": success, "results": results, "total": len(results)}

    def evaluate(self, task: str, plan: dict, exec_result: dict) -> dict:
        """🔵 EVALUATE: LLM 评估执行结果，打分。"""
        score = 5
        feedback = ""

        if not self.llm:
            # 规则评估
            if exec_result.get("success"):
                score = 8
                feedback = "任务成功执行"
            else:
                score = 3
                errors = [r.get("error", "") for r in exec_result.get("results", [])]
                feedback = f"执行失败: {errors}"
        else:
            prompt = f"""评估此任务执行:
任务: {task[:200]}
计划: {json.dumps(plan, ensure_ascii=False)[:200]}
执行: {json.dumps(exec_result, ensure_ascii=False)[:300]}

输出 JSON: {{"score": 1-10, "feedback": "一句话评价", "improvement": "可改进点"}}"""
            try:
                resp = self.llm.chat.completions.create(
            reasoning_effort="high",
                    model=LLM_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200, temperature=0.2,
                )
                text = resp.choices[0].message.content.strip()
                if "{" in text:
                    eval_result = json.loads(text[text.index("{"):text.rindex("}") + 1])
                    score = eval_result.get("score", 5)
                    feedback = eval_result.get("feedback", "")
            except Exception:
                pass

        return {"score": score, "feedback": feedback, "needs_optimization": score < 6}

    def learn(self, task: str, plan: dict, exec_result: dict, evaluation: dict):
        """🟣 LEARN: 从执行中学习，存入记忆。"""
        if not self.redis:
            return

        memory = {
            "task": task[:300],
            "plan": str(plan)[:300],
            "success": exec_result.get("success", False),
            "score": evaluation.get("score", 0),
            "feedback": evaluation.get("feedback", ""),
            "timestamp": time.time(),
        }

        self.redis.lpush(MEMORY_KEY, json.dumps(memory, ensure_ascii=False))
        self.redis.ltrim(MEMORY_KEY, 0, 99)  # 保留最近 100 条

        # 如果评分高，优化角色提示词
        if evaluation.get("score", 0) >= 8 and evaluation.get("feedback"):
            improved = f"{self.role_prompt}\n\n[经验] {evaluation['feedback']}"
            if len(improved) < 2000:
                self.role_prompt = improved
                if self.redis:
                    self.redis.set(PROMPT_KEY, improved)

    def _get_memory_summary(self) -> str:
        """获取最近记忆摘要。"""
        if not self.redis:
            return "(无历史)"
        memories = self.redis.lrange(MEMORY_KEY, 0, 4)
        if not memories:
            return "(无历史)"
        lines = []
        for m in memories:
            try:
                d = json.loads(m)
                lines.append(f"- [{d.get('score',0)}分] {d.get('task','')[:80]}")
            except Exception:
                pass
        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════
    # 主处理循环
    # ═══════════════════════════════════════════════════════════

    def process_task(self, msg: dict) -> dict:
        """完整执行循环: THINK → PLAN → EXECUTE → EVALUATE → LEARN"""
        t0 = time.time()
        self.task_count += 1
        task_id = msg.get("taskId", "unknown")
        payload = msg.get("payload", {})
        task_text = payload.get("task", payload.get("description", str(payload)))

        self.log(f"📥 任务 {task_id}: {str(task_text)[:100]}")

        # 1. THINK: LLM 推理
        self.log("🟡 THINK...")
        plan = self.think(str(task_text), payload)

        # 2. PLAN → EXECUTE
        commands = plan.get("commands", [])
        if not commands and payload.get("command"):
            commands = [payload["command"]]

        self.log(f"🟢 EXECUTE: {len(commands)} commands")
        exec_result = self.execute(commands) if commands else {"success": True, "results": [], "total": 0}

        # 3. EVALUATE
        self.log("🔵 EVALUATE...")
        evaluation = self.evaluate(str(task_text), plan, exec_result)

        # 4. LEARN
        self.log(f"🟣 LEARN: score={evaluation['score']}")
        self.learn(str(task_text), plan, exec_result, evaluation)

        elapsed = int((time.time() - t0) * 1000)

        # 构建响应
        if exec_result.get("success"):
            response = {
                "from": AGENT_NAME,
                "to": msg.get("from", "commander"),
                "type": "response",
                "taskId": task_id,
                "payload": {
                    "status": "success",
                    "reasoning": plan.get("reasoning", ""),
                    "plan": plan.get("plan", []),
                    "results": exec_result.get("results", []),
                    "score": evaluation["score"],
                    "feedback": evaluation.get("feedback", ""),
                    "elapsed_ms": elapsed,
                },
            }
            self.log(f"✅ 完成 (score={evaluation['score']}, {elapsed}ms)")
        else:
            self.fail_count += 1
            response = {
                "from": AGENT_NAME,
                "to": msg.get("from", "commander"),
                "type": "error",
                "taskId": task_id,
                "payload": {
                    "status": "failed",
                    "reasoning": plan.get("reasoning", ""),
                    "results": exec_result.get("results", []),
                    "score": evaluation["score"],
                    "feedback": evaluation.get("feedback", ""),
                    "elapsed_ms": elapsed,
                },
            }
            self.log(f"❌ 失败 (score={evaluation['score']})", "ERROR")

        # 回复
        reply_to = msg.get("replyTo") or f"lightingmetal:agent:{msg.get('from', 'commander')}"
        if self.redis:
            self.redis.publish(reply_to, json.dumps(response, ensure_ascii=False))
            # 也发到全局结果频道供 Commander 收集
            self.redis.publish(RESULT_CHANNEL, json.dumps(response, ensure_ascii=False))

        return response

    # ═══════════════════════════════════════════════════════════
    # 运行
    # ═══════════════════════════════════════════════════════════

    def send_heartbeat(self):
        if self.redis:
            self.redis.publish(CONTROL_CHANNEL, json.dumps({
                "from": AGENT_NAME, "to": "commander", "type": "heartbeat",
                "payload": {
                    "status": "alive",
                    "tasks": self.task_count,
                    "fails": self.fail_count,
                    "uptime": int(time.time() - self.start_time),
                },
            }))

    def run(self):
        self.log(f"🧠 启动 (角色={AGENT_ROLE}, LLM={'on' if self.llm else 'off'})")
        self.send_heartbeat()

        if not self.redis:
            self.log("Redis 不可用，退出", "ERROR")
            return

        pubsub = self.redis.pubsub()
        pubsub.subscribe(CHANNEL, CONTROL_CHANNEL)
        last_heartbeat = time.time()

        for message in pubsub.listen():
            if not self.running:
                break
            if message["type"] != "message":
                continue

            channel = message["channel"]
            try:
                data = json.loads(message["data"])
            except Exception:
                continue

            msg_type = data.get("type", "")

            # 控制频道
            if channel == CONTROL_CHANNEL:
                if msg_type == "shutdown" and data.get("to") in (AGENT_NAME, "*"):
                    self.log("收到关闭指令")
                    self.running = False
                    break
                elif msg_type == "upgrade_prompt":
                    new = data.get("payload", {}).get("prompt", "")
                    if new:
                        self.role_prompt = new
                        if self.redis:
                            self.redis.set(PROMPT_KEY, new)
                        self.log(f"提示词已升级")
                continue

            # 任务频道
            if data.get("to") not in (AGENT_NAME, "*", "all"):
                continue

            if msg_type in ("request", "task"):
                self.process_task(data)
            elif msg_type == "heartbeat_check":
                self.send_heartbeat()

            # 心跳
            if time.time() - last_heartbeat > 30:
                self.send_heartbeat()
                last_heartbeat = time.time()

        self.log(f"🛑 关闭 (任务={self.task_count}, 失败={self.fail_count})")
        pubsub.close()


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    agent = YaxiioAgent()
    try:
        agent.run()
    except KeyboardInterrupt:
        agent.running = False
    except Exception as e:
        print(f"[{AGENT_NAME}] FATAL: {e}")
        traceback.print_exc()
