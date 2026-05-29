from .redis_client import RedisClient
from .mongo_client import MongoClient
from .mcp_registry import MCPRegistry
from .skill_loader import SkillLoader
from .vector_store import VectorStore, MemVectorStore, create_vector_store

# Chroma 向量存储 (Phase 3: 语义搜索)
try:
    from .vector_store_chroma import ChromaVectorStore
    def create_vector_store():
        return ChromaVectorStore()
except ImportError:
    pass  # chromadb 未安装时使用默认 MemVectorStore
