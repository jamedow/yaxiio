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

export REDIS_HOST="127.0.0.1"
export REDIS_PASSWORD="$REDIS_PASS"
export YAXIO_HOME="/opt/yaxiio/.pi/skills/commander"
export PYTHONUNBUFFERED=1
export YAXIO_HOME="/opt/yaxiio/.pi/skills/commander"
mkdir -p /data/db /data/log; rm -rf /opt/commander
ln -sf /opt/yaxiio/.pi/skills/commander /opt/commander; mkdir -p /opt/commander/logs
ln -sf /opt/yaxiio/modules/layer5 /opt/commander/modules/layer5
ln -sf /opt/yaxiio/modules/layer4 /opt/commander/modules/layer4
ln -sf /opt/yaxiio/modules/layer3 /opt/commander/modules/layer3
ln -sf /opt/yaxiio/modules/layer2 /opt/commander/modules/layer2
ln -sf /opt/yaxiio/modules/layer1 /opt/commander/modules/layer1
ln -sf /opt/yaxiio/modules/shared /opt/commander/modules/shared

cd "$COMMANDER_DIR"
export COMMANDER_SCRIPT="/opt/yaxiio/.pi/skills/commander/yaxiio.py"

pm2 start pi_guardian_v3.py \
  --name "yaxiio-guardian" \
  --interpreter python3.12 \
  --max-restarts 5 \
  --restart-delay 3000
echo "[Yaxiio] Guard 已启动"
sleep 5

# Gateway
echo "[Yaxiio] 启动 Gateway (WS:3398 HTTP:3399)..."
nohup python3.12 gateway.py \
  --ws-port 3398 --http-port 3399 \
  --redis-host 127.0.0.1 --redis-password "$REDIS_PASS" \
  > /data/log/gateway.log 2>&1 &
sleep 2

# Dashboard
if [ -f dashboard_v2.py ]; then
  nohup python3.12 dashboard_v2.py --port 3003 > /data/log/dashboard.log 2>&1 &
  echo "[Yaxiio] Dashboard: http://0.0.0.0:3003"
fi

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio 生产就绪                            ║"
echo "║  Redis     : 127.0.0.1:6379                      ║"
echo "║  Gateway   : ws://0.0.0.0:3398 / :3399          ║"
echo "║  Dashboard : http://0.0.0.0:3003                 ║"
echo "╚══════════════════════════════════════════════════╝"

# 保持容器存活
exec sleep infinity
