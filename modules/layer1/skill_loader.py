"""Skill 加载和管理 — 热加载 + 向量索引"""
import os, json, time, hashlib, threading
from modules.shared.config import SKILL_DIR, DATA_DIR

class SkillLoader:
    def __init__(self, vector_store=None):
        self.skills = {}
        self._mtimes = {}
        self._watcher = None
        self._running = False
        self.vector = vector_store  # 可选向量索引
        self._load()
        self._start_watcher()

    def _load(self):
        if not os.path.exists(SKILL_DIR): return
        count = 0
        for name in os.listdir(SKILL_DIR):
            path = os.path.join(SKILL_DIR, name)
            if not os.path.isdir(path): continue
            md = os.path.join(path, "SKILL.md")
            if not os.path.exists(md): continue
            mtime = os.path.getmtime(md)
            if name in self._mtimes and self._mtimes[name] == mtime:
                continue  # 未变化，跳过
            self._mtimes[name] = mtime
            with open(md) as f:
                doc = f.read()
                self.skills[name] = {"name": name, "path": path, "doc": doc, "mtime": mtime}
                # 向量索引
                if self.vector:
                    self.vector.add(f"skill:{name}", doc[:500], {"type": "skill", "name": name})
            count += 1
        if count:
            print(f"[SkillLoader] 加载/更新 {count} 个 Skill", flush=True)

    def _start_watcher(self):
        """后台线程每30秒检查Skill目录变化"""
        def watch():
            self._running = True
            while self._running:
                time.sleep(30)
                try:
                    self._load()
                except Exception:
                    pass
        self._watcher = threading.Thread(target=watch, daemon=True)
        self._watcher.start()

    def stop(self):
        self._running = False

    def list_all(self):
        return list(self.skills.keys())

    def get(self, name: str):
        return self.skills.get(name)

    def search(self, query: str, top_k: int = 5):
        """向量语义搜索 Skill"""
        if self.vector:
            return self.vector.search(query, top_k)
        # fallback: 关键词匹配
        results = []
        for name, skill in self.skills.items():
            score = sum(1 for w in query if w in skill["doc"])
            if score > 0:
                results.append({"name": name, "score": score, "doc": skill["doc"][:200]})
        return sorted(results, key=lambda x: -x["score"])[:top_k]
