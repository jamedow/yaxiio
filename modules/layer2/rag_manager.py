"""RAG 检索增强 — Agent执行前自动注入相关知识"""
import os, json
from typing import List, Dict, Optional

class RAGManager:
    """检索增强生成管理器"""
    def __init__(self, vector_store=None, redis_client=None):
        self.vector = vector_store
        self.redis = redis_client
        self._indexed = set()

    def index_directory(self, path: str, pattern: str = ".md"):
        """索引目录中的文档"""
        count = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.endswith(pattern):
                    fp = os.path.join(root, fn)
                    key = f"doc:{fp}"
                    if key in self._indexed:
                        continue
                    try:
                        with open(fp) as f:
                            text = f.read()[:2000]
                        self.vector.add(key, text, {"file": fp, "type": "document"})
                        self._indexed.add(key)
                        count += 1
                    except:
                        pass
        if count:
            print(f"[RAG] 索引 {count} 个文档", flush=True)
        return count

    def index_code(self, path: str):
        """索引代码文件"""
        count = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ("node_modules",".nuxt",".git",".output")]
            for fn in files:
                if fn.endswith((".vue",".ts",".py",".js")):
                    fp = os.path.join(root, fn)
                    key = f"code:{fp}"
                    if key in self._indexed:
                        continue
                    try:
                        with open(fp) as f:
                            text = f.read()[:1500]
                        self.vector.add(key, text, {"file": fp, "type": "code", "lang": fn.split(".")[-1]})
                        self._indexed.add(key)
                        count += 1
                    except:
                        pass
        if count:
            print(f"[RAG] 索引 {count} 个代码文件", flush=True)
        return count

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """检索相关知识"""
        if not self.vector:
            return []
        results = self.vector.search(query, top_k)
        # 附加 Redis 缓存的相关 Agent 记忆
        if self.redis:
            try:
                mem = self.redis.get(f"agent:memory:{query[:30]}")
                if mem:
                    results.append({"key": "memory", "score": 0.8, "text": str(mem)[:500], "meta": {"type": "memory"}})
            except:
                pass
        return results

    def augment_prompt(self, base_prompt: str, task_desc: str, top_k: int = 3) -> str:
        """增强 Prompt——拼接检索到的相关知识"""
        docs = self.retrieve(task_desc, top_k)
        if not docs:
            return base_prompt
        context = "\n\n## 相关知识 (RAG检索)\n"
        for i, d in enumerate(docs):
            context += f"\n### {i+1}. {d.get('meta',{}).get('file', d['key'])}\n{d.get('text','')[:400]}\n"
        return context + "\n\n---\n" + base_prompt

    def count(self) -> int:
        return len(self._indexed)
