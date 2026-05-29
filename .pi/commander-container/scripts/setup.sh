#!/bin/bash
# ── Commander Setup Script ──
# 初始化 Commander 运行环境
set -e

echo "╔══════════════════════════════════════════╗"
echo "║   Commander v2.3 — 环境初始化            ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 检测 Python ──
PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python3，请先安装"
    exit 1
fi
echo "✅ Python: $($PYTHON --version)"

# ── 检测 Node.js / PM2 ──
if ! command -v node &>/dev/null; then
    echo "⚠️ 未找到 Node.js，PM2 不可用（Agent 进程管理必需）"
    echo "   安装: https://nodejs.org/"
else
    if ! command -v pm2 &>/dev/null; then
        echo "📦 安装 PM2..."
        npm install -g pm2
    fi
    echo "✅ PM2: $(pm2 --version 2>/dev/null || echo 'installed')"
fi

# ── Python 依赖 ──
echo ""
echo "📦 安装 Python 依赖..."
pip install -r requirements.txt

# ── Redis 检查 ──
echo ""
if python3 -c "import redis; r=redis.Redis(host='${REDIS_HOST:-127.0.0.1}',port=${REDIS_PORT:-6379},password='${REDIS_PASSWORD:-commander-secret}'); r.ping()" 2>/dev/null; then
    echo "✅ Redis 连接成功 (${REDIS_HOST:-127.0.0.1}:${REDIS_PORT:-6379})"
else
    echo "⚠️ Redis 未连接。启动本地 Redis："
    echo "   docker run -d --name redis -p 6379:6379 redis:7-alpine redis-server --requirepass commander-secret"
fi

# ── 编译检查 ──
echo ""
echo "🔍 编译检查所有模块..."
cd src
for f in *.py; do
    if python3 -m py_compile "$f" 2>/dev/null; then
        echo "  ✅ $f"
    else
        echo "  ❌ $f 编译失败"
    fi
done
cd ..

# ── 创建目录 ──
mkdir -p logs data

# ── 注册本地 Skills ──
echo ""
echo "📦 注册本地 Skills 到 Redis..."
python3 -c "
import sys, redis
sys.path.insert(0, 'src')
from skill_manager import SkillManager, LocalSkillAdapter
try:
    r = redis.Redis(host='${REDIS_HOST:-127.0.0.1}', port=${REDIS_PORT:-6379}, password='${REDIS_PASSWORD:-commander-secret}', decode_responses=True)
    r.ping()
    sm = SkillManager(r)
    adapter = LocalSkillAdapter(sm, '../.pi/skills')
    count = len(adapter.bootstrap_local_skills())
    print(f'✅ 已注册 {count} 个 Skill')
except Exception as e:
    print(f'⚠️ Skill 注册跳过（Redis不可用）: {e}')
"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  ✅ Commander v2.3 环境初始化完成         ║"
echo "║                                          ║"
echo "║  启动 Commander:  make dev-start         ║"
echo "║  启动 Dashboard:  make dev-dashboard     ║"
echo "║  Docker 部署:     make start             ║"
echo "╚══════════════════════════════════════════╝"
