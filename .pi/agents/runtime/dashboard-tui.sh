#!/bin/bash
# Agent 指挥中心 — 终端仪表盘
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:?REDIS_PASSWORD not set}"
REDIS_CLI="redis-cli -h ${REDIS_HOST} -p ${REDIS_PORT} -a ${REDIS_PASSWORD} --no-auth-warning"
REDIS_AUTH="${REDIS_CLI}"

draw() {
  clear
  echo "╔══════════════════════════════════════════════════════╗"
  echo "║     ⚡ LightingMetal Agent 指挥中心                  ║"
  echo "╠══════════════════════════════════════════════════════╣"
  
  # Agent状态
  echo "║  🤖 Agent 集群                                       ║"
  for agent in "翻译官" "商务经理" "售前经理"; do
    sub=$($REDIS_AUTH PUBLISH "lightingmetal:agent:${agent}" '{"type":"heartbeat_check","to":"'"${agent}"'"}' 2>/dev/null)
    if [ "$sub" -gt 0 ] 2>/dev/null; then
      printf "║    ● %-12s  在线  订阅:%s                             ║\n" "$agent" "$sub"
    else
      printf "║    ○ %-12s  离线                                     ║\n" "$agent"
    fi
  done
  
  # 任务面板
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  📊 进度                                             ║"
  echo "║  ┌────────────────────────────────────────────────┐  ║"
  echo "║  │ ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  等待任务...    │  ║"
  echo "║  └────────────────────────────────────────────────┘  ║"
  
  # PM2进程
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  📡 PM2 进程                                         ║"
  pm2 list 2>/dev/null | grep agent- | while read line; do
    name=$(echo "$line" | awk '{print $4}')
    status=$(echo "$line" | awk '{print $10}')
    mem=$(echo "$line" | awk '{print $14}')
    printf "║    %-20s %-8s %s                              ║\n" "$name" "$status" "$mem"
  done
  
  echo "╠══════════════════════════════════════════════════════╣"
  echo "║  ⌨️  命令: s)启动Agent  d)销毁  t)测试任务  q)退出   ║"
  echo "╚══════════════════════════════════════════════════════╝"
}

while true; do
  draw
  read -t 3 -n 1 key
  case "$key" in
    s) 
      pm2 start /app/.pi/agents/runtime/agent.sh --name agent-translator -- 翻译官 2>/dev/null
      pm2 start /app/.pi/agents/runtime/agent.sh --name agent-business -- 商务经理 2>/dev/null
      pm2 start /app/.pi/agents/runtime/agent.sh --name agent-presales -- 售前经理 2>/dev/null
      echo "✅ Agent已启动"
      sleep 2
      ;;
    d)
      pm2 delete agent-translator agent-business agent-presales 2>/dev/null
      echo "🛑 Agent已销毁"
      sleep 1
      ;;
    t)
      for agent in 翻译官 商务经理 售前经理; do
        $REDIS_AUTH PUBLISH "lightingmetal:agent:${agent}" "{\"from\":\"commander\",\"to\":\"${agent}\",\"type\":\"task\",\"taskId\":\"test-$(date +%s)\"}" 2>/dev/null >/dev/null
      done
      echo "🚀 并行分派3个测试任务"
      sleep 1
      ;;
    q) 
      echo "退出"
      break
      ;;
  esac
done
