
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_CFG_TOKEN = '95ed1320'

"""
雅溪 Yaxiio v3.1 — 统一配置中心
=============================
所有配置通过环境变量注入，单点管理。

五层 MCP Server 端口:
  L1_PERCEPTION_PORT=3401
  L2_PLANNING_PORT=3402
  L3_COORDINATION_PORT=3403
  L4_EXECUTION_PORT=3404
  L5_EVOLUTION_PORT=3405

跨层服务:
  ORCHESTRATOR_HOST=127.0.0.1
  ORCHESTRATOR_PORT=3300
"""

import os

# ── 基础设施 ──────────────────────────────────────────
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
# ── 持久化 (SQLite) ──────────────────────────────
YAXIO_DB = os.environ.get("YAXIO_DB", "/opt/commander/data/yaxiio.db")

# ── LLM ──────────────────────────────────────────────
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY", os.environ.get("LLM_API_KEY", ""))
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# ── 五层端口 ─────────────────────────────────────────
L1_PERCEPTION_PORT = int(os.environ.get("L1_PERCEPTION_PORT", "3401"))
L2_PLANNING_PORT = int(os.environ.get("L2_PLANNING_PORT", "3402"))
L3_COORDINATION_PORT = int(os.environ.get("L3_COORDINATION_PORT", "3403"))
L4_EXECUTION_PORT = int(os.environ.get("L4_EXECUTION_PORT", "3404"))
L5_EVOLUTION_PORT = int(os.environ.get("L5_EVOLUTION_PORT", "3405"))

# ── 跨层 ─────────────────────────────────────────────
ORCHESTRATOR_HOST = os.environ.get("ORCHESTRATOR_HOST", "127.0.0.1")
ORCHESTRATOR_PORT = int(os.environ.get("ORCHESTRATOR_PORT", "3300"))
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_V2_PORT", "3003"))

# ── Session ──────────────────────────────────────────
SESSION_TOKEN_SECRET = os.environ.get("SESSION_TOKEN_SECRET", "commander-v3-secret")
MAX_OFFLINE_QUEUE = int(os.environ.get("SESSION_MAX_OFFLINE_QUEUE", "1000"))
MAX_HISTORY_REDIS = int(os.environ.get("SESSION_MAX_HISTORY_REDIS", "500"))
OFFLINE_ARCHIVE_HOURS = int(os.environ.get("SESSION_OFFLINE_ARCHIVE_HOURS", "24"))

# ── WebSocket ────────────────────────────────────────
WS_PORT = int(os.environ.get("WS_PORT", "3398"))
WS_HOST = os.environ.get("WS_HOST", "0.0.0.0")
WS_PING_INTERVAL = int(os.environ.get("WS_PING_INTERVAL", "15"))
WS_PING_TIMEOUT = int(os.environ.get("WS_PING_TIMEOUT", "10"))

# ── 评分与路由 ──────────────────────────────────────
SCORE_THRESHOLD = int(os.environ.get("SCORE_THRESHOLD", "6"))
LLM_THRESHOLD_TOKEN = int(os.environ.get("LLM_THRESHOLD_TOKEN", "500"))

# ── 监督树 ──────────────────────────────────────────
SUPERVISION_STRATEGY = os.environ.get("SUPERVISION_STRATEGY", "one_for_one")
MAX_RESTARTS = int(os.environ.get("MAX_RESTARTS_PER_PERIOD", "5"))
RESTART_PERIOD = int(os.environ.get("RESTART_PERIOD_SECONDS", "60"))

# ── Skill ────────────────────────────────────────────
SKILL_DIR = os.environ.get("SKILL_DIR", "/app/data/skills")
SKILL_MAX_CHARS = int(os.environ.get("SKILL_MAX_CHARS", "2200"))
CONTEXT_MAX_CHARS = int(os.environ.get("CONTEXT_MAX_CHARS", "1375"))

# ── 优化算法 ────────────────────────────────────────
TEXTGRAD_ENABLED = os.environ.get("TEXTGRAD_ENABLED", "true").lower() == "true"
AFLOW_ENABLED = os.environ.get("AFLOW_ENABLED", "true").lower() == "true"
MIPRO_ENABLED = os.environ.get("MIPRO_ENABLED", "true").lower() == "true"

# ── 审计 ────────────────────────────────────────────
AUDIT_ENABLED = os.environ.get("AUDIT_ENABLED", "true").lower() == "true"
AUDIT_LOG_LEVEL = os.environ.get("AUDIT_LOG_LEVEL", "INFO")
AUDIT_BATCH_SIZE = int(os.environ.get("AUDIT_BATCH_SIZE", "50"))

# ── 雅溪 Yaxiio 版本 ──────────────────────────────────
YAXIO_VERSION = os.environ.get("YAXIO_VERSION", "1.0.0")

# ── 五层 MCP URL 构建 ───────────────────────────────
LAYER_URLS = {
    "perception": f"http://127.0.0.1:{L1_PERCEPTION_PORT}",
    "planning": f"http://127.0.0.1:{L2_PLANNING_PORT}",
    "coordination": f"http://127.0.0.1:{L3_COORDINATION_PORT}",
    "execution": f"http://127.0.0.1:{L4_EXECUTION_PORT}",
    "evolution": f"http://127.0.0.1:{L5_EVOLUTION_PORT}",
}
