"""
ChromaVectorStore — 基于 ChromaDB 的语义向量存储
=================================================
替代 MemVectorStore，提供:
- 语义相似度搜索 (不是关键词匹配)
- 持久化存储 (SQLite 后端)
- 自动降级: Chroma 不可用时回退到 MemVectorStore
"""

import os, json, time
from typing import List, Optional

CHROMA_PATH = os.environ.get("CHROMA_PATH", "/data/chroma")


class ChromaVectorStore:
    """ChromaDB 向量存储 — 语义搜索 + 自动降级"""

    def __init__(self, collection_name: str = "yaxiio_experiences"):
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._fallback = None  # MemVectorStore fallback

        try:
            __import__("pysqlite3")
            import sys as _sys
            _sys.modules["sqlite3"] = _sys.modules.pop("pysqlite3")
            import chromadb
            self._client = chromadb.PersistentClient(path=CHROMA_PATH)
            try:
                self._collection = self._client.get_collection(collection_name)
            except Exception:
                self._collection = self._client.create_collection(
                    name=collection_name,
                    metadata={"description": "Yaxiio L0 experience embeddings"}
                )
            print(f"[Chroma] ✅ 向量存储就绪: {collection_name} (path={CHROMA_PATH})")
        except ImportError:
            print("[Chroma] ⚠️ chromadb 未安装, 回退到 MemVectorStore")
        except Exception as e:
            print(f"[Chroma] ⚠️ 初始化失败: {e}, 回退到 MemVectorStore")

        if self._collection is None:
            from .vector_store import MemVectorStore
            self._fallback = MemVectorStore()

    def add(self, doc_id: str, text: str, metadata: dict = None):
        """添加文档到向量库"""
        if self._collection:
            try:
                self._collection.add(
                    ids=[doc_id],
                    documents=[text[:2000]],
                    metadatas=[metadata or {}],
                )
            except Exception as e:
                print(f"[Chroma] add error: {e}")
        if self._fallback:
            self._fallback.add(doc_id, text, metadata)

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """语义搜索最相似的文档"""
        results = []
        if self._collection:
            try:
                raw = self._collection.query(
                    query_texts=[query[:500]],
                    n_results=min(top_k, 20),
                )
                ids = raw.get("ids", [[]])[0]
                docs = raw.get("documents", [[]])[0]
                metas = raw.get("metadatas", [[]])[0]
                distances = raw.get("distances", [[]])[0]
                for i, doc_id in enumerate(ids):
                    results.append({
                        "id": doc_id,
                        "text": docs[i] if i < len(docs) else "",
                        "metadata": metas[i] if i < len(metas) else {},
                        "distance": distances[i] if i < len(distances) else 1.0,
                    })
            except Exception as e:
                print(f"[Chroma] search error: {e}")

        if self._fallback and not results:
            results = self._fallback.search(query, top_k)
        return results

    def search_experiences(self, intent: str, agent_name: str = "", top_k: int = 5) -> List[dict]:
        """搜索相关经验 — 专门为 L0 经验检索优化"""
        query = f"Task intent: {intent}"
        if agent_name:
            query += f" Agent: {agent_name}"
        return self.search(query, top_k)

    def count(self) -> int:
        if self._collection:
            try:
                return self._collection.count()
            except Exception:
                pass
        return 0

    def stats(self) -> dict:
        return {
            "backend": "chroma" if self._collection else "mem_fallback",
            "count": self.count(),
            "path": CHROMA_PATH if self._collection else "in-memory",
        }
