# Copyright 2026 LightingMetal
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


#!/bin/bash
# MemoryManager — 调度中心外挂记忆体 (Redis-backed)
# 核心能力: 写入记忆 / 检索记忆 / 相似搜索 / 上下文注入

REDIS="docker exec redis-centos7 redis-cli -a "Lt@114514!""
NOW() { date -Iseconds 2>/dev/null || date +%Y-%m-%dT%H:%M:%S; }

# ==========================================
# 写入记忆
# ==========================================
save() {
  local task_id="$1" key="$2" value="$3"
  local ts=$(date +%s)
  
  # Hash: 任务详情
  $REDIS HSET "agent:memory:${task_id}" "${key}" "${value}" > /dev/null
  $REDIS HSET "agent:memory:${task_id}" "_updated" "$(NOW)" > /dev/null
  
  # Sorted Set: 时间索引
  $REDIS ZADD "agent:memory:index" "${ts}" "${task_id}" > /dev/null
  
  # 关键词索引 (从value中提取)
  local keywords=$(echo "${key}:${value}" | tr '[:upper:]' '[:lower:]' | grep -oE '俄语|翻译|审计|报价|询价|俄|ru|ar|es|power|solar|mining|螺栓|紧固件|地面|光伏' | tr '\n' ',' | sed 's/,$//')
  for kw in $(echo "$keywords" | tr ',' ' '); do
    $REDIS SADD "agent:memory:kw:${kw}" "${task_id}" > /dev/null
  done
}

# ==========================================
# 进度快照 (多个key一次性写入)
# ==========================================
snapshot() {
  local task_id="$1" shift
  for pair in "$@"; do
    local k="${pair%%=*}"
    local v="${pair#*=}"
    save "$task_id" "$k" "$v"
  done
}

# ==========================================
# 检索记忆
# ==========================================
recall() {
  local task_id="$1"
  echo "📋 任务记忆: ${task_id}"
  $REDIS HGETALL "agent:memory:${task_id}" 2>/dev/null | while IFS= read -r line; do
    # Redis HGETALL 输出: key\nvalue\nkey\nvalue...
    read -r val
    printf "  %-25s %s\n" "$line" "${val:0:100}"
  done
}

# ==========================================
# 压缩上下文 (检索+压缩为Agent可用的精简摘要)
# ==========================================
compress() {
  local task_id="$1"
  local max_fields="${2:-20}"
  
  # 获取关键字段
  local fields=$($REDIS HGETALL "agent:memory:${task_id}" 2>/dev/null | head -$((max_fields * 2)))
  
  # 压缩为注入格式
  echo "--- 历史记忆: ${task_id} ---"
  local is_key=1
  local current_key=""
  echo "$fields" | while IFS= read -r line; do
    if [ $is_key -eq 1 ]; then
      current_key="$line"
      is_key=0
    else
      # 只保留重要字段
      case "$current_key" in
        customer|需求|产品|报价|结果|结论|status|result|summary|_updated)
          echo "  ${current_key}: ${line:0:200}"
          ;;
      esac
      is_key=1
    fi
  done
}

# ==========================================
# 相似搜索 (关键词匹配历史任务)
# ==========================================
search() {
  local query="$1"
  local limit="${2:-5}"
  
  echo "🔍 搜索: ${query}"
  
  # 提取查询关键词
  local keywords=$(echo "$query" | tr '[:upper:]' '[:lower:]' | grep -oE '俄语|翻译|审计|报价|询价|俄|ru|ar|es|power|solar|mining|螺栓|紧固件|地面|光伏' | sort -u)
  
  local found=0
  for kw in $keywords; do
    local tasks=$($REDIS SMEMBERS "agent:memory:kw:${kw}" 2>/dev/null)
    for tid in $tasks; do
      [ $found -ge $limit ] && break 2
      local summary=$($REDIS HGET "agent:memory:${tid}" "summary" 2>/dev/null)
      local status=$($REDIS HGET "agent:memory:${tid}" "status" 2>/dev/null)
      printf "  [%s] %s — %s\n" "$tid" "${summary:0:80}" "${status}"
      found=$((found + 1))
    done
  done
  
  [ $found -eq 0 ] && echo "  (无相似历史任务)"
}

# ==========================================
# 上下文注入 (生成新Agent的初始上下文)
# ==========================================
inject() {
  local task_id="$1"
  local agent_role="${2:-翻译官}"
  
  echo "💉 上下文注入: ${task_id} → ${agent_role}"
  
  # 1. 当前任务记忆
  local current=$($REDIS HGETALL "agent:memory:${task_id}" 2>/dev/null)
  
  # 2. 相似历史任务 (最多3个)
  local query=$($REDIS HGET "agent:memory:${task_id}" "需求" 2>/dev/null)
  query="${query:-$($REDIS HGET "agent:memory:${task_id}" "task" 2>/dev/null)}"
  
  echo ""
  echo "=== 当前任务 ==="
  compress "$task_id" 10
  
  echo ""
  echo "=== 相关历史 ==="
  [ -n "$query" ] && search "$query" 3
  
  echo ""
  echo "=== Agent指令 ==="
  echo "  角色: ${agent_role}"
  echo "  任务: ${task_id}"
  echo "  注意: 优先参考当前任务记忆，必要时查阅相关历史"
}

# ==========================================
# Agent工作历史 (追踪每个Agent处理过的任务)
# ==========================================
agent_log() {
  local agent_id="$1" task_id="$2" action="${3:-assigned}"
  local ts=$(date +%s)
  
  # 追加到Agent的任务列表
  $REDIS LPUSH "agent:memory:agent:${agent_id}" "${task_id}|${action}|${ts}" > /dev/null
  
  # 限制列表长度
  $REDIS LTRIM "agent:memory:agent:${agent_id}" 0 99 > /dev/null
}

agent_history() {
  local agent_id="$1" limit="${2:-10}"
  echo "📜 ${agent_id} 最近任务:"
  $REDIS LRANGE "agent:memory:agent:${agent_id}" 0 $((limit - 1)) 2>/dev/null | while IFS='|' read -r tid action ts; do
    local dt=$(date -d "@${ts}" '+%m-%d %H:%M' 2>/dev/null || echo "?")
    printf "  %s  %-10s  %s\n" "$dt" "$action" "$tid"
  done
}

# ==========================================
# 全局仪表盘
# ==========================================
dashboard() {
  echo "╔══════════════════════════════════════════╗"
  echo "║     调度中心记忆体 — 全局视图             ║"
  echo "╠══════════════════════════════════════════╣"
  echo "║ 📊 任务索引                               ║"
  local total=$($REDIS ZCARD "agent:memory:index" 2>/dev/null)
  echo "║    总任务: ${total:-0}                              ║"
  echo "║                                          ║"
  echo "║ 📋 最近任务                               ║"
  $REDIS ZREVRANGE "agent:memory:index" 0 4 WITHSCORES 2>/dev/null | while IFS= read -r tid; do
    read -r score
    local summary=$($REDIS HGET "agent:memory:${tid}" "summary" 2>/dev/null)
    local status=$($REDIS HGET "agent:memory:${tid}" "status" 2>/dev/null)
    printf "║   %-25s %-15s ║\n" "${tid:0:25}" "${status:-pending}"
  done
  echo "║                                          ║"
  echo "║ 🏷️ 关键词索引                             ║"
  local kw_count=$($REDIS KEYS "agent:memory:kw:*" 2>/dev/null | wc -l)
  echo "║    关键词: ${kw_count:-0}                              ║"
  echo "╚══════════════════════════════════════════╝"
}

# ==========================================
# CLI
# ==========================================
case "${1:-}" in
  save)    save "$2" "$3" "$4" ;;
  snapshot) shift; snapshot "$@" ;;
  recall)  recall "$2" ;;
  search)  search "$2" "$3" ;;
  inject)  inject "$2" "$3" ;;
  compress) compress "$2" "$3" ;;
  agent-log) agent_log "$2" "$3" "$4" ;;
  agent-history) agent_history "$2" "$3" ;;
  dashboard) dashboard ;;
  *)
    echo "MemoryManager — 调度中心外挂记忆体"
    echo ""
    echo "写入:  $0 save <taskId> <key> <value>"
    echo "快照:  $0 snapshot <taskId> key1=val1 key2=val2..."
    echo "检索:  $0 recall <taskId>"
    echo "搜索:  $0 search <关键词>"
    echo "压缩:  $0 compress <taskId> [maxFields]"
    echo "注入:  $0 inject <taskId> <agentRole>"
    echo "追踪:  $0 agent-log <agentId> <taskId> [action]"
    echo "历史:  $0 agent-history <agentId>"
    echo "面板:  $0 dashboard"
    echo ""
    echo "示例:"
    echo "  $0 save T001 需求 '沙漠光伏项目,5000根螺旋地桩'"
    echo "  $0 save T001 方案 'Q235B热镀锌,φ76×1200mm'"
    echo "  $0 inject T001 售前经理  # 注入上下文启动Agent"
    ;;
esac
