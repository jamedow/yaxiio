#!/usr/bin/env python3
"""雅溪 Yaxiio — 五层模块化主入口"""
import sys, os, json, time, signal
sys.path.insert(0, "/opt/commander")

from modules.shared.config import LOG_DIR
from modules.layer1 import RedisClient, MongoClient, MCPRegistry, SkillLoader
from modules.layer2 import AgentFactory, LifecycleManager, ModelRouter, AgentRegistry
from modules.layer3 import TaskDecomposer, DependencyAnalyzer, Scheduler, WorkflowSnapshot
from modules.layer4 import AutoScorer, AuditLogger, FailureDetector
from modules.layer5 import PromptOptimizer, WorkflowOptimizer, ABTester, SkillAutoGenerator

class Commander:
    def __init__(self):
        # L1: 基础组件
        self.redis = RedisClient()
        self.mongo = MongoClient()
        self.mcp = MCPRegistry()
        self.skills = SkillLoader()
        # L2: 智能体
        self.model_router = ModelRouter()
        self.agent_factory = AgentFactory(self.redis, self.model_router)
        self.lifecycle = LifecycleManager(self.agent_factory, self.redis)
        self.registry = AgentRegistry(self.redis)
        # L3: 工作流
        self.scheduler = Scheduler(self.agent_factory, self.lifecycle)
        self.snapshot = WorkflowSnapshot()
        # L4: 评估
        self.scorer = AutoScorer()
        self.audit = AuditLogger(self.mongo)
        self.detector = FailureDetector()
        # L5: 进化
        self.prompt_opt = PromptOptimizer()
        self.workflow_opt = WorkflowOptimizer()
        self.ab = ABTester()
        self.skill_gen = SkillAutoGenerator()
        # 状态
        self.task_count = 0
        self.start_time = time.time()
        self.running = True

    def handle_task(self, data: dict):
        task_id = data.get("taskId", f"auto-{int(time.time())}")
        payload = data.get("payload", {})
        action = payload.get("action", "")
        print(f"[雅溪] 🧠 任务: {task_id} ({action})", flush=True)
        
        # 实际执行
        if action == "site_audit":
            result = self._run_audit(task_id, payload)
        elif action == "site_evolve":
            result = self._run_evolve(task_id, payload)
        elif action == "site_fix":
            result = self._run_fix(task_id, payload)
        else:
            result = self._run_diagnose(task_id, payload)
        
        score = self.scorer.score({"task_id": task_id, "action": action}, result)
        self.audit.log({"task_id": task_id, "action": action}, result, score)
        self.task_count += 1
        return result

    def _run_audit(self, task_id: str, payload: dict) -> dict:
        """L3+L4: 扫描代码 → LLM分析 → 写报告"""
        import asyncio
        codebase = payload.get("codebase", "/app/lightingmetal/customer-portal")
        print(f"[雅溪] 🔍 审计 {codebase}", flush=True)
        
        vue_files = []
        for root, dirs, files in os.walk(codebase):
            dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
            for fn in files:
                if fn.endswith(".vue"):
                    fp = os.path.join(root, fn)
                    with open(fp) as fh:
                        ct = fh.read()
                        ds = sum(1 for kw in ["card-zhongli","btn-zhongli","fangsheng","chain-divider","meander","vision"] if kw in ct)
                        old = sum(1 for kw in ["bg-white","bg-gray","text-gray","shadow-lg"] if kw in ct)
                        th = sum(1 for kw in ["useIndustryTheme","texture-kunlun","texture-myth","texture-jingzhe","texture-ding","texture-canal"] if kw in ct)
                        vue_files.append({"f":fp.replace(codebase,""),"l":len(ct.split(chr(10))),"ds":ds,"old":old,"th":th})
        
        findings = []
        llm = self._get_llm()
        if llm:
            summary = "\n".join(f"{v['f']}: {v['l']}L ds={v['ds']} old={v['old']} theme={v['th']}" for v in sorted(vue_files,key=lambda x:-x["ds"])[:15])
            try:
                loop = asyncio.new_event_loop()
                report = loop.run_until_complete(llm.chat(f"Audit {len(vue_files)} Vue files:\n{summary}\nAnalyze design compliance, old UI, theme pushdown. Markdown Chinese 300 chars."))
                loop.close()
                findings.append(report)
            except Exception as e:
                findings.append(f"LLM error: {e}")
        
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/audit-{ts}.md"
        rpt = f"# Yaxiio Audit\n> {ts} | {len(vue_files)} files\n\n| File | Lines | ds | old | theme |\n|------|-------|----|-----|-------|\n"
        for v in sorted(vue_files,key=lambda x:-x["ds"])[:20]:
            rpt += f"| {v['f']} | {v['l']} | {v['ds']} | {v['old']} | {v['th']} |\n"
        rpt += "\n## Analysis\n\n" + "\n".join(findings)
        with open(rp,"w") as f: f.write(rpt)
        print(f"[雅溪] 📄 {rp}", flush=True)
        return {"status":"success","report":rp,"files_scanned":len(vue_files)}

    def _run_fix(self, task_id: str, payload: dict) -> dict:
        """L3+L4: 读审计报告 → LLM生成方案 → 沙箱执行"""
        import asyncio, shutil
        codebase = payload.get("codebase", "/app/lightingmetal/customer-portal")
        reports = sorted([f for f in os.listdir("/app/.pi/blackboard/reports") if f.startswith("diag-") or f.startswith("audit-")], reverse=True)
        if not reports: return {"status":"fail","error":"no audit report"}
        with open(f"/app/.pi/blackboard/reports/{reports[0]}") as f:
            audit = f.read()[:4000]
        
        llm = self._get_llm()
        if not llm: return {"status":"fail","error":"LLM offline"}
        
        print("[雅溪] 🔧 生成修复方案...", flush=True)
        try:
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(llm.chat(f"Audit:\n{audit[:3000]}\nSuggest concrete fixes. Format: filepath | replace_color | old->new. 5-10 items. Chinese 300 chars."))
            loop.close()
        except Exception as e: return {"status":"fail","error":str(e)}
        
        print(f"[雅溪] 方案: {plan[:200]}", flush=True)
        sandbox = f"/tmp/yaxiio-fix-{int(time.time())}"
        shutil.copytree(codebase, sandbox+"/code", symlinks=True, ignore=shutil.ignore_patterns("node_modules",".nuxt",".git",".output"))
        
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
        
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/fix-{ts}.md"
        with open(rp,"w") as f: f.write(f"# Fix Report\n> {task_id}\n\n## Plan\n\n{plan}\n\n## Applied ({applied})\n"+"\n".join(diffs))
        print(f"[雅溪] 📄 {rp} ({applied})", flush=True)
        return {"status":"success","report":rp,"applied":applied,"sandbox":sandbox}

    def _run_diagnose(self, task_id: str, payload: dict) -> dict:
        """LLM 通用诊断：读代码→分析问题→写报告"""
        import asyncio, fnmatch
        codebase = payload.get("codebase", "/app/lightingmetal/customer-portal")
        issue = payload.get("issue", payload.get("task", str(payload)))
        files_hint = payload.get("files", "")
        
        print(f"[雅溪] 🔍 诊断: {issue[:80]}", flush=True)
        
        # 收集相关代码
        relevant = []
        patterns = files_hint.split(",") if files_hint else ["*.vue", "*.ts", "*.js"]
        for root, dirs, files in os.walk(codebase):
            dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
            for fn in files:
                for pat in patterns:
                    if fnmatch.fnmatch(fn, pat.strip()):
                        fp = os.path.join(root, fn)
                        try:
                            with open(fp) as fh:
                                ct = fh.read()
                            if any(kw in ct.lower() for kw in ("lang","locale","i18n","switch","mobile","overflow","card","responsive") if kw in issue.lower() or kw in files_hint.lower()):
                                relevant.append({"file": fp.replace(codebase,""), "code": ct[:2000]})
                        except: pass
                        break
        
        if not relevant:
            relevant.append({"file": "all .vue files", "code": "scanning all files..."})
        
        # LLM 诊断
        findings = []
        llm = self._get_llm()
        if llm:
            ctx = "\n\n".join(f"=== {r['file']} ===\n{r['code']}" for r in relevant[:8])
            try:
                loop = asyncio.new_event_loop()
                report = loop.run_until_complete(llm.chat(
                    f"诊断 lightingmetal.com 的问题：{issue}\n\n相关代码：\n{ctx[:4000]}\n\n请分析：1)根因 2)影响范围 3)修复方案(具体代码)。Markdown中文，300字。"
                ))
                loop.close()
                findings.append(report)
            except Exception as e:
                findings.append(f"LLM: {e}")
        
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/diag-{ts}.md"
        rpt = f"# 诊断报告\n> {task_id} | {ts}\n\n## 问题\n{issue}\n\n## LLM分析\n\n" + "\n".join(findings)
        with open(rp,"w") as f: f.write(rpt)
        print(f"[雅溪] 📄 {rp}", flush=True)
        return {"status":"success","report":rp}


    def _run_evolve(self, task_id: str, payload: dict) -> dict:
        """L5 自进化：读自身代码→LLM发现缺口→生成补丁→验证→应用→重启"""
        import asyncio, shutil, subprocess
        target = payload.get("target", "/opt/commander/yaxiio.py")
        requirement = payload.get("requirement", payload.get("task", "分析并改进自身"))
        
        print(f"[雅溪] 🧬 自进化: {requirement[:80]}", flush=True)
        
        # 1. 读取自身代码
        my_code = ""
        for root, dirs, files in os.walk("/opt/commander/modules"):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.endswith(".py") and "bak" not in fn:
                    fp = os.path.join(root, fn)
                    with open(fp) as fh:
                        my_code += f"\n=== {fp.replace(chr(47)+chr(111)+chr(112)+chr(116)+chr(47)+chr(99)+chr(111)+chr(109)+chr(109)+chr(97)+chr(110)+chr(100)+chr(101)+chr(114),chr(47))} ===\n{fh.read()[:800]}"
        with open(target) as fh:
            my_code += f"\n=== yaxiio.py ===\n{fh.read()[:2000]}"
        
        # 2. LLM 分析并生成补丁
        llm = self._get_llm()
        if not llm: return {"status":"fail","error":"LLM offline"}
        
        try:
            loop = asyncio.new_event_loop()
            plan = loop.run_until_complete(llm.chat(
                f"你是雅溪，需要进化自己。\n需求：{requirement}\n当前代码：\n{my_code[:4000]}\n\n"
                "如果需要在 yaxiio.py 中添加新方法，输出格式：\n"
                "FILE: yaxiio.py\nACTION: add_method\nMETHOD_NAME: xxx\nCODE:\n```python\n...\n```\n\n"
                "如果只需修改现有方法，输出：\nFILE: yaxiio.py\nACTION: patch\nFIND: 旧代码片段\nREPLACE: 新代码片段\n\n"
                "用中文，200字内。"
            ))
            loop.close()
        except Exception as e: return {"status":"fail","error":str(e)}
        
        print(f"[雅溪] 💭 进化计划: {plan[:200]}", flush=True)
        
        # 3. 解析并应用补丁
        applied = 0
        if "FILE:" in plan and "CODE:" in plan:
            try:
                code_block = plan.split("```python")[1].split("```")[0] if "```python" in plan else ""
                method_name = plan.split("METHOD_NAME:")[1].split("\n")[0].strip() if "METHOD_NAME:" in plan else "new_method"
                
                if code_block:
                    # 备份
                    shutil.copy(target, target + ".prev")
                    with open(target) as fh: orig = fh.read()
                    
                    # 插入新方法（在 _get_llm 之前）
                    marker = "    def _get_llm(self):"
                    if marker in orig:
                        new_code = orig.replace(marker, code_block + "\n\n    " + marker)
                        # 语法检查
                        try:
                            compile(new_code, target, "exec")
                            with open(target, "w") as fh: fh.write(new_code)
                            applied = 1
                            print(f"[雅溪] ✅ 已添加方法: {method_name}", flush=True)
                        except SyntaxError as se:
                            print(f"[雅溪] ❌ 语法错误: {se}", flush=True)
                            shutil.copy(target + ".prev", target)
            except Exception as e:
                print(f"[雅溪] ⚠️ 补丁应用失败: {e}", flush=True)
                shutil.copy(target + ".prev", target) if os.path.exists(target + ".prev") else None
        
        elif "FIND:" in plan and "REPLACE:" in plan:
            try:
                find = plan.split("FIND:")[1].split("REPLACE:")[0].strip()
                replace = plan.split("REPLACE:")[1].split("\n")[0].strip()
                shutil.copy(target, target + ".prev")
                with open(target) as fh: orig = fh.read()
                if find in orig:
                    new_code = orig.replace(find, replace, 1)
                    compile(new_code, target, "exec")
                    with open(target, "w") as fh: fh.write(new_code)
                    applied = 1
                    print(f"[雅溪] ✅ 已应用补丁", flush=True)
            except Exception as e:
                print(f"[雅溪] ⚠️ 补丁失败: {e}", flush=True)
        
        # 4. 写报告
        ts = time.strftime("%Y%m%d-%H%M%S")
        rp = f"/app/.pi/blackboard/reports/evolve-{ts}.md"
        with open(rp,"w") as f: f.write(f"# 自进化报告\n> {ts}\n\n## 需求\n{requirement}\n\n## LLM方案\n\n{plan}\n\n## 结果\n应用{applied}处修改")
        print(f"[雅溪] 📄 {rp}", flush=True)
        
        # 5. 如果有改动，触发重启
        if applied:
            print("[雅溪] 🔄 触发重启以应用进化...", flush=True)
            os._exit(42)  # Guard 会检测并重启
        
        return {"status":"success","applied":applied,"report":rp}

    def _get_llm(self):
        """获取 LLM 客户端"""
        try:
            sys.path.insert(0, "/app/.pi/skills/commander")
            from agent_lifecycle_v2 import LLMAdapter
            key = self.redis.get("yaxiio:config:llm_api_key") or os.environ.get("DEEPSEEK_API_KEY","")
            model = self.redis.get("yaxiio:config:llm_model") or "deepseek-v4-pro"
            return LLMAdapter(api_key=key, base_url="https://api.deepseek.com/v1", model=model)
        except:
            return None

    def run(self):
        print(f"[雅溪] ⚡ Yaxiio v2.0 五层架构上线", flush=True)
        print(f"[雅溪] 📡 订阅 lightingmetal:agent:commander", flush=True)
        
        while self.running:
            try:
                pubsub = self.redis.subscribe("lightingmetal:agent:commander")
                for msg in pubsub.listen():
                    if msg["type"] != "message": continue
                    try:
                        data = json.loads(msg["data"])
                        if data.get("type") == "task":
                            self.handle_task(data)
                    except Exception as e:
                        print(f"[雅溪] ⚠️ {e}", flush=True)
            except Exception as e:
                print(f"[雅溪] ⚠️ PubSub断开: {e}, 5s重连...", flush=True)
                time.sleep(5)

    def shutdown(self):
        self.running = False
        print("[雅溪] 👋 下线", flush=True)

def main():
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    
    os.makedirs(LOG_DIR, exist_ok=True)
    print(f"[雅溪] 🛡️ 启动五层架构", flush=True)
    
    commander = Commander()
    commander.run()

if __name__ == "__main__":
    main()
