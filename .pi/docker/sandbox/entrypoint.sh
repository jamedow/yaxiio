#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Yaxiio Sandbox entrypoint
# 启动后进入全功能调试环境，保持容器存活
# ═══════════════════════════════════════════════════════════════
set -e

echo "╔══════════════════════════════════════════════╗"
echo "║  🛠️  Yaxiio Sandbox — LightingMetal 全栈环境 ║"
echo "╚══════════════════════════════════════════════╝"
echo ""
echo "  JDK:    $(java -version 2>&1 | head -1)"
echo "  Node:   $(node -v)"
echo "  Python: $(python3.12 --version 2>&1)"
echo "  Maven:  $(mvn -v 2>/dev/null | head -1 | awk '{print $3}')"
echo "  Docker: $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
echo "  PM2:    $(pm2 -v 2>/dev/null)"
echo "  Git:    $(git --version | awk '{print $3}')"
echo ""
echo "  工作区: /workspace"
echo "  代码:   /app/lightingmetal"
echo ""

cd /workspace 2>/dev/null || cd /app

if [ $# -eq 0 ]; then
    # 默认：进入 bash，容器持续存活
    exec bash
else
    exec "$@"
fi
