# ─────────────────────────────────────────────────────────────
# LightingMetal Commander 纯应用镜像 (不含 Redis/MongoDB)
# 用于 docker-compose 分离架构 (方案 B)
# ─────────────────────────────────────────────────────────────

FROM ubuntu:24.04

LABEL org.lightingmetal.image="commander-app"
LABEL version="v2.3"

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8

# 基础工具 + Python 3.12
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl wget gnupg \
    locales language-pack-zh-hans \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    build-essential procps net-tools \
    && rm -rf /var/lib/apt/lists/*

RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && python3 -m pip install --upgrade pip setuptools wheel

# Node 20 + PM2
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pm2@latest \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
RUN pip3 install --no-cache-dir \
    redis[hiredis]>=5.0 \
    pymongo>=4.6 \
    flask>=3.0 \
    jieba>=0.42 \
    openai>=1.30

# pi 内核 + MCP 适配器
RUN npm install -g @mariozechner/pi-coding-agent@latest pi-mcp-adapter@latest \
    && npm cache clean --force

WORKDIR /app
COPY .pi/skills/commander/ /app/.pi/skills/commander/
COPY .pi/agents/runtime/ /app/.pi/agents/runtime/
COPY .pi/skills/ /app/.pi/skills/

EXPOSE 3002 3003

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD pm2 ping || exit 1

COPY docker-entrypoint-app.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint-app.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint-app.sh"]
CMD ["all"]
