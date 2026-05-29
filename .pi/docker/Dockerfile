# ─────────────────────────────────────────────────────────────
# LightingMetal Commander 全能镜像
# Base: Ubuntu 24.04 LTS (Noble Numbat) — 支持到 2029
# Python 3.12 + Node 20 LTS + Redis 7 + MongoDB 7 + PM2 + pi
# ─────────────────────────────────────────────────────────────

FROM ubuntu:24.04

LABEL org.lightingmetal.image="commander-allinone"
LABEL description="LightingMetal Multi-Agent Commander — 六大优化引擎 + Dashboard + A2A协议层"
LABEL version="v2.3"

# ─────────────────────────────────────────────────────────────
# 1. 系统基础
# ─────────────────────────────────────────────────────────────
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update && apt-get install -y --no-install-recommends \
    # 基础工具
    ca-certificates curl wget gnupg \
    # 中文支持
    locales language-pack-zh-hans \
    # Python 3.12
    python3.12 python3.12-dev python3.12-venv python3-pip \
    # 编译依赖 (pymongo/redis 需要)
    build-essential \
    # Redis
    redis-server \
    # 进程管理
    procps net-tools iputils-ping \
    # 清理
    && rm -rf /var/lib/apt/lists/*

# Python 软链接
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && python3 -m pip install --upgrade pip setuptools wheel

# 中文 locale
RUN locale-gen zh_CN.UTF-8 && update-locale LANG=zh_CN.UTF-8

# ─────────────────────────────────────────────────────────────
# 2. Node.js 20 LTS + PM2
# ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pm2@latest \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────
# 3. MongoDB 7.0
# ─────────────────────────────────────────────────────────────
RUN curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc | \
    gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg] \
    https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" \
    > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update \
    && apt-get install -y mongodb-org \
    && rm -rf /var/lib/apt/lists/*

# MongoDB 数据目录
RUN mkdir -p /data/db /data/log && chown -R mongodb:mongodb /data

# ─────────────────────────────────────────────────────────────
# 4. Python 依赖 (六大引擎所需)
# ─────────────────────────────────────────────────────────────
RUN pip3 install --no-cache-dir \
    redis[hiredis]>=5.0 \
    pymongo>=4.6 \
    flask>=3.0 \
    jieba>=0.42 \
    openai>=1.30

# ─────────────────────────────────────────────────────────────
# 5. pi 编码 Agent 内核 + MCP 服务器
# ─────────────────────────────────────────────────────────────
RUN npm install -g \
    @mariozechner/pi-coding-agent@latest \
    pi-mcp-adapter@latest \
    && npm cache clean --force

# ─────────────────────────────────────────────────────────────
# 6. 应用代码
# ─────────────────────────────────────────────────────────────
WORKDIR /app

# Commander 六大引擎
COPY .pi/skills/commander/ /app/.pi/skills/commander/

# Agent 运行时
COPY .pi/agents/runtime/ /app/.pi/agents/runtime/

# 技能配置
COPY .pi/skills/ /app/.pi/skills/

# ─────────────────────────────────────────────────────────────
# 7. Redis 配置
# ─────────────────────────────────────────────────────────────
RUN echo 'requirepass Lt@114514!' >> /etc/redis/redis.conf \
    && echo 'maxmemory 256mb' >> /etc/redis/redis.conf \
    && echo 'maxmemory-policy allkeys-lru' >> /etc/redis/redis.conf \
    && echo 'save 900 1' >> /etc/redis/redis.conf \
    && echo 'save 300 10' >> /etc/redis/redis.conf

# ─────────────────────────────────────────────────────────────
# 8. 入口脚本
# ─────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# ─────────────────────────────────────────────────────────────
# 9. 健康检查
# ─────────────────────────────────────────────────────────────
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD redis-cli -a 'Lt@114514!' ping && mongosh --quiet --eval 'db.runCommand("ping")' || exit 1

# ─────────────────────────────────────────────────────────────
# 端口
# ─────────────────────────────────────────────────────────────
EXPOSE 6379 27017 3002 3003

# 数据卷
VOLUME ["/data/db", "/data/log"]

# 入口
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["all"]
