#!/bin/bash
# lightingmetal.com 部署脚本
# 用法: bash deploy.sh [build_dir]
# 示例: bash deploy.sh /app/lightingmetal/customer-portal

set -e
BUILD_DIR="${1:-/app/lightingmetal/customer-portal}"
TS=$(date +%Y%m%d-%H%M%S)
PKG="lightingmetal-${TS}.tar.gz"
HK_HOST="47.79.20.2"
HK_PASS="Zhangliang@520"
MAINLAND_HTTP="http://106.14.210.31:3004"

echo "=== 1. 构建 ==="
cd "$BUILD_DIR"
rm -rf .nuxt .output
npx nuxi build
tar -czf "/tmp/$PKG" .output i18n
echo "构建完成: /tmp/$PKG ($(du -h /tmp/$PKG | cut -f1))"

echo "=== 2. 启动 HTTP 传输 ==="
docker exec yaxiio bash -c '
  pm2 stop agent-board 2>/dev/null || true
  cd /tmp && nohup python3 -m http.server 3004 > /dev/null 2>&1 &
'
sleep 1

echo "=== 3. HK 下载 ==="
sshpass -p "$HK_PASS" ssh -o StrictHostKeyChecking=no root@$HK_HOST "
  curl -s -o /tmp/$PKG $MAINLAND_HTTP/$PKG
  ls -la /tmp/$PKG
"

echo "=== 4. 部署 ==="
sshpass -p "$HK_PASS" ssh -o StrictHostKeyChecking=no root@$HK_HOST "
  cd /opt/lightingMetal/customer-portal
  rm -rf .output
  tar -xzf /tmp/$PKG
  docker restart nuxt-app
"

echo "=== 5. 恢复板子 ==="
docker exec yaxiio bash -c '
  pkill -f "http.server 3004" 2>/dev/null || true
  pm2 start agent-board 2>/dev/null || true
'

echo "=== 6. 验证 ==="
sleep 8
HTTP=$(curl -sk -o /dev/null -w "%{http_code}" "https://www.lightingmetal.com/en")
echo "部署完成. HTTP $HTTP"
