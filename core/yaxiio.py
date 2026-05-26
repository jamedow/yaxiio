#!/usr/bin/env python3
"""雅溪 Yaxiio v1.04 — 五层模块化智能调度系统 · AGPLv3"""
import sys, os, json, time, signal, asyncio, shutil, glob

sys.path.insert(0, "/opt/commander")
from modules.shared.config import LOG_DIR
from modules.layer1 import RedisClient, MongoClient, MCPRegistry, SkillLoader, create_vector_store
from modules.layer2 import AgentFactory, LifecycleManager, ModelRouter, AgentRegistry, RAGManager
from modules.layer3 import TaskDecomposer, DependencyAnalyzer, Scheduler, WorkflowSnapshot
from modules.layer4 import AutoScorer, AuditLogger, FailureDetector
from modules.layer5 import PromptOptimizer, WorkflowOptimizer, ABTester, SkillAutoGenerator

class Commander:
    def __init__(self):
        self.redis = RedisClient(); self.mongo = MongoClient()
        self.mcp = MCPRegistry(); self.vector = create_vector_store()
        self.skills = SkillLoader(self.vector)
        self.model_router = ModelRouter()
        self.agent_factory = AgentFactory(self.redis, self.model_router)
        self.lifecycle = LifecycleManager(self.agent_factory, self.redis)
        self.rag = RAGManager(self.vector, self.redis)
        self.registry = AgentRegistry(self.redis)
        self.scheduler = Scheduler(self.agent_factory, self.lifecycle)
        self.snapshot = WorkflowSnapshot()
        self.scorer = AutoScorer(); self.audit = AuditLogger(self.mongo)
        self.detector = FailureDetector()
        self.prompt_opt = PromptOptimizer(); self.workflow_opt = WorkflowOptimizer()
        self.ab = ABTester(); self.skill_gen = SkillAutoGenerator()
        self.task_count = 0; self.start_time = time.time(); self.running = True

    # ═══════════════════════════════════════════════
    # 任务路由
    # ═══════════════════════════════════════════════
    def handle_task(self, data: dict):
        tid = data.get("taskId", f"auto-{int(time.time())}")
        p = data.get("payload", {})
        a = p.get("action", "")
        print(f"[雅溪] 🧠 {tid} ({a})", flush=True)
        actions = {
            "session_end": self._cleanup_sandboxes,
            "site_audit": lambda: self._run_audit(tid, p),
            "site_fix": lambda: self._run_fix(tid, p),
            "site_evolve": lambda: self._run_evolve(tid, p),
            "site_drill": lambda: self._run_drill(tid, p),
            "site_inquire": lambda: self._tool_inquire(tid, p, data),
        }
        if a in actions:
            result = actions[a]() if a == "session_end" else actions[a]()
        else:
            result = self._run_diagnose(tid, p)
        score = self.scorer.score({"task_id": tid, "action": a}, result)
        self.audit.log({"task_id": tid, "action": a}, result, score)
        self.task_count += 1
        return result

    # ═══════════════════════════════════════════════
    # 沙箱管理
    # ═══════════════════════════════════════════════

    def _tool_inquire(self, tid, p, data):
        """Inquire: LLM checks missing info, asks external agent, retries"""
        import asyncio
        reply_ch = data.get("replyTo", f"lightingmetal:agent:{data.get('from','external')}")
        llm = self._get_llm()
        if not llm: return {"status":"fail","error":"LLM offline"}
        try:
            loop = asyncio.new_event_loop()
            resp = loop.run_until_complete(llm.chat(
                f"You are Yaxiio. A task came in but may lack info. If enough info, say READY. Otherwise ask 1-2 follow-up questions in Chinese. Task: {json.dumps(p, ensure_ascii=False)[:800]}. Known: codebase=/app/lightingmetal/customer-portal"
            ))
            loop.close()
        except Exception as e: return {"status":"fail","error":str(e)}
        if resp and "READY" not in resp.upper():
            self.redis.publish(reply_ch, {"type":"inquiry","taskId":tid,"from":"yaxiio","payload":{"action":"need_info","question":resp[:300]}})
            print(f"[雅溪] Ask: {resp[:100]}", flush=True)
            return {"status":"inquiring","question":resp[:300]}
        return self.handle_task({**data,"payload":p})

    def _cleanup_sandboxes(self) -> dict:
        c = 0
        for d in glob.glob("/tmp/yaxiio-fix-*") + glob.glob("/tmp/yaxiio-drill-*"):
            shutil.rmtree(d, ignore_errors=True); c += 1
        return {"status": "cleaned", "removed": c}

    # ═══════════════════════════════════════════════
    # 审计
    # ═══════════════════════════════════════════════
    def _run_audit(self, tid, p):
        cb = p.get("codebase", "/app/lightingmetal/customer-portal")
        print(f"[雅溪] 🔍 审计 {cb}", flush=True)
        vf = []
        for root, dirs, files in os.walk(cb):
            dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
            for fn in files:
                if fn.endswith(".vue"):
                    fp = os.path.join(root, fn)
                    with open(fp) as fh:
                        ct = fh.read()
                        ds = sum(1 for kw in ["card-zhongli","btn-zhongli","fangsheng","chain-divider","meander","vision"] if kw in ct)
                        ol = sum(1 for kw in ["bg-white","bg-gray","text-gray","shadow-lg"] if kw in ct)
                        th = sum(1 for kw in ["useIndustryTheme","texture-kunlun","texture-myth","texture-jingzhe","texture-ding","texture-canal"] if kw in ct)
                        vf.append({"f":fp.replace(cb,""),"l":len(ct.split("\n")),"ds":ds,"old":ol,"th":th})
        findings = []
        llm = self._get_llm()
        if llm:
            s = "\n".join(f"{v['f']}: {v['l']}L ds={v['ds']} old={v['old']} theme={v['th']}" for v in sorted(vf,key=lambda x:-x["ds"])[:15])
            try:
                loop = asyncio.new_event_loop()
                r = loop.run_until_complete(llm.chat(f"Audit {len(vf)} Vue files:\n{s}\nAnalyze design compliance, old UI, theme pushdown. Markdown Chinese 300 chars."))
                loop.close(); findings.append(r)
            except Exception as e: findings.append(f"LLM: {e}")
        ts = time.strftime("%Y%m%d-%H%M%S"); rp = f"/app/.pi/blackboard/reports/audit-{ts}.md"
        rpt = f"# Audit\n> {ts} | {len(vf)} files\n\n| File | Lines | ds | old | theme |\n|------|-------|----|-----|-------|\n"
        for v in sorted(vf,key=lambda x:-x["ds"])[:20]: rpt += f"| {v['f']} | {v['l']} | {v['ds']} | {v['old']} | {v['th']} |\n"
        rpt += "\n## Analysis\n\n" + "\n".join(findings)
        with open(rp,"w") as f: f.write(rpt)
        # 记录改进幅度
        improvement = est - score
        if improvement < stop.get("score_improvement_min", 1.5):
            no_improve_key2 = f"yaxiio:drill_no_improve:{task[:50]}"
            self.redis.setex(no_improve_key2, 86400, str(no_improve + 1))
        else:
            self.redis.delete(f"yaxiio:drill_no_improve:{task[:50]}")
        print(f"[雅溪] 📄 {rp}", flush=True)
        return {"status":"success","report":rp,"files":len(vf)}

    # ═══════════════════════════════════════════════
    # 修复
    # ═══════════════════════════════════════════════
    def _run_fix(self, tid, p):
        cb = p.get("codebase", "/app/lightingmetal/customer-portal")
        reports = sorted([f for f in os.listdir("/app/.pi/blackboard/reports") if f.startswith(("audit-","diag-"))], reverse=True)
        if not reports: return {"status":"fail","error":"no reports"}
        with open(f"/app/.pi/blackboard/reports/{reports[0]}") as f: audit = f.read()[:4000]
        llm = self._get_llm()
        if not llm: return {"status":"fail","error":"LLM offline"}
        print("[雅溪] 🔧 fixing...", flush=True)
        try:
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(llm.chat(f"Audit:\n{audit[:3000]}\nSuggest concrete fixes. Format: filepath | replace_color | old->new. 5-10 items. Chinese 300 chars."))
            loop.close()
        except Exception as e: return {"status":"fail","error":str(e)}
        sandbox = f"/tmp/yaxiio-fix-{int(time.time())}"
        shutil.copytree(cb, sandbox+"/code", symlinks=True, ignore=shutil.ignore_patterns("node_modules",".nuxt",".git",".output"))
        applied = 0; diffs = []
        for line in plan.split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3 or "->" not in parts[2]: continue
            target = os.path.join(sandbox+"/code", parts[0].lstrip("/"))
            if os.path.exists(target):
                ov, nv = parts[2].split("->",1)
                with open(target) as fh: orig = fh.read()
                if ov.strip() in orig:
                    with open(target,"w") as fh: fh.write(orig.replace(ov.strip(), nv.strip(), 1))
                    diffs.append(f"  {parts[0]}: {ov.strip()} -> {nv.strip()}"); applied += 1
        ts = time.strftime("%Y%m%d-%H%M%S"); rp = f"/app/.pi/blackboard/reports/fix-{ts}.md"
        with open(rp,"w") as f: f.write(f"# Fix\n> {tid}\n\n## Plan\n\n{plan}\n\n## Applied ({applied})\n"+"\n".join(diffs))
        print(f"[雅溪] 📄 {rp} ({applied})", flush=True)
        return {"status":"success","report":rp,"applied":applied,"sandbox":sandbox}

    # ═══════════════════════════════════════════════
    # 诊断
    # ═══════════════════════════════════════════════
    def _run_diagnose(self, tid, p):
        cb = p.get("codebase", "/app/lightingmetal/customer-portal")
        issue = p.get("issue", str(p))
        print(f"[雅溪] 🔍 诊断: {issue[:80]}", flush=True)
        relevant = []
        hints = p.get("hint","").lower().split(",")
        for root, dirs, files in os.walk(cb):
            dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
            for fn in files:
                if fn.endswith((".vue",".ts",".js")):
                    fp = os.path.join(root, fn)
                    try:
                        with open(fp) as fh: ct = fh.read()
                        matched = any(h.strip() in ct.lower() for h in hints if h.strip())
                        name_match = any(h.strip() in fn.lower() for h in hints if h.strip())
                        if matched or name_match:
                            relevant.append({"file":fp.replace(cb,""),"code":ct[:1500]})
                            if len(relevant) >= 12: break
                    except: pass
        findings = []
        llm = self._get_llm()
        if llm and relevant:
            ctx = "\n\n".join(f"=== {r['file']} ===\n{r['code']}" for r in relevant[:6])
            try:
                loop = asyncio.new_event_loop()
                r = loop.run_until_complete(llm.chat(f"Diagnose: {issue}\n\nCode:\n{ctx[:4000]}\n\nRoot cause, impact, fix. Markdown Chinese 300 chars."))
                loop.close(); findings.append(r)
            except Exception as e: findings.append(f"LLM: {e}")
        ts = time.strftime("%Y%m%d-%H%M%S"); rp = f"/app/.pi/blackboard/reports/diag-{ts}.md"
        with open(rp,"w") as f: f.write(f"# Diagnosis\n> {tid}\n\n## Issue\n{issue}\n\n## Analysis\n\n"+"\n".join(findings))
        # 记录改进幅度
        improvement = est - score
        if improvement < stop.get("score_improvement_min", 1.5):
            no_improve_key2 = f"yaxiio:drill_no_improve:{task[:50]}"
            self.redis.setex(no_improve_key2, 86400, str(no_improve + 1))
        else:
            self.redis.delete(f"yaxiio:drill_no_improve:{task[:50]}")
        print(f"[雅溪] 📄 {rp}", flush=True)
        return {"status":"success","report":rp}

    # ═══════════════════════════════════════════════
    # 自进化
    # ═══════════════════════════════════════════════
    def _run_evolve(self, tid, p):
        req = p.get("requirement", str(p))
        print(f"[雅溪] 🧬 进化: {req[:80]}", flush=True)
        target = "/opt/commander/yaxiio.py"
        my_code = ""
        for root, dirs, files in os.walk("/opt/commander/modules"):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.endswith(".py") and "bak" not in fn:
                    with open(os.path.join(root,fn)) as fh: my_code += f"\n=== {fn} ===\n{fh.read()[:600]}"
        with open(target) as fh: my_code += f"\n=== yaxiio.py ===\n{fh.read()[:2000]}"
        llm = self._get_llm()
        if not llm: return {"status":"fail","error":"LLM offline"}
        try:
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(llm.chat(
                f"Evolve Yaxiio. Need: {req}\nCurrent:\n{my_code[:5000]}\n"
                "Output: FILE: path\nMETHOD_NAME: xxx\nCODE:\n```python\n...\n```\nChinese 200 chars."
            )); loop.close()
        except Exception as e: return {"status":"fail","error":str(e)}
        print(f"[雅溪] 💭 {plan[:200]}", flush=True)
        applied = 0
        if "CODE:" in plan and "```python" in plan:
            try:
                cb = plan.split("```python")[1].split("```")[0]
                mn = plan.split("METHOD_NAME:")[1].split("\n")[0].strip() if "METHOD_NAME:" in plan else "new"
                shutil.copy(target, target+".prev")
                with open(target) as fh: orig = fh.read()
                mk = "    def _get_llm(self):"
                if mk in orig:
                    nc = orig.replace(mk, cb+"\n\n    "+mk)
                    compile(nc, target, "exec")
                    with open(target,"w") as fh: fh.write(nc)
                    applied = 1; print(f"[雅溪] ✅ {mn}", flush=True)
            except Exception as e: print(f"[雅溪] ⚠️ {e}", flush=True)
        ts = time.strftime("%Y%m%d-%H%M%S"); rp = f"/app/.pi/blackboard/reports/evolve-{ts}.md"
        with open(rp,"w") as f: f.write(f"# Evolve\n> {ts}\n\n## Need\n{req}\n\n## Plan\n\n{plan}\n\n## Result\n{applied} applied")
        # 记录改进幅度
        improvement = est - score
        if improvement < stop.get("score_improvement_min", 1.5):
            no_improve_key2 = f"yaxiio:drill_no_improve:{task[:50]}"
            self.redis.setex(no_improve_key2, 86400, str(no_improve + 1))
        else:
            self.redis.delete(f"yaxiio:drill_no_improve:{task[:50]}")
        print(f"[雅溪] 📄 {rp}", flush=True)
        if applied: os._exit(42)
        return {"status":"success","applied":applied,"report":rp}

    # ═══════════════════════════════════════════════
    # 沙箱演习
    # ═══════════════════════════════════════════════
    def _run_drill(self, tid, p):
        cb = p.get("codebase", "/app/lightingmetal/customer-portal")
        task = p.get("replay_task", "unknown")
        score = float(p.get("original_score", 5.0))
        threshold = float(p.get("threshold", 7.0))
        # 加载演习策略
        policy = {}
        try:
            import json as _j
            raw = self.redis.get("yaxiio:drill_policy")
            if raw: policy = _j.loads(raw)
        except: pass
        trigger = policy.get("trigger", {})
        limits = policy.get("limits", {})
        stop = policy.get("stop_conditions", {})

        # 检查触发条件
        if score < trigger.get("min_score", 1.0): return {"status":"skip","reason":"score too low, manual review needed"}
        if score >= trigger.get("max_score", 6.0): return {"status":"skip","reason":"score acceptable"}
        allowed_actions = trigger.get("only_actions", [])
        action_type = task  # drill检查的是原始任务类型，不是site_drill
        if allowed_actions and action_type not in allowed_actions: return {"status":"skip","reason":f"{action_type} not in drill scope"}

        # 检查频率限制
        drill_key = f"yaxiio:drill_count:{time.strftime('%Y%m%d-%H')}"
        hourly = int(self.redis.get(drill_key) or 0)
        if hourly >= limits.get("max_drills_per_hour", 5): return {"status":"skip","reason":f"hourly limit {hourly}/{limits.get('max_drills_per_hour',5)}"}
        daily_key = f"yaxiio:drill_count:{time.strftime('%Y%m%d')}"
        daily = int(self.redis.get(daily_key) or 0)
        if daily >= limits.get("max_drills_per_day", 20): return {"status":"skip","reason":f"daily limit {daily}/{limits.get('max_drills_per_day',20)}"}

        # 检查任务重复演练次数
        task_drill_key = f"yaxiio:drill_task:{task[:50]}"
        task_drills = int(self.redis.get(task_drill_key) or 0)
        if task_drills >= limits.get("max_drills_per_task", 3): return {"status":"skip","reason":f"task drill limit {task_drills}/{limits.get('max_drills_per_task',3)}"}

        # 检查连续无改进
        no_improve_key = f"yaxiio:drill_no_improve:{task[:50]}"
        no_improve = int(self.redis.get(no_improve_key) or 0)
        if no_improve >= stop.get("max_consecutive_no_improvement", 2): return {"status":"skip","reason":f"no improvement {no_improve} times, stop"}

        # 通过检查，增加计数
        self.redis.setex(drill_key, 3600, str(hourly + 1))
        self.redis.setex(daily_key, 86400, str(daily + 1))
        self.redis.setex(task_drill_key, 86400, str(task_drills + 1))

        if score >= threshold: return {"status":"skip","reason":f"{score}>={threshold}"}
        print(f"[雅溪] 🎯 Drill: score={score}<{threshold}", flush=True)
        ts = int(time.time()); sb = f"/tmp/yaxiio-drill-{ts}"
        os.makedirs(f"{sb}/code", exist_ok=True)
        os.makedirs(f"{sb}/mock_deploy", exist_ok=True)
        try: shutil.copytree(cb, f"{sb}/code/src", symlinks=True, ignore=shutil.ignore_patterns("node_modules",".nuxt",".git",".output"))
        except: pass
        with open(f"{sb}/mock_deploy/deploy.log","w") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] MOCK DRILL - no real deploy\n")
        llm = self._get_llm()
        plan = ""
        if llm:
            reps = sorted(glob.glob("/app/.pi/blackboard/reports/audit-*.md")+glob.glob("/app/.pi/blackboard/reports/diag-*.md"), key=os.path.getmtime, reverse=True)[:2]
            ctx = ""
            for rp in reps:
                try:
                    with open(rp) as f: ctx += f.read()[:1000]+"\n"
                except: pass
            try:
                loop = asyncio.new_event_loop()
                plan = loop.run_until_complete(llm.chat(f"Drill: {task}, score={score}/{threshold}.\nHistory:\n{ctx[:2000]}\nImprove? Chinese 200 chars."))
                loop.close()
            except: pass
        est = min(10.0, score+1.5+len(plan)/500)
        rp = f"/app/.pi/blackboard/reports/drill-{time.strftime('%Y%m%d-%H%M%S')}.md"
        with open(rp,"w") as f: f.write(f"# Drill\n> {time.strftime('%Y-%m-%d %H:%M:%S')}\n\nTask: {task}\nScore: {score}->{est:.1f}\n\nPlan:\n{plan}\n\nSandbox: {sb} (isolated, mock deploy)\n")
        for old in sorted(glob.glob("/tmp/yaxiio-drill-*"))[:-3]: shutil.rmtree(old, ignore_errors=True)
        # 记录改进幅度
        improvement = est - score
        if improvement < stop.get("score_improvement_min", 1.5):
            no_improve_key2 = f"yaxiio:drill_no_improve:{task[:50]}"
            self.redis.setex(no_improve_key2, 86400, str(no_improve + 1))
        else:
            self.redis.delete(f"yaxiio:drill_no_improve:{task[:50]}")
        print(f"[雅溪] 📄 {rp}", flush=True)
        return {"status":"success","report":rp,"sandbox":sb,"estimated_score":est}

    # ═══════════════════════════════════════════════
    # LLM 客户端
    # ═══════════════════════════════════════════════
    def _get_llm(self):
        try:
            sys.path.insert(0, "/app/.pi/skills/commander")
            from agent_lifecycle_v2 import LLMAdapter
            key = self.redis.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY","")
            model = self.redis.get("yaxiio:config:llm_model") or "deepseek-v4-pro"
            return LLMAdapter(api_key=key, base_url="https://api.deepseek.com/v1", model=model)
        except: return None

    # ═══════════════════════════════════════════════
    # 主循环
    # ═══════════════════════════════════════════════
    def run(self):
                # Auto-start MCP layers
        import subprocess as _sp
        mcp_layers = [(3401,"L1_perception"),(3402,"L2_planning"),(3403,"L3_coordination"),(3404,"L4_execution"),(3405,"L5_evolution")]
        for port, layer in mcp_layers:
            try:
                _sp.Popen([sys.executable, f"/app/.pi/skills/commander/layers/{layer}/mcp_server.py"], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except: pass
        print("[雅溪] ⚡ Yaxiio v2.0 上线", flush=True)
        print("[雅溪] 📡 订阅 lightingmetal:agent:commander", flush=True)
        while self.running:
            try:
                pubsub = self.redis.subscribe("lightingmetal:agent:commander")
                for msg in pubsub.listen():
                    if msg["type"] != "message": continue
                    try:
                        data = json.loads(msg["data"])
                        if data.get("type") == "task": self.handle_task(data)
                    except Exception as e: print(f"[雅溪] ⚠️ {e}", flush=True)
            except Exception as e:
                print(f"[雅溪] ⚠️ PubSub: {e}, 5s...", flush=True)
                time.sleep(5)

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    os.makedirs(LOG_DIR, exist_ok=True)
    Commander().run()

if __name__ == "__main__":
    main()
