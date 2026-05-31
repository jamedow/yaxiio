#!/bin/bash
set -e
echo "╔══════════════════════════════════════════════╗"
echo "║  雅溪 Yaxiio — 工作环境恢复                   ║"
echo "╚══════════════════════════════════════════════╝"

export DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
# ⚠️ 请设置环境变量 DEEPSEEK_API_KEY，不要在此文件中硬编码

echo ""
echo "▸ 检查 Yaxiio 容器..."
if docker ps --filter name=yaxiio --format "{{.Names}}" | grep -q yaxiio; then
    echo "  ✅ yaxiio 容器运行中"
else
    echo "  ⚠️ 启动 yaxiio 容器..."
    docker start yaxiio 2>/dev/null || {
        docker run -d --name yaxiio \
          -v /var/run/docker.sock:/var/run/docker.sock \
          -v /opt/yaxiio:/opt/yaxiio \
          -v /opt/lightingMetal:/opt/lightingMetal \
          -v yaxiio-data:/data \
          -p 3398:3398 -p 3399:3399 \
          --restart unless-stopped \
          yaxiio:prod
        sleep 10
    }
fi

echo "▸ 验证服务..."
for i in 1 2 3 4 5; do
    HEALTH=$(docker exec yaxiio curl -s http://127.0.0.1:3399/health 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status"'; then
        echo "  ✅ Gateway 就绪: $HEALTH"
        break
    fi
    sleep 2
done

echo ""
echo "▸ 状态总览:"
docker exec yaxiio bash -c "echo \"  Commander: \$(ps aux | grep yaxiio.py | grep -v grep | wc -l)\"; echo \"  Gateway:   \$(ps aux | grep gateway | grep -v grep | wc -l)\"; echo \"  Guard:     \$(pm2 status 2>/dev/null | grep online | wc -l)\"; echo \"  Redis:     \$(redis-cli -a ${REDIS_PASSWORD:-Yaxiio2026} ping 2>/dev/null)\"; echo \"  HTTP API:  \$(curl -s http://127.0.0.1:3399/health 2>/dev/null)\""

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║  快速命令 (添加到 ~/.bashrc)                  ║"
echo "╠══════════════════════════════════════════════╣"
echo "║  alias yx='docker exec -it yaxiio bash'       ║"
echo "║  alias yx-restart='pm2 restart yaxiio-guardian' ║"
echo "║  alias yx-logs='pm2 logs yaxiio-guardian'    ║"
echo "╚══════════════════════════════════════════════╝"
echo ""