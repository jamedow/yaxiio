"""Yaxiio 共享配置"""
import os

# ── 基础设施 ──
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "Yaxiio2026")
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/")

# ── LLM ──
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-v4-pro")

# ── 路径 ──
BASE_DIR = "/opt/commander"
SKILL_DIR = os.path.join(BASE_DIR, "skills")
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")
AGENT_DIR = os.path.join(BASE_DIR, "agents")
BLACKBOARD_DIR = "/app/.pi/blackboard/reports"
CODEBASE = os.environ.get("CODEBASE", "/app/lightingmetal/customer-portal")

# ── Agent ──
MAX_AGENTS = 10
AGENT_HEARTBEAT_TIMEOUT = 60
MAX_RESTARTS = 3
RESTART_PERIOD = 120

# ── MCP 五层独立开关 (Phase 0: 默认全部走旧引擎) ──
# 每迁移一层 → 改一个 False → True → 验证 → 下一层
# 回滚也是单层回滚：改回 False 即可
MCP_LAYERS_ENABLED = {
    "L1": False,  # perception-server: 意图识别与工具分发
    "L2": False,  # planning-server: 任务拆解与模型路由
    "L3": False,  # coordination-server: 并行编排与依赖调度
    "L4": False,  # execution-server: Agent分配与执行
    "L5": False,  # evolution-server: 评分、优化与进化
}

# 对象式访问（兼容雅溪自生成的代码）
class Config:
    def __init__(self):
        self.REDIS_HOST = REDIS_HOST
        self.REDIS_PORT = REDIS_PORT
        self.REDIS_PASSWORD = REDIS_PASSWORD
        self.MONGO_URI = MONGO_URI
        self.LLM_API_KEY = LLM_API_KEY
        self.LLM_BASE_URL = LLM_BASE_URL
        self.LLM_MODEL = LLM_MODEL
        self.BASE_DIR = BASE_DIR
        self.SKILL_DIR = SKILL_DIR
        self.CODEBASE = CODEBASE
        self.MAX_AGENTS = MAX_AGENTS
        self.BLACKBOARD_DIR = BLACKBOARD_DIR

config = Config()
# Redis 配置字典（兼容雅溪自生成代码）
config.REDIS_CONFIG = {
    "host": REDIS_HOST, "port": REDIS_PORT, "password": REDIS_PASSWORD,
    "decode_responses": True, "socket_connect_timeout": 5
}
config.MONGO_CONFIG = {"uri": MONGO_URI, "serverSelectionTimeoutMS": 3000}
