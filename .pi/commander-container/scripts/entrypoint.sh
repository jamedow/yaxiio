#!/bin/bash
# ── Commander Container Entrypoint ──
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   Commander v2.3 — Multi-Agent System   ║"
echo "║   LightingMetal © 2026  Apache 2.0      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 等待 Redis ──
echo "[entrypoint] 等待 Redis ($REDIS_HOST:$REDIS_PORT)..."
for i in $(seq 1 30); do
    if python3 -c "
import redis
r = redis.Redis(host='$REDIS_HOST', port=$REDIS_PORT, password='$REDIS_PASSWORD', decode_responses=True)
r.ping()
" 2>/dev/null; then
        echo "[entrypoint] ✅ Redis 就绪"
        break
    fi
    sleep 1
done

# ── 创建运行时目录 ──
mkdir -p /app/logs /app/data

# ── 启动模式选择 ──
MODE="${1:-full}"

case "$MODE" in
    full)
        echo "[entrypoint] 🚀 启动全模式 (Commander + Dashboard)"
        # 启动 Commander（后台）
        cd /app/src
        python3 commander_v2.py &
        COMMANDER_PID=$!

        # 启动 Dashboard v2
        cd /app/agents
        if [ -n "$MONGO_URI" ]; then
            python3 dashboard_v2.py "$MONGO_URI" &
        else
            python3 dashboard_v2.py &
        fi
        DASHBOARD_PID=$!

        echo "[entrypoint] Commander PID: $COMMANDER_PID, Dashboard PID: $DASHBOARD_PID"

        # 等待任意进程退出
        wait -n
        ;;

    commander-only)
        echo "[entrypoint] 🎯 仅启动 Commander"
        cd /app/src
        exec python3 commander_v2.py
        ;;

    dashboard-only)
        echo "[entrypoint] 📊 仅启动 Dashboard"
        cd /app/agents
        if [ -n "$MONGO_URI" ]; then
            exec python3 dashboard_v2.py "$MONGO_URI"
        else
            exec python3 dashboard_v2.py
        fi
        ;;

    tui)
        echo "[entrypoint] 🖥️ 启动 Commander TUI"
        cd /app/agents
        exec python3 commander-tui.py
        ;;

    shell)
        echo "[entrypoint] 🐚 进入调试 Shell"
        exec /bin/bash
        ;;

    *)
        echo "[entrypoint] 用法: docker run ... [full|commander-only|dashboard-only|tui|shell]"
        echo "[entrypoint] 默认: full"
        exec /bin/bash
        ;;
esac
