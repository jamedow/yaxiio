#!/bin/bash
# Agent v2 — 支持扁平化P2P通信
NAME="${1:-unknown}"
CHANNEL="lightingmetal:agent:${NAME}"
CONTROL="lightingmetal:agent:commander"
COUNT=0

# ── Redis 连接配置（从环境变量读取，不再硬编码 Docker 容器名）──
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_PASSWORD="${REDIS_PASSWORD:?REDIS_PASSWORD 环境变量未设置}"

REDIS_CLI="redis-cli -h ${REDIS_HOST} -p ${REDIS_PORT} -a ${REDIS_PASSWORD} --no-auth-warning"

log() { echo "[${NAME}] $1"; }
now() { date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S; }

# 发布消息到指定频道
publish() {
  local target="$1" payload="$2"
  ${REDIS_CLI} PUBLISH "lightingmetal:agent:${target}" "${payload}" 2>/dev/null > /dev/null
}

# 回复消息给发送者
reply() {
  local msg="$1" status="${2:-done}" note="${3:-}"
  local from=$(echo "$msg" | python3 -c "import json,sys;print(json.load(sys.stdin).get('from',''))" 2>/dev/null)
  local tid=$(echo "$msg" | python3 -c "import json,sys;print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null)
  local reply_to=$(echo "$msg" | python3 -c "import json,sys;print(json.load(sys.stdin).get('replyTo','commander'))" 2>/dev/null)
  
  local resp="{\"from\":\"${NAME}\",\"to\":\"${from}\",\"type\":\"response\",\"taskId\":\"${tid}\",\"timestamp\":\"$(now)\",\"payload\":{\"status\":\"${status}\",\"note\":\"${note}\"}}"
  publish "${reply_to}" "${resp}"
}

# 转发任务给其他Agent（P2P）
forward() {
  local msg="$1" target_agent="$2"
  local tid=$(echo "$msg" | python3 -c "import json,sys;print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null)
  local payload=$(echo "$msg" | python3 -c "import json,sys;d=json.load(sys.stdin);print(json.dumps(d.get('payload',{})))" 2>/dev/null)
  
  local fwd="{\"from\":\"${NAME}\",\"to\":\"${target_agent}\",\"type\":\"task\",\"taskId\":\"${tid}-fwd\",\"timestamp\":\"$(now)\",\"replyTo\":\"${NAME}\",\"payload\":${payload}}"
  publish "${target_agent}" "${fwd}"
  log "↗️ 转发任务 ${tid} → ${target_agent}"
}

# 请求其他Agent协助（P2P request）
request_help() {
  local target_agent="$1" action="$2" data="$3"
  local tid="help-$(date +%s)"
  local req="{\"from\":\"${NAME}\",\"to\":\"${target_agent}\",\"type\":\"request\",\"taskId\":\"${tid}\",\"timestamp\":\"$(now)\",\"replyTo\":\"${NAME}\",\"payload\":{\"action\":\"${action}\",\"data\":${data}}}"
  publish "${target_agent}" "${req}"
  log "🤝 请求协助: → ${target_agent} (${action})"
  echo "${tid}"
}

log "启动 v0.2.6 (P2P), 频道: ${CHANNEL}"

# 订阅自己的频道 + Commander控制频道
${REDIS_CLI} SUBSCRIBE "${CHANNEL}" "${CONTROL}" 2>/dev/null | while read -r line; do
  read -r type
  read -r channel
  read -r payload

  if [ "$type" != "message" ]; then
    continue
  fi

  # 解析消息
  to=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('to',''))" 2>/dev/null)
  msg_type=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('type',''))" 2>/dev/null)
  task_id=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('taskId',''))" 2>/dev/null)
  msg_from=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('from',''))" 2>/dev/null)

  # 过滤：只处理发给自己的消息
  if [ "$to" != "$NAME" ] && [ "$to" != "*" ] && [ "$to" != "all" ]; then
    continue
  fi

  case "$msg_type" in
    shutdown)
      log "收到关闭指令 (来自 ${msg_from})"
      break
      ;;

    heartbeat_check)
      publish "commander" "{\"from\":\"${NAME}\",\"type\":\"heartbeat\",\"payload\":{\"status\":\"alive\",\"tasks\":${COUNT},\"uptime\":\"$(ps -o etimes= -p $$ | tr -d ' ')\"}}"
      ;;

    request)
      # P2P请求：其他Agent直接请求协助
      COUNT=$((COUNT + 1))
      action=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('payload',{}).get('action',''))" 2>/dev/null)
      log "🤝 P2P请求 #${COUNT}: ${task_id} ← ${msg_from} (${action})"
      reply "$payload" "done" "P2P请求已处理"
      ;;

    task)
      # 标准任务
      COUNT=$((COUNT + 1))
      action=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('payload',{}).get('action',''))" 2>/dev/null)
      log "📋 任务 #${COUNT}: ${task_id} ← ${msg_from} (${action})"
      reply "$payload" "done" "任务完成"
      ;;

    response)
      # 收到其他Agent的回复
      status=$(echo "$payload" | python3 -c "import json,sys;print(json.load(sys.stdin).get('payload',{}).get('status',''))" 2>/dev/null)
      log "📬 回复: ${task_id} ← ${msg_from} [${status}]"
      ;;

    *)
      log "未知消息类型: ${msg_type}"
      ;;
  esac
done

log "下线, 处理了${COUNT}个任务"
