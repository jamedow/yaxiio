"""向量存储 — 轻量级，支持 Chroma 或内存模式"""
import os, json, math, hashlib
from typing import List, Dict, Optional

class VectorStore:
    """向量存储基类"""
    def add(self, key: str, text: str, meta: dict = None): pass
    def search(self, query: str, top_k: int = 5) -> List[Dict]: return []
    def delete(self, key: str): pass
    def count(self) -> int: return 0


class MemVectorStore(VectorStore):
    """内存向量存储 — 零依赖，基于 TF-IDF 相似度"""
    def __init__(self):
        self._docs = {}   # key → {text, meta}
        self._idf = {}    # term → idf

    def _tokenize(self, text: str) -> List[str]:
        """简单分词"""
        import re
        return [w.lower() for w in re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', text) if len(w) > 1]

    def add(self, key: str, text: str, meta: dict = None):
        tokens = self._tokenize(text)
        tf = {}
        for t in tokens:
            tf[t] = tf.get(t, 0) + 1
            self._idf[t] = self._idf.get(t, 0) + 1
        self._docs[key] = {"text": text, "meta": meta or {}, "tokens": tokens, "tf": tf}

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        q_tokens = self._tokenize(query)
        if not q_tokens or not self._docs:
            return []
        # TF-IDF 相似度
        N = len(self._docs)
        scores = {}
        for key, doc in self._docs.items():
            score = 0
            for t in set(q_tokens):
                if t not in doc["tf"]:
                    continue
                tf = doc["tf"][t] / max(len(doc["tokens"]), 1)
                idf = math.log((N + 1) / (self._idf.get(t, 0) + 1)) + 1
                score += tf * idf
            if score > 0:
                scores[key] = score
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        return [{"key": k, "score": round(s, 3), "meta": self._docs[k]["meta"],
                 "text": self._docs[k]["text"][:200]} for k, s in ranked]

    def delete(self, key: str):
        if key in self._docs:
            # 更新 IDF
            doc = self._docs[key]
            for t in doc["tf"]:
                self._idf[t] = max(0, self._idf.get(t, 0) - 1)
            del self._docs[key]

    def count(self) -> int:
        return len(self._docs)


class ChromaVectorStore(VectorStore):
    """Chroma 向量存储 — pip install chromadb"""
    def __init__(self, path: str = None):
        self._path = path or os.path.join(os.environ.get("DATA_DIR", "/opt/commander/data"), "chroma")
        self._client = None
        self._collection = None
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=self._path)
            self._collection = self._client.get_or_create_collection("yaxiio_skills")
        except ImportError:
            print("[VectorStore] chromadb not installed, using MemVectorStore")

    def add(self, key: str, text: str, meta: dict = None):
        if not self._collection: return
        self._collection.add(documents=[text], metadatas=[meta or {}], ids=[key])

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        if not self._collection: return []
        results = self._collection.query(query_texts=[query], n_results=top_k)
        return [{"key": ids[0], "score": round(dist, 3), "text": doc[:200]}
                for ids, dist, doc in zip(results["ids"], results["distances"], results["documents"])]

    def delete(self, key: str):
        if self._collection: self._collection.delete(ids=[key])

    def count(self) -> int:
        if self._collection: return self._collection.count()
        return 0


def create_vector_store(backend: str = "memory", path: str = None) -> VectorStore:
    if backend == "chroma":
        return ChromaVectorStore(path)
    return MemVectorStore()
