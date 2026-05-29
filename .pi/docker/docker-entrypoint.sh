#!/bin/bash
# ─────────────────────────────────────────────────────────────
# LightingMetal Commander 容器入口
# 启动顺序: Redis → MongoDB → PM2(Agent集群) → Dashboard
# ─────────────────────────────────────────────────────────────
set -e

REDIS_PASS="Lt@114514!"
MONGO_DB="lightingmetal"

# ── 颜色 ──
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $1"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARN${NC} $1"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR${NC} $1"; }

# ── 信号处理 ──
cleanup() {
    log "收到终止信号，优雅关闭..."
    pm2 stop all 2>/dev/null || true
    mongod --shutdown --dbpath /data/db 2>/dev/null || true
    redis-cli -a "$REDIS_PASS" shutdown 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT SIGQUIT

# ── 1. Redis ──
start_redis() {
    log "启动 Redis..."
    redis-server /etc/redis/redis.conf --daemonize yes
    # 等待就绪
    for i in $(seq 1 20); do
        if redis-cli -a "$REDIS_PASS" ping 2>/dev/null | grep -q PONG; then
            log "✅ Redis 就绪"
            return 0
        fi
        sleep 0.5
    done
    err "Redis 启动失败"
    return 1
}

# ── 2. MongoDB ──
start_mongo() {
    log "启动 MongoDB..."
    mongod --fork --logpath /data/log/mongod.log --dbpath /data/db --bind_ip 0.0.0.0
    for i in $(seq 1 30); do
        if mongosh --quiet --eval 'db.runCommand("ping")' 2>/dev/null | grep -q '"ok" : 1'; then
            log "✅ MongoDB 就绪"
            # 初始化数据库（如果不存在）
            mongosh --quiet --eval "
                use $MONGO_DB;
                db.createCollection('agent_optimization_log');
                db.createCollection('degraded_tasks');
                db.createCollection('token_usage');
                db.createCollection('routing_decisions');
                db.createCollection('agent_failures');
                db.createCollection('agent_failovers');
            " 2>/dev/null || true
            return 0
        fi
        sleep 1
    done
    err "MongoDB 启动失败"
    return 1
}

# ── 3. Agent 集群 (PM2) ──
start_agents() {
    log "启动 Agent 集群 (PM2)..."

    # 使用 ecosystem 配置（如果存在且 Agent 脚本可用）
    if [ -f /app/.pi/agents/runtime/ecosystem.agents.cjs ]; then
        cd /app/.pi/agents/runtime
        pm2 start ecosystem.agents.cjs
    else
        # 单独启动 Commander + 核心 Agent
        pm2 start /app/.pi/agents/runtime/agent.sh \
            --name agent-translator -- 翻译官
        pm2 start /app/.pi/agents/runtime/agent.sh \
            --name agent-business  -- 商务经理
        pm2 start /app/.pi/agents/runtime/agent.sh \
            --name agent-presales  -- 售前经理
    fi

    pm2 save
    log "✅ Agent 集群已启动 ($(pm2 jlist 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d))' 2>/dev/null || echo '?') 个进程)"
}

# ── 4. Dashboard ──
start_dashboard() {
    log "启动 Dashboard v2 (端口 3003)..."
    pm2 start /app/.pi/agents/runtime/dashboard_v2.py \
        --name dashboard-v2 \
        --interpreter python3 \
        -- "mongodb://127.0.0.1:27017/" 3003
    log "✅ Dashboard v2 → http://0.0.0.0:3003/dashboard"
}

# ── 显示状态 ──
show_status() {
    echo ""
    log "╔══════════════════════════════════════════════════════╗"
    log "║   ⚡ LightingMetal Commander v2.3 已就绪            ║"
    log "╠══════════════════════════════════════════════════════╣"
    log "║  Redis    → 127.0.0.1:6379  (密码: Lt@114514!)     ║"
    log "║  MongoDB  → 127.0.0.1:27017 (db: lightingmetal)    ║"
    log "║  Dashboard v2 → http://0.0.0.0:3003/dashboard      ║"
    log "║  六大引擎: 去重+A/B+降级+伸缩+双通道+LLM路由        ║"
    log "╚══════════════════════════════════════════════════════╝"
    echo ""
}

# ── 主流程 ──
MODE="${1:-all}"

case "$MODE" in
    redis)
        start_redis
        show_status
        tail -f /var/log/redis/redis-server.log
        ;;
    mongo)
        start_mongo
        show_status
        tail -f /data/log/mongod.log
        ;;
    agents)
        start_agents
        show_status
        pm2 logs
        ;;
    dashboard)
        start_dashboard
        show_status
        pm2 logs dashboard-v2
        ;;
    all|*)
        start_redis   || exit 1
        start_mongo   || exit 1
        start_agents  || true  # Agent 失败不阻断
        start_dashboard || true
        show_status
        # 保持前台，打印 PM2 日志
        pm2 logs
        ;;
esac

wait
