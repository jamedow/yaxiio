#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Yaxiio Sandbox 启动脚本
# 用法:
#   ./start-sandbox.sh              # 默认启动
#   ./start-sandbox.sh my-session   # 指定会话名
#   ./start-sandbox.sh --rm         # 用完即删
# ═══════════════════════════════════════════════════════════════
set -e

IMAGE="yaxiio-sandbox:lightingmetal"
NAME="${1:-sandbox-default}"
AUTO_REMOVE=""

if [ "$1" = "--rm" ]; then
    AUTO_REMOVE="--rm"
    NAME="${2:-sandbox-temp}"
fi

docker run -d --name "$NAME" $AUTO_REMOVE \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /opt/lightingMetal:/app/lightingmetal \
    -v sandbox-workspace-${NAME}:/workspace \
    --memory="4g" \
    --cpus="2" \
    "$IMAGE" \
    sleep infinity

echo "✅ Sandbox '$NAME' 已启动"
echo ""
echo "   进入: docker exec -it $NAME bash"
echo "   停止: docker stop $NAME"
echo "   删除: docker rm -f $NAME"
