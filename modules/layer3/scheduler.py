import json
import threading
from concurrent.futures import ThreadPoolExecutor
import redis
from modules.layer3.task_decomposer import TaskDecomposer
from modules.shared.config import config  # Yaxiio config

class Scheduler:
    def __init__(self, agent_factory=None, lifecycle=None):
        self.factory = agent_factory
        self.lifecycle = lifecycle
        self.redis = redis.Redis(**config.REDIS_CONFIG)

    def execute(self, task: dict) -> dict:
        subtasks = self._get_subtasks(task)
        if not subtasks:
            return {"status": "success", "subtasks": []}

        task_map = {st["id"]: st for st in subtasks}
        dep_count = {st["id"]: len(st.get("dependencies", [])) for st in subtasks}
        dependents = {st["id"]: [] for st in subtasks}
        for st in subtasks:
            for dep in st.get("dependencies", []):
                if dep in dependents:
                    dependents[dep].append(st["id"])

        results = {}
        lock = threading.Lock()
        total = len(subtasks)
        completed = 0
        all_done = threading.Event()

        def run_subtask(st):
            nonlocal completed
            try:
                res = self._execute_subtask(st)
            except Exception as e:
                res = {"error": str(e)}
            with lock:
                results[st["id"]] = res
                completed += 1
                if completed == total:
                    all_done.set()
            ready = []
            with lock:
                for dep_id in dependents.get(st["id"], []):
                    dep_count[dep_id] -= 1
                    if dep_count[dep_id] == 0:
                        ready.append(task_map[dep_id])
            for ready_st in ready:
                executor.submit(run_subtask, ready_st)

        with ThreadPoolExecutor(max_workers=10) as executor:
            for st in subtasks:
                if dep_count[st["id"]] == 0:
                    executor.submit(run_subtask, st)
            if total == 0:
                all_done.set()
            all_done.wait()

        final_results = [{"id": st["id"], "result": results.get(st["id"])} for st in subtasks]
        return {"status": "success", "subtasks": final_results}

    def _execute_subtask(self, subtask):
        role = self._determine_role(subtask)
        agent_id = self.factory.create(role=role, task=subtask) if self.factory else "auto"
        task_channel = f"tasks:{role}"
        result_channel = f"result:{subtask['id']}"
        message = json.dumps({
            "task_id": subtask["id"],
            "action": subtask["action"],
            "params": subtask.get("params", {}),
            "agent_id": agent_id,
            "result_channel": result_channel
        })
        pubsub = self.redis.pubsub()
        try:
            pubsub.subscribe(result_channel)
            self.redis.publish(task_channel, message)
            for msg in pubsub.listen():
                if msg["type"] == "message":
                    return json.loads(msg["data"])
        finally:
            pubsub.unsubscribe(result_channel)
            pubsub.close()

    def _get_subtasks(self, task):
        if "subtasks" in task and isinstance(task["subtasks"], list):
            return task["subtasks"]
        decomposer = TaskDecomposer()
        return decomposer.decompose(task)

    def _determine_role(self, subtask):
        action = subtask.get("action", "")
        role_map = {
            "scan_codebase": "审计Agent",
            "llm_analyze": "审计Agent",
            "write_report": "审计Agent",
            "generate_fixes": "修复Agent",
            "apply_fixes": "修复Agent",
        }
        return role_map.get(action, "通用Agent")