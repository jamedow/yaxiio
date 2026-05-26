"""MongoDB 连接管理"""
from pymongo import MongoClient as PyMongo
from modules.shared.config import MONGO_URI
class MongoClient:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    def __init__(self):
        if self._initialized: return
        self._initialized = True
        try: self.client = PyMongo(MONGO_URI, serverSelectionTimeoutMS=3000)
        except: self.client = None
    def insert_one(self, collection: str, doc: dict):
        if self.client: self.client["lightingmetal"][collection].insert_one(doc)
    def find(self, collection: str, query: dict = None, limit: int = 100):
        if self.client: return list(self.client["lightingmetal"][collection].find(query or {}).limit(limit))
        return []
