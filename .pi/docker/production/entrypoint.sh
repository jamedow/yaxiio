#!/bin/bash
set -e
REDIS_PASS="${REDIS_PASSWORD:-Yaxiio2026}"
COMMANDER_DIR="/opt/yaxiio/.pi/skills/commander"

echo "╔══════════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio · 生产模式                          ║"
echo "║  PM2 → Guard → Commander + Gateway              ║"
echo "╚══════════════════════════════════════════════════╝"

echo "[Yaxiio] 启动 Redis..."
redis-server --daemonize yes --bind 127.0.0.1 --dir /data \
  --requirepass "$REDIS_PASS" --maxmemory 512mb --maxmemory-policy allkeys-lru
for i in $(seq 1 30); do
  if redis-cli -a "$REDIS_PASS" ping 2>/dev/null | grep -q PONG; then
    echo "[Yaxiio] Redis OK"; break
  fi
  sleep 0.5
done

export REDIS_HOST="127.0.0.1" REDIS_PASSWORD="$REDIS_PASS" PYTHONUNBUFFERED=1
export YAXIO_HOME="/opt/yaxiio/.pi/skills/commander"
export COMMANDER_SCRIPT="$YAXIO_HOME/yaxiio.py"

mkdir -p /data/db /data/log /opt/commander/logs
rm -rf /opt/commander
ln -sf /opt/yaxiio/.pi/skills/commander /opt/commander
ln -sf /opt/yaxiio/modules/shared /opt/commander/modules/shared 2>/dev/null
ln -sf /opt/yaxiio/modules/layer1 /opt/commander/modules/layer1 2>/dev/null
ln -sf /opt/yaxiio/modules/layer2 /opt/commander/modules/layer2 2>/dev/null
ln -sf /opt/yaxiio/modules/layer3 /opt/commander/modules/layer3 2>/dev/null
ln -sf /opt/yaxiio/modules/layer4 /opt/commander/modules/layer4 2>/dev/null
ln -sf /opt/yaxiio/modules/layer5 /opt/commander/modules/layer5 2>/dev/null
mkdir -p /opt/commander/data

cd "$COMMANDER_DIR"
pm2 start pi_guardian_v3.py --name yaxiio-guardian --interpreter python3.12 --max-restarts 5 --restart-delay 3000
echo "[Yaxiio] Guard 已启动"
sleep 5

echo "[Yaxiio] 启动 Gateway (WS:3398 HTTP:3399)..."
nohup python3.12 gateway.py --ws-port 3398 --http-port 3399 --redis-host 127.0.0.1 --redis-password "$REDIS_PASS" --llm-api-key "${DEEPSEEK_API_KEY:-}" > /data/log/gateway.log 2>&1 &
sleep 3

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio 生产就绪                            ║"
echo "║  Redis     : 127.0.0.1:6379                      ║"
echo "║  Gateway   : ws://0.0.0.0:3398 / :3399          ║"
echo "╚══════════════════════════════════════════════════╝"

exec sleep infinity
