#!/bin/bash
set -e
# ═══════════════════════════════════════════════════════════════
# 雅溪 Yaxiio — 一行安装脚本
# curl -sL https://yaxiio.io/install.sh | bash
# ═══════════════════════════════════════════════════════════════

YAXIIO_VERSION="${YAXIIO_VERSION:-latest}"
YAXIIO_IMAGE="${YAXIIO_IMAGE:-yaxiio/yaxiio:${YAXIIO_VERSION}}"
YAXIIO_PORT="${YAXIIO_PORT:-3399}"
YAXIIO_WS_PORT="${YAXIIO_WS_PORT:-3398}"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
banner() { echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
           echo -e "${CYAN}║  雅溪 Yaxiio · Agent 操作系统内核            ║${NC}"
           echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"; }

banner

# ── 1. 检查 Docker ──
if ! command -v docker &>/dev/null; then
    echo -e "${RED}❌ 需要 Docker。请先安装: https://docs.docker.com/get-docker/${NC}"
    exit 1
fi
echo -e "${GREEN}✅ Docker 已安装${NC}"

# ── 2. 拉取镜像（或本地构建） ──
echo "📦 准备 Yaxiio 镜像..."
if docker pull "$YAXIIO_IMAGE" 2>/dev/null; then
    echo -e "${GREEN}✅ 镜像已拉取: $YAXIIO_IMAGE${NC}"
else
    echo "⚠️  远程镜像不可用，使用本地构建..."
    if [ -f "Dockerfile" ]; then
        docker build -t yaxiio:local .
        YAXIIO_IMAGE="yaxiio:local"
        echo -e "${GREEN}✅ 本地构建完成${NC}"
    else
        echo -e "${RED}❌ 无法获取镜像。请指定 YAXIIO_IMAGE 环境变量${NC}"
        exit 1
    fi
fi

# ── 3. 检查是否已有运行实例 ──
if docker ps --filter name=yaxiio --format "{{.Names}}" | grep -q yaxiio; then
    echo -e "${GREEN}✅ Yaxiio 已在运行${NC}"
    HEALTH=$(curl -s "http://localhost:${YAXIIO_PORT}/health" 2>/dev/null || echo "")
    echo "   Gateway: $HEALTH"
    exit 0
fi

# ── 4. 启动 Yaxiio ──
echo "🚀 启动 Yaxiio..."
docker rm -f yaxiio 2>/dev/null || true

docker run -d --name yaxiio \
    -p "${YAXIIO_WS_PORT}:3398" \
    -p "${YAXIIO_PORT}:3399" \
    -v yaxiio-data:/data \
    -e "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}" \
    --restart unless-stopped \
    "$YAXIIO_IMAGE"

# ── 5. 等待就绪 ──
echo "⏳ 等待服务就绪..."
for i in $(seq 1 30); do
    HEALTH=$(curl -s "http://localhost:${YAXIIO_PORT}/health" 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"ok"'; then
        echo -e "${GREEN}✅ Yaxiio 已就绪: $HEALTH${NC}"
        break
    fi
    sleep 1
done

# ── 6. 输出信息 ──
echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Yaxiio v${YAXIIO_VERSION} 已启动               ║${NC}"
echo -e "${CYAN}╠══════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║  HTTP API:  http://localhost:${YAXIIO_PORT}         ║${NC}"
echo -e "${CYAN}║  WebSocket: ws://localhost:${YAXIIO_WS_PORT}        ║${NC}"
echo -e "${CYAN}║  健康检查:  curl localhost:${YAXIIO_PORT}/health    ║${NC}"
echo -e "${CYAN}║  查看日志:  docker logs -f yaxiio             ║${NC}"
echo -e "${CYAN}║  进入容器:  docker exec -it yaxiio bash       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
