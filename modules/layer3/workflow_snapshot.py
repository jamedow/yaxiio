"""工作流快照"""
import json, os, time
class WorkflowSnapshot:
    def __init__(self, path="/opt/commander/logs/snapshots"): self.path = path; os.makedirs(path, exist_ok=True)
    def save(self, tid: str, plan: dict, result: dict):
        with open(f"{self.path}/{tid}.json","w") as f: json.dump({"tid":tid,"plan":plan,"result":result,"time":time.time()},f,ensure_ascii=False,indent=2)
