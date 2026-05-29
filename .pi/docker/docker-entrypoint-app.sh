#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Commander 纯应用入口 (依赖外部 Redis + MongoDB)
# ─────────────────────────────────────────────────────────────
set -e
GREEN='\033[0;32m'; NC='\033[0m'
log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $1"; }

cleanup() {
    log "优雅关闭..."
    pm2 stop all 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# 等待 Redis
log "等待 Redis (${REDIS_HOST:-redis}:6379)..."
for i in $(seq 1 30); do
    if python3 -c "
import redis; r=redis.Redis(host='${REDIS_HOST:-redis}',port=6379,
password='${REDIS_PASS:-Lt@114514!}',decode_responses=True)
r.ping(); print('OK')
" 2>/dev/null; then
        log "✅ Redis 就绪"
        break
    fi
    sleep 1
done

# 等待 MongoDB
log "等待 MongoDB (mongo:27017)..."
for i in $(seq 1 30); do
    if python3 -c "
from pymongo import MongoClient
c=MongoClient('${MONGO_URI:-mongodb://mongo:27017/}',serverSelectionTimeoutMS=2000)
c.admin.command('ping'); print('OK')
" 2>/dev/null; then
        log "✅ MongoDB 就绪"
        break
    fi
    sleep 1
done

# 启动 Agent 集群
log "启动 Agent 集群..."
cd /app/.pi/agents/runtime
pm2 start ecosystem.agents.cjs 2>/dev/null || {
    pm2 start agent.sh --name agent-translator -- 翻译官
    pm2 start agent.sh --name agent-business  -- 商务经理
    pm2 start agent.sh --name agent-presales  -- 售前经理
}
pm2 save

# 启动 Dashboard v2
log "启动 Dashboard v2 (端口 3003)..."
pm2 start dashboard_v2.py --name dashboard-v2 --interpreter python3 \
    -- "${MONGO_URI:-mongodb://mongo:27017/}" 3003

log "╔══════════════════════════════════════════╗"
log "║  ⚡ Commander v2.3 纯应用已就绪         ║"
log "║  Dashboard → http://0.0.0.0:3003        ║"
log "╚══════════════════════════════════════════╝"

pm2 logs
wait
