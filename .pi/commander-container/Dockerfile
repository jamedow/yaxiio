# ── Commander Container ──
# Multi-Agent System for LightingMetal
# Base: Python 3.9+ with Node.js (for PM2)
FROM python:3.9-slim

LABEL org.opencontainers.image.title="Commander - Multi-Agent Orchestrator"
LABEL org.opencontainers.image.description="LightingMetal 多Agent 系统总指挥 — 六大优化引擎 + 动态扩展"
LABEL org.opencontainers.image.version="2.3.0"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.source="https://github.com/lightingmetal/commander"

# ── 系统依赖 ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    procps \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# ── PM2 进程管理器 ──
RUN npm install -g pm2@latest

# ── Python 依赖 ──
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ── 应用代码 ──
WORKDIR /app

# 源码（按依赖顺序，maximize layer caching）
COPY src/ /app/src/
COPY agents/ /app/agents/
COPY scripts/ /app/scripts/
COPY docs/ /app/docs/
COPY LICENSE NOTICE VERSION /app/

# ── 运行时配置 ──
ENV PYTHONUNBUFFERED=1
ENV REDIS_HOST=redis
ENV REDIS_PORT=6379
ENV REDIS_PASSWORD=commander-secret
ENV DASHBOARD_PORT=3003

# ── 健康检查 ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${DASHBOARD_PORT}/health || exit 1

# ── 入口 ──
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/agents/*.sh /app/scripts/*.sh 2>/dev/null || true

EXPOSE 3003

ENTRYPOINT ["/entrypoint.sh"]
