"""调度器升级 — 支持并行执行无依赖子任务"""
import threading, time
from typing import Dict, List

class ParallelScheduler:
    """并行调度器 — 无依赖子任务同时执行"""
    def __init__(self, agent_factory=None, lifecycle=None, max_workers: int = 5):
        self.factory = agent_factory
        self.lifecycle = lifecycle
        self.max_workers = max_workers

    def execute(self, task: dict) -> dict:
        """并行执行子任务"""
        subtasks = task.get("subtasks", [])
        if not subtasks:
            return {"status": "success", "subtasks": []}

        # 按依赖分组
        groups = self._topo_sort(subtasks)
        results = []

        for group in groups:
            if len(group) == 1:
                # 串行执行
                r = self._run_one(group[0])
                results.append(r)
            else:
                # 并行执行
                parallel_results = self._run_parallel(group)
                results.extend(parallel_results)

        return {"status": "success", "subtasks": results,
                "parallel_groups": len(groups), "max_parallel": max(len(g) for g in groups)}

    def _run_one(self, subtask: dict) -> dict:
        agent = subtask.get("agent_type", "通用Agent")
        if self.factory:
            aid = self.factory.create(role=agent, task=subtask)
        else:
            aid = "auto"
        time.sleep(0.1)  # 模拟执行
        return {"id": subtask.get("id"), "agent": aid, "status": "completed"}

    def _run_parallel(self, group: List[dict]) -> List[dict]:
        results = []
        threads = []

        def worker(st):
            r = self._run_one(st)
            results.append(r)

        for st in group:
            t = threading.Thread(target=worker, args=(st,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=30)

        return results

    def _topo_sort(self, subtasks: List[dict]) -> List[List[dict]]:
        """拓扑排序分组：同组无依赖可并行"""
        task_map = {s["id"]: s for s in subtasks}
        in_degree = {s["id"]: len(s.get("depends_on", [])) for s in subtasks}
        groups = []

        remaining = set(s["id"] for s in subtasks)
        while remaining:
            # 找出所有入度为0的（可并行）
            ready = [tid for tid in remaining if in_degree[tid] == 0]
            if not ready:
                break
            groups.append([task_map[tid] for tid in ready])
            for tid in ready:
                remaining.remove(tid)
                # 减少依赖此任务的其他任务的入度
                for s in subtasks:
                    if tid in s.get("depends_on", []):
                        in_degree[s["id"]] -= 1

        return groups
