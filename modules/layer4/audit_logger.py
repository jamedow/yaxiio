"""审计日志"""
import json, os, time
class AuditLogger:
    def __init__(self, mongo=None): self.mongo = mongo; os.makedirs("/opt/commander/logs", exist_ok=True)
    def log(self, task: dict, result: dict, score: dict = None):
        e = {"time":time.strftime("%Y-%m-%dT%H:%M:%S"),"task_id":task.get("task_id",""),"action":task.get("action",""),"status":result.get("status",""),"score":score.get("score") if score else None}
        with open("/opt/commander/logs/audit.jsonl","a") as f: f.write(json.dumps(e,ensure_ascii=False)+"\n")
