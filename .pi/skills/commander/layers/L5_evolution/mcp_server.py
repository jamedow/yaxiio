"""
L5 Evolution Server v2.0 — LLM 驱动的评估与进化
================================================
升级:
  - deep_score: LLM 深度质量评估 (质量门神)
  - generate_agent: 自主创建新 Agent (智能体工厂)
  - meta_reflect: 任务后反思与模式发现 (元认知)
  - 保留原有 score_task/generate_skill 作为无 LLM 兜底
"""

import sys, os, json, time, uuid
sys.path.insert(0, "/opt/commander")

from mcp.protocol import MCPServer, run_mcp_server
from config import L5_EVOLUTION_PORT, SCORE_THRESHOLD, SKILL_DIR

# LLM 客户端
def _get_llm():
    try:
        from openai import OpenAI
        import redis as _r
        r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
        key = r.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            return None
        return OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
    except:
        return None

def _llm_chat(prompt: str, max_tokens: int = 500, thinking: str = "medium") -> str:
    llm = _get_llm()
    if not llm:
        return ""
    resp = llm.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3, max_tokens=max_tokens,
        # extra_body removed - DeepSeek does not support reasoning_effort
    )
    return resp.choices[0].message.content


class EvolutionServer(MCPServer):
    def __init__(self):
        super().__init__("L5_evolution", "Evolution Layer v2.0 — LLM Scoring, Agent Factory, Meta-Cognition")
        self._scores = []
        self._topologies = {}
        self._reflections = []  # 元认知日志

        # ── 原有工具 (保留兼容) ──
        self.register_tool("score_task", self.score_task)
        self.register_tool("generate_skill", self.generate_skill)
        self.register_tool("record_workflow", self.record_workflow)
        self.register_tool("evaluate_topologies", self.evaluate_topologies)
        self.register_tool("optimize_prompt", self.optimize_prompt)
        self.register_tool("audit_log", self.audit_log)
        self.register_tool("generate_design_enforcement", self.generate_design_enforcement)
        self.register_tool("get_modification_stream", self.get_modification_stream)

        # ── 新增: LLM 驱动工具 ──
        self.register_tool("deep_score", self.deep_score)
        self.register_tool("generate_agent", self.generate_agent)
        self.register_tool("meta_reflect", self.meta_reflect)
        self.register_tool("generate_tool", self.generate_tool)
        self.register_tool("web_research", self.web_research)
        self.register_tool("research_and_retry", self.research_and_retry)

    # ═══════════════════════════════════════════════
    # 🆕 质量门神: LLM 深度内容评估
    # ═══════════════════════════════════════════════

    def deep_score(self, task_id: str = "", action: str = "",
                   agent_name: str = "", output: str = "",
                   context: str = "", source_material: str = "") -> dict:
        """
        LLM 驱动的深度质量评估。评估维度: 准确性/完整性/专业性/可操作性/一致性。
        如果 LLM 不可用，降级为规则评分。
        """
        if not output or len(output) < 20:
            return {"overall": 1, "method": "rule", "reason": "output too short"}

        prompt = f"""你是 ExampleCorp 的内容质量总编。评估以下 Agent 产出:

任务: {action}
Agent: {agent_name}
上下文: {context[:300]}
参考材料: {source_material[:300]}

=== Agent 产出 ===
{output[:2000]}

=== 评估维度 (每项1-10分) ===
1. 准确性 — 事实数据是否正确？术语是否恰当？
2. 完整性 — 是否覆盖了任务要求的所有方面？
3. 专业性 — 表述是否达到行业专家水准？
4. 可操作性 — 产出是否可以直接使用？
5. 一致性 — 是否与品牌调性一致？

输出 JSON:
{{"accuracy":X,"completeness":X,"professionalism":X,"actionability":X,"consistency":X,
 "overall":X,"key_issues":["问题1","问题2"],"suggestions":["建议1","建议2"],
 "verdict":"pass|retry|reject","verdict_reason":"理由"}}"""

        try:
            print(f"[L5 DEBUG] calling LLM, output_len={len(output)}, prompt_len={len(prompt)}", flush=True)
            resp = _llm_chat(prompt, max_tokens=400, thinking="off")
            # 提取 JSON
            if "```" in resp:
                resp = resp.split("```")[1]
                if resp.startswith("json"): resp = resp[4:]
            result = json.loads(resp.strip())
            result["method"] = "llm"
            result["task_id"] = task_id
            return result
        except Exception as e:
            # 降级: 规则评分
            return self.score_task(task=task_id, result={"stdout": output},
                                   agent_id=agent_name, elapsed_ms=0)

    # ═══════════════════════════════════════════════
    # 🆕 智能体工厂: 自主创建新 Agent
    # ═══════════════════════════════════════════════

    def generate_agent(self, requirement: str = "", gap_analysis: str = "",
                       existing_agents: str = "") -> dict:
        """
        LLM 驱动的 Agent 创建。分析需求缺口，生成新 Agent 的 SKILL.md。
        """
        if not requirement:
            return {"status": "error", "reason": "requirement is required"}

        prompt = f"""你是 Yaxiio 的智能体工厂。需要创建一个新的 Agent。

需求: {requirement}
能力缺口: {gap_analysis[:300]}
已有 Agent: {existing_agents[:200]}

先基于需求进行行业研究，再设计新 Agent，输出 JSON:
{{
  "agent_name": "中文名称",
  "agent_role": "角色简述",
  "model_config": {{"model":"deepseek-chat","thinking":"medium"}},
  "skill_content": "完整的 SKILL.md 内容，包含: ## 身份, ## 能力, ## 输出格式, ## 约束",
  "reason": "为什么需要这个Agent",
  "expected_improvement": "预期改进"
}}"""

        try:
            # 先做行业研究
            research = self.web_research(topic=requirement[:80], context=gap_analysis[:200], depth="standard")
            if research.get("status") == "success":
                prompt = prompt.replace("{research_findings}", research.get("findings", "")[:400])
            resp = _llm_chat(prompt, max_tokens=800, thinking="high")
            if "```" in resp:
                resp = resp.split("```")[1]
                if resp.startswith("json"): resp = resp[4:]
            design = json.loads(resp.strip())

            # 写入 SKILL.md
            os.makedirs(SKILL_DIR, exist_ok=True)
            agent_dir = os.path.join(SKILL_DIR, design["agent_name"])
            os.makedirs(agent_dir, exist_ok=True)

            skill_path = os.path.join(agent_dir, "SKILL.md")
            with open(skill_path, "w") as f:
                f.write(design.get("skill_content", ""))

            # 写报告
            rp = f"/app/.pi/blackboard/reports/factory-{time.strftime('%Y%m%d-%H%M%S')}.md"
            os.makedirs(os.path.dirname(rp), exist_ok=True)
            with open(rp, "w") as f:
                f.write(f"# Agent 创建报告\n\n"
                        f"Agent: {design['agent_name']}\n"
                        f"需求: {requirement}\n"
                        f"理由: {design.get('reason','')}\n"
                        f"Skill: {skill_path}\n")

            return {
                "status": "created",
                "agent_name": design["agent_name"],
                "skill_path": skill_path,
                "model_config": design.get("model_config", {}),
                "report": rp,
            }
        except Exception as e:
            return {"status": "error", "reason": str(e)[:200]}

    # ═══════════════════════════════════════════════
    # 🆕 元认知: 任务后反思与模式发现
    # ═══════════════════════════════════════════════

    def meta_reflect(self, task_id: str = "", action: str = "",
                     agent_name: str = "", score: float = 0,
                     issues: str = "", timeline: str = "") -> dict:
        """
        任务后反思。分析失败模式，发现规律，给出系统级改进建议。
        """
        reflection = {
            "task_id": task_id,
            "ts": time.time(),
            "score": score,
            "agent": agent_name,
            "action": action,
        }

        # 规则分析
        if score < 5:
            reflection["severity"] = "critical"
            reflection["rule_hint"] = f"{agent_name} 在 {action} 上严重低分，建议检查 Skill 或切换模型"
        elif score < 7:
            reflection["severity"] = "warning"
            reflection["rule_hint"] = f"{agent_name} 有改进空间"
        else:
            reflection["severity"] = "ok"
            reflection["rule_hint"] = "正常"

        # Auto-detect missing tool patterns and generate tools
        if score < 5 and issues:
            issues_str = str(issues).lower()
            if any(kw in issues_str for kw in ["translat", "翻译", "mixed", "混杂", "chinese", "中文"]):
                try:
                    gen = self.generate_tool(
                        requirement="translate Chinese text in MongoDB fields to target language via LLM",
                        pattern="repeated_translation_failure_" + action,
                        task_type="content_fix")
                    if gen.get("status") == "success":
                        reflection["tool_generated"] = gen.get("tool")
                        print("[L5] auto-generated tool: " + str(gen.get("tool","?"))[:50], flush=True)
                except Exception as e:
                    reflection["tool_gen_error"] = str(e)[:100]

        # LLM deep reflection
        if issues and score < 7:
            prompt = f"""分析这次任务执行的失败模式:

任务: {action}
Agent: {agent_name}
评分: {score}/10
问题: {issues[:500]}
时间线: {timeline[:300]}

请用 100 字中文输出:
1. 根因是什么
2. 系统级改进建议（不是针对这一次，是针对以后所有类似任务）"""

            try:
                hint = _llm_chat(prompt, max_tokens=200, thinking="medium")
                reflection["llm_insight"] = hint.strip()
            except:
                reflection["llm_insight"] = "(LLM unavailable)"

        self._reflections.append(reflection)
        if len(self._reflections) > 200:
            self._reflections = self._reflections[-100:]

        # 发现模式
        patterns = self._detect_patterns()
        reflection["patterns_detected"] = patterns

        return reflection

    def _detect_patterns(self) -> list:
        """从反思日志中发现 recurring patterns"""
        if len(self._reflections) < 5:
            return []

        patterns = []
        # Agent 失败频率
        agent_fails = {}
        for r in self._reflections[-30:]:
            if r.get("severity") in ("critical", "warning"):
                agent = r.get("agent", "?")
                agent_fails[agent] = agent_fails.get(agent, 0) + 1

        for agent, count in agent_fails.items():
            if count >= 3:
                patterns.append({
                    "type": "agent_degradation",
                    "agent": agent,
                    "recent_failures": count,
                    "suggestion": f"建议检查 {agent} 的 Skill 或考虑模型升级",
                })

        return patterns

    # ═══════════════════════════════════════════════
    # 🆕 网络研究: 利用 LLM 知识库搜索行业资料
    # ═══════════════════════════════════════════════

    def generate_tool(self, requirement: str = "", pattern: str = "",
                      task_type: str = "", language: str = "python") -> dict:
        """L5: Auto-generate tool scripts from repeated task patterns"""
        if not requirement:
            return {"status": "error", "reason": "requirement is required"}
        
        prompt = f"""Generate a Python script that {requirement}.
Pattern detected: {pattern}
Task type: {task_type}

Requirements:
- Single file, runnable via python3 script.py
- Connect to MongoDB at os.environ.get("MONGO_URI", "mongodb://localhost:27017")
- Use OpenAI(api_key=os.environ.get("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com/v1")
- Print JSON result with status and count
- Handle errors gracefully
- Add #!/usr/bin/env python3 header

Output ONLY the Python code, no explanation."""
        
        try:
            code = _llm_chat(prompt, max_tokens=1000, thinking="high")
            if "```" in code:
                code = code.split("```")[1]
                if code.startswith("python"): code = code[6:]
            
            # Save the generated tool
            tool_name = f"gen_{task_type}_{int(time.time())}"
            tool_path = f"/opt/commander/tools/{tool_name}.py"
            os.makedirs(os.path.dirname(tool_path), exist_ok=True)
            with open(tool_path, "w") as f:
                f.write(code.strip())
            os.chmod(tool_path, 0o755)
            
            # Register in Redis
            import redis as _r
            r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
            r.hset("tools:registry", tool_name, json.dumps({
                "name": tool_name, "desc": requirement[:80],
                "usage": f"python3 /opt/commander/tools/{tool_name}.py",
                "category": "generated", "generated_at": time.time()
            }))
            
            return {"status": "success", "tool": tool_name, "path": tool_path,
                    "lines": len(code.split(chr(10)))}
        except Exception as e:
            return {"status": "error", "reason": str(e)[:200]}

    def generate_tool(self, requirement: str = "", pattern: str = "",
                      task_type: str = "") -> dict:
        """L5: Auto-generate tool scripts from repeated task patterns"""
        if not requirement:
            return {"status": "error", "reason": "requirement is required"}
        prompt = "Generate a Python script that " + requirement + ". Pattern: " + str(pattern)[:200] + ". Task: " + task_type + ". Requirements: single file, MongoDB, OpenAI, print JSON, handle errors. Output ONLY Python code, no explanation."
        try:
            code = _llm_chat(prompt, max_tokens=1000, thinking="high")
            if "```" in code:
                code = code.split("```")[1]
                if code.startswith("python"): code = code[6:]
            tool_name = "gen_" + task_type + "_" + str(int(time.time()))
            tool_path = "/opt/commander/tools/" + tool_name + ".py"
            os.makedirs(os.path.dirname(tool_path), exist_ok=True)
            with open(tool_path, "w") as f:
                f.write(code.strip())
            os.chmod(tool_path, 0o755)
            import redis as _r
            r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=os.environ.get("REDIS_PASSWORD", ""), decode_responses=True)
            r.hset("tools:registry", tool_name, json.dumps({
                "name": tool_name, "desc": requirement[:80],
                "usage": "python3 " + tool_path,
                "category": "generated", "generated_at": time.time()
            }))
            return {"status": "success", "tool": tool_name, "path": tool_path,
                    "lines": len(code.split(chr(10)))}
        except Exception as e:
            return {"status": "error", "reason": str(e)[:200]}

    def web_research(self, topic: str = "", context: str = "",
                     depth: str = "quick") -> dict:
        """利用 LLM 知识库 + 浏览器进行网络研究"""
        if not topic:
            return {"status": "error", "reason": "topic required"}

        # 1. 先用 LLM 知识库 (快速)
        quick_prompt = f"你是行业专家。关于「{topic}」: 1)关键概念 2)最新标准(2024-2026) 3)最佳实践 4)主流厂商。中文300字。背景: {context[:200]}"
        try:
            llm_result = _llm_chat(quick_prompt, max_tokens=600, thinking="medium")
        except:
            llm_result = ""

        # 2. 尝试用浏览器搜索 (深度模式)
        browser_result = ""
        if depth == "deep":
            try:
                sys.path.insert(0, "/opt/commander/mcp")
                from browser_agent import BrowserAgent
                agent = BrowserAgent(headless=True)
                search_task = f"Search for latest information about {topic}. Find key specifications, standards, and trends. Extract the most important findings."
                br = agent.execute(task=search_task, max_steps=8)
                if br.get("status") == "success":
                    browser_result = br.get("output", "")
                elif br.get("steps"):
                    for step in br["steps"]:
                        r = step.get("result", "")
                        if "extracted" in str(r):
                            browser_result += str(r)[:500]
            except Exception as e:
                browser_result = f"[browser unavailable: {e}]"

        findings = llm_result
        if browser_result:
            findings += f"\n\n=== 浏览器实地调研 ===\n{browser_result[:600]}"

        return {"status": "success", "topic": topic, "depth": depth,
                "findings": findings,
                "sources": ["LLM_knowledge"] + (["browser_search"] if browser_result else []),
                "disclaimer": "建议人工核实关键数据"}

    def research_and_retry(self, task_id: str = "", action: str = "",
                           agent_name: str = "", original_output: str = "",
                           issues: str = "", topic: str = "") -> dict:
        """研究+重试: 网上找资料 → 生成增强参考材料 → 供 Agent 重试用"""
        research = self.web_research(topic=topic or action,
                                     context=f"原产出问题: {issues[:200]}", depth="standard")
        if research.get("status") != "success":
            return {"status": "error", "reason": "research failed", "retry": False}
        findings = research.get("findings", "")
        enhanced = f"基于行业研究重新完成。研究资料:\n{findings[:600]}\n原任务:{action}\n原问题:{issues[:300]}"
        return {"status": "success", "retry": True, "research_findings": findings[:800],
                "enhanced_prompt": enhanced[:1000], "suggested_thinking": "high"}

    # ═══════════════════════════════════════════════
    # 原有工具 (保持不变)
    # ═══════════════════════════════════════════════

    def score_task(self, task: str = "", result: dict = None,
                   agent_id: str = "", elapsed_ms: int = 0) -> dict:
        result = result or {}
        has_content = bool(result.get("stdout") or result.get("result"))
        has_error = bool(result.get("error") or result.get("stderr"))
        scores = {
            "completeness": 8 if has_content else 3,
            "quality": 3 if has_error else 7,
            "efficiency": 9 if elapsed_ms < 5000 else (7 if elapsed_ms < 30000 else 4),
            "relevance": 6,
        }
        overall = round(0.35 * scores["completeness"] + 0.30 * scores["quality"] +
                        0.20 * scores["efficiency"] + 0.15 * scores["relevance"])
        self._scores.append({"agent_id": agent_id, "overall": overall, "timestamp": time.time()})
        return {"overall": overall, "dimensions": scores, "method": "rule",
                "needs_review": overall < SCORE_THRESHOLD}

    def generate_skill(self, agent_id: str = "", task: str = "",
                        result: dict = None, score: float = 0) -> dict:
        os.makedirs(SKILL_DIR, exist_ok=True)
        name = f"{agent_id}-{uuid.uuid4().hex[:6]}"
        content = f"---\nname: {name}\nagent: {agent_id}\nscore: {score}\n---\n# {name}\n## Trigger\n{task[:200]}\n"
        content = content[:2200]
        path = os.path.join(SKILL_DIR, f"{name}.md")
        with open(path, "w") as f: f.write(content)
        return {"status": "generated", "path": path, "name": name}

    def record_workflow(self, task_type: str = "general", topology: dict = None,
                         metrics: dict = None) -> dict:
        topology = topology or {}; metrics = metrics or {}
        sid = f"ws-{uuid.uuid4().hex[:8]}"
        self._topologies.setdefault(task_type, []).append({
            "snapshot_id": sid, "topology": topology, "metrics": metrics, "timestamp": time.time()
        })
        return {"snapshot_id": sid}

    def evaluate_topologies(self, task_type: str = "general") -> dict:
        records = self._topologies.get(task_type, [])
        if not records: return {"topologies": [], "recommendation": None}
        grouped = {}
        for r in records:
            sig = json.dumps(r["topology"], sort_keys=True)
            grouped.setdefault(sig, []).append(r)
        scored = []
        for sig, group in grouped.items():
            successes = sum(1 for r in group if r["metrics"].get("success"))
            avg_score = sum(r["metrics"].get("score", 5) for r in group) / len(group)
            scored.append({"signature": sig[:40], "count": len(group), "success_rate": round(successes/len(group),2)})
        scored.sort(key=lambda x: x["success_rate"], reverse=True)
        return {"topologies": scored[:3], "recommendation": scored[0] if scored else None}

    def optimize_prompt(self, prompt: str = "", feedback: str = "") -> dict:
        improved = prompt
        if "error" in feedback.lower(): improved += "\nHandle errors gracefully."
        if "slow" in feedback.lower(): improved += "\nBe concise."
        return {"prompt": improved, "improvement_score": 0.6 if improved != prompt else 0.5, "method": "textgrad-rule"}

    def audit_log(self, level: str = "INFO", event_type: str = "unknown", detail: dict = None) -> dict:
        return {"logged": True, "entry": {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "level": level, "event_type": event_type, "detail": detail or {}}}

    def generate_design_enforcement(self, industry: str = "", level: str = "") -> dict:
        from modules.design_enforcer import generate_modifications, generate_full_plan
        if industry and level: return {"instructions": generate_modifications(industry, level)}
        return generate_full_plan()

    def get_modification_stream(self, industry: str = "", level: str = "", start_index: int = 0, batch_size: int = 5) -> dict:
        from modules.design_enforcer import generate_modifications, generate_full_plan
        if industry and level: all_inst = generate_modifications(industry, level)
        else:
            plan = generate_full_plan(); all_inst = []
            for ind in plan["industries"]:
                for lvl in plan["industries"][ind]["levels"]:
                    all_inst.extend(plan["industries"][ind]["levels"][lvl])
        batch = all_inst[start_index:start_index + batch_size]
        nx = start_index + batch_size if start_index + batch_size < len(all_inst) else None
        return {"batch": batch, "next_index": nx, "total": len(all_inst)}


if __name__ == "__main__":
    run_mcp_server("L5_evolution", EvolutionServer(), L5_EVOLUTION_PORT)
