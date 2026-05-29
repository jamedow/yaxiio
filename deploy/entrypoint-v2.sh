#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 雅溪 Yaxiio v2.0 — 两层守护架构
# ═══════════════════════════════════════════════════════════════
# 守护层级:
#   PM2 (最外层) ──→ Commander Guard (AI修复层) ──→ Commander
#
# PM2 职责: 仅守护 Commander Guard 进程，崩溃时自动重启（最多5次）
# Guard 职责: 健康检查 → 故障诊断 → 自动修复 → 重启 Commander
#
# 启动顺序: Redis → MongoDB → PM2(Guard) → Guard自启Commander → Dashboard
# ═══════════════════════════════════════════════════════════════

REDIS_PASS="${REDIS_PASSWORD:-}"
# ⚠️ 请设置环境变量 REDIS_PASSWORD
VER="${YAXIO_VERSION:-2.0.0}"
COMMANDER_DIR="/app/.pi/skills/commander"
GUARD_SCRIPT="/app/.pi/skills/commander/pi_guardian_v3.py"
DASHBOARD_SCRIPT="/app/.pi/agents/runtime/dashboard_v2.py"

# ── 清理函数 ──
cleanup() {
  echo "[Yaxiio] 收到终止信号，执行优雅关闭..."
  pm2 delete all 2>/dev/null
  mongod --shutdown --dbpath /data/db 2>/dev/null
  redis-cli -a "$REDIS_PASS" shutdown 2>/dev/null
  exit 0
}
trap cleanup SIGTERM SIGINT

# ═══════════════════════════════════════════════════════════
# 启动横幅
# ═══════════════════════════════════════════════════════════
echo "╔══════════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio v${VER} · 2026 AI元年               ║"
echo "║  架构: PM2 → Commander Guard → Commander         ║"
echo "╚══════════════════════════════════════════════════╝"

# ═══════════════════════════════════════════════════════════
# L1: 基础设施 (Redis + MongoDB)
# ═══════════════════════════════════════════════════════════
echo "[Yaxiio] 启动基础设施..."

# Redis
redis-server --daemonize yes --bind 127.0.0.1 --dir /data \
  --requirepass "$REDIS_PASS" --maxmemory 256mb

echo -n "[Yaxiio] 等待 Redis..."
for i in $(seq 1 30); do
  if redis-cli -a "$REDIS_PASS" ping 2>/dev/null | grep -q PONG; then
    echo " OK"
    break
  fi
  sleep 0.5
done

# MongoDB
mkdir -p /data/db /data/log
mongod --fork --logpath /data/log/mongod.log --dbpath /data/db --bind_ip 127.0.0.1
sleep 2
echo "[Yaxiio] MongoDB: OK"

# ═══════════════════════════════════════════════════════════
# 环境变量
# ═══════════════════════════════════════════════════════════
export ENV_REDIS_HOST="127.0.0.1"
export ENV_REDIS_PASSWORD="$REDIS_PASS"
export MONGO_URI="mongodb://127.0.0.1:27017/"
export HEALTH_PORT="3003"
export GUARD_LOG_DIR="/opt/commander"

# Guard 日志目录
mkdir -p /opt/commander

# Commander 备份
if [ -f "$COMMANDER_DIR/commander_v2.py" ]; then
  cp "$COMMANDER_DIR/commander_v2.py" /tmp/commander_v2.py.bak
fi

# ═══════════════════════════════════════════════════════════
# L2: PM2 启动 Commander Guard (仅守护 Guard)
# ═══════════════════════════════════════════════════════════
echo "[Yaxiio] PM2 启动 Commander Guard..."

cd "$COMMANDER_DIR"

# PM2 只管理 Guard 进程
# max_restarts=5: Guard 连续崩溃5次后停止重启
# restart_delay=3000: 崩溃后3秒重启
pm2 start "$GUARD_SCRIPT" \
  --name "yaxiio-guardian" \
  --interpreter python3 \
  --max-restarts 5 \
  --restart-delay 3000

echo "[Yaxiio] Commander Guard: 已由 PM2 接管"

# ═══════════════════════════════════════════════════════════
# 等待 Guard 启动 Commander
# ═══════════════════════════════════════════════════════════
sleep 3
echo "[Yaxiio] Guard 正在启动 Commander..."

# ═══════════════════════════════════════════════════════════
# Dashboard (独立于守护链)
# ═══════════════════════════════════════════════════════════
python3 "$DASHBOARD_SCRIPT" "mongodb://127.0.0.1:27017/" 3003 &
echo "[Yaxiio] Dashboard: http://0.0.0.0:3003"

# ═══════════════════════════════════════════════════════════
# 就绪
# ═══════════════════════════════════════════════════════════
echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio v${VER} 已苏醒                       ║"
echo "║  PM2 → Guard → Commander 守护链就绪              ║"
echo "╚══════════════════════════════════════════════════╝"

# 保持容器运行，等待任意子进程退出
wait -n
