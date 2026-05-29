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
# Commander Agent Factory — 动态创建/销毁 Skill + Agent
# 用法: bash agent-factory.sh <action> <name> [role] [prompt]

ACTION="${1:-help}"
NAME="${2:-}"
ROLE="${3:-worker}"
PROMPT="${4:-通用任务处理}"

SKILL_DIR="/app/.pi/skills/${NAME}"
AGENT_DIR="/app/.pi/agents/runtime"

case "$ACTION" in
  create)
    [ -z "$NAME" ] && { echo "用法: $0 create <skill名> <角色名> <提示词>"; exit 1; }
    
    # 1. 创建 Skill
    mkdir -p "${SKILL_DIR}/experience"
    cat > "${SKILL_DIR}/SKILL.md" << SKILL
---
name: ${NAME}
description: ${PROMPT}
---

# ${ROLE} — ${NAME}

# 核心任务
${PROMPT}

# 通信
- 订阅频道: lightingmetal:agent:${ROLE}
- 消息格式: 标准JSON (from/to/type/taskId/payload)
- 支持P2P: 通过replyTo字段直接回复请求方

# 安全边界
- 不删除Redis数据
- 不修改MongoDB原始数据
SKILL
    
    # 2. 创建经验
    cat > "${SKILL_DIR}/experience/patterns.json" << 'PATTERNS'
{"version":1,"lastUpdated":"","knownPatterns":[]}
PATTERNS
    
    # 3. 注册Agent进程
    cp "${AGENT_DIR}/agent.sh" "${AGENT_DIR}/agent-${NAME}.sh" 2>/dev/null
    
    echo "✅ Skill+Agent 已创建: ${NAME} (${ROLE})"
    echo "   Skill: ${SKILL_DIR}/SKILL.md"
    echo "   启动: pm2 start ${AGENT_DIR}/agent.sh --name agent-${NAME} -- ${ROLE}"
    ;;
  
  spawn)
    [ -z "$NAME" ] && { echo "用法: $0 spawn <agent名> <角色名>"; exit 1; }
    
    # 启动Agent进程
    pm2 delete "agent-${NAME}" 2>/dev/null
    pm2 start "${AGENT_DIR}/agent.sh" --name "agent-${NAME}" -- "${ROLE}" 2>&1 | tail -1
    
    sleep 1
    # 验证
    SUB=$(docker exec redis-centos7 redis-cli -a 'Lt@114514!' PUBLISH "lightingmetal:agent:${ROLE}" '{"type":"heartbeat_check","to":"'"${ROLE}"'"}' 2>/dev/null)
    if [ "$SUB" -gt 0 ] 2>/dev/null; then
      echo "✅ Agent已上线: ${ROLE} (订阅者:${SUB})"
    else
      echo "⚠️  Agent启动中..."

    fi
    ;;
  
  destroy)
    [ -z "$NAME" ] && { echo "用法: $0 destroy <agent名>"; exit 1; }
    
    pm2 delete "agent-${NAME}" 2>/dev/null
    rm -rf "${SKILL_DIR}" 2>/dev/null
    rm -f "${AGENT_DIR}/agent-${NAME}.sh" 2>/dev/null
    echo "🗑️  Agent+Skill已销毁: ${NAME}"
    ;;
  
  list)
    echo "=== 自定义Agent ==="
    pm2 list 2>/dev/null | grep "agent-" | awk '{print "  "$4" "$10}'
    echo ""
    echo "=== 自定义Skill ==="
    ls -d /app/.pi/skills/*/ 2>/dev/null | while read d; do
      name=$(basename "$d")
      [ "$name" = "audit-engine" ] && continue
      [ "$name" = "backend-engineer" ] && continue
      [ "$name" = "cms-engineer" ] && continue
      [ "$name" = "infrastructure-engineer" ] && continue
      [ "$name" = "product-search" ] && continue
      [ "$name" = "seo-engineer" ] && continue
      [ "$name" = "strategic-partner" ] && continue
      [ "$name" = "translate-engine" ] && continue
      [ "$name" = "ui-ux-designer" ] && continue
      [ "$name" = "commander" ] && continue
      desc=$(head -3 "$d/SKILL.md" 2>/dev/null | grep description | cut -d: -f2- | xargs)
      echo "  ${name}: ${desc:0:80}"
    done
    ;;
  
  analyze)
    # 分析任务文本，自动决定需要创建什么Agent
    TASK="${2:-}"
    [ -z "$TASK" ] && { echo "用法: $0 analyze <任务描述>"; exit 1; }
    
    echo "📋 任务分析: ${TASK:0:100}..."
    echo ""
    
    # 关键词匹配
    NEEDS=()
    echo "$TASK" | grep -qi "翻译\|translate\|俄语\|阿拉伯\|西班牙" && NEEDS+=("翻译官")
    echo "$TASK" | grep -qi "报价\|报价单\|询价\|客户\|quote\|inquiry" && NEEDS+=("售前经理")  
    echo "$TASK" | grep -qi "客服\|接待\|需求\|对话\|conversation" && NEEDS+=("商务经理")
    echo "$TASK" | grep -qi "审计\|检查\|qa\|质量\|审核" && NEEDS+=("审计官")
    
    if [ ${#NEEDS[@]} -eq 0 ]; then
      echo "  未匹配到特定Agent需求，使用通用Commander处理"
    else
      echo "  建议Agent:"
      for a in "${NEEDS[@]}"; do
        printf "    - %s\n" "$a"
      done
      echo ""
      echo "  创建命令:"
      for a in "${NEEDS[@]}"; do
        nm=$(echo "$a" | tr '[:upper:]' '[:lower:]' | sed 's/官//;s/经理//')
        echo "    bash $0 spawn ${nm} ${a}"
      done
    fi
    ;;
  
  *)
    echo "Agent Factory — 动态Skill+Agent管理"
    echo ""
    echo "用法:"
    echo "  $0 create <名称> <角色> <提示词>   创建Skill+Agent模板"
    echo "  $0 spawn  <名称> <角色>            启动Agent进程"
    echo "  $0 destroy <名称>                  销毁Agent+Skill"
    echo "  $0 list                            列出自定义Agent"
    echo "  $0 analyze <任务描述>              分析任务，建议Agent"
    echo ""
    echo "示例:"
    echo "  $0 create ru-auditor 俄语审计官 '扫描俄语页面中文残留'"
    echo "  $0 spawn ru-auditor 俄语审计官"
    echo "  $0 analyze '翻译7个俄语页面并检查中文残留'"
    ;;
esac
