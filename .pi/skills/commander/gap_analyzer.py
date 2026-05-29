# Yaxiio v1.1 - AGPLv3
"""GapAnalyzer — L5 gap analysis and corrective action generation"""
import json, re

class GapAnalyzer:
    def analyze(self, task_id, payload, results, l5):
        task_desc = str(payload.get("task", payload.get("action", "")))[:500]
        score = l5.get("overall", 5)
        dims = l5.get("dimensions", {})
        needs_review = l5.get("needs_review", False)
        has_gap = score < 7 or needs_review
        if not has_gap:
            return {"has_gap": False, "gap_summary": "Goal met", "next_actions": [], "priority": "low"}
        issues = self.detect_issues(results)
        dim_agents = {"accuracy":"审计官","completeness":"审计官","professionalism":"LM内容工程师","actionability":"审计官","consistency":"翻译官"}
        actions = []
        if issues.get("mixed_lang",0) > 100:
            actions.append({"action":"translate_mixed_content","agent":"翻译官","description":"Fix %d mixed entries" % issues["mixed_lang"],"priority":"high","intent":"translate"})
        if issues.get("empty_fields",0) > 100:
            actions.append({"action":"fill_empty_content","agent":"LM内容工程师","description":"Fill %d empty fields" % issues["empty_fields"],"priority":"high","intent":"fix"})
        if issues.get("missing_pages",0) > 0:
            actions.append({"action":"create_missing_pages","agent":"LM内容工程师","description":"Create %d missing pages" % issues["missing_pages"],"priority":"medium","intent":"create"})
        if not actions:
            for dim, val in sorted(dims.items(), key=lambda x: x[1]):
                if val < 7:
                    agent = dim_agents.get(dim, "审计官")
                    actions.append({"action":"improve "+dim,"agent":agent,"description":"Improve "+dim+" (score: "+str(val)+"/10)","priority":"high" if val<5 else "medium"})
        actions = actions[:3]
        return {"has_gap":True,"gap_summary":"score="+str(score)+" issues="+str(issues),"next_actions":actions,"priority":"high" if score<5 else "medium","content_issues":issues}

    def detect_issues(self, results):
        issues = {"mixed_lang":0,"empty_fields":0,"missing_pages":0,"truncated":0}
        for sid, res in results.items():
            output = str(res.get("output",""))
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

    def to_subtasks(self, task_id, gap, payload, round_num):
        actions = gap.get("next_actions", [])
        if not actions: return []
        subtasks = []
        for i, act in enumerate(actions):
            sid = "s%d_%d" % (round_num, i+1)
            subtasks.append({"id":sid,"action":act.get("action","")[:60],"agent":act.get("agent","审计官"),"depends":act.get("depends",[]),"prompt":act.get("description","")[:500],"tool":act.get("tool","")})
        return subtasks
