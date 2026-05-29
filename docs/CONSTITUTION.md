# 雅溪宪法 Yaxiio Constitution

## 概述

宪法是 Commander 的行为约束框架，**硬编码在代码路径中**，不可绕过。每个任务必经 `constitution.review()` 审查。

## 四大裁决

```
constitution.review(action, payload)
  → ALLOWED    — 系统白名单，Commander 直通执行
  → DELEGATED  — 必须走 L1→L5 MCP 流水线
  → REJECTED   — 违宪，拒绝执行
  → DEGRADED   — 高危操作，强制 sandbox 降级
```

## 白名单 (SYSTEM_OPS)

只有 6 个纯系统管理操作可以绕过流水线：

| 操作 | 说明 |
|------|------|
| `session_end` | 清理临时沙箱 |
| `agent_export` | Agent 配置备份导出 |
| `agent_import` | Agent 配置恢复导入 |
| `skill_export` | Skill 配置备份导出 |
| `skill_import` | Skill 配置恢复导入 |
| `status` | 系统健康检查 |

## 禁止直接执行 (FORBIDDEN_DIRECT)

Commander 不能亲自执行的业务操作：

`site_audit`, `site_fix`, `site_evolve`, `site_drill`, `site_build`, `site_deploy`,
`site_inquire`, `translate_mongodb`, `translate_all_pages`, `generate_quote`, `send_email`

## 高危模式检测 (DANGEROUS_PATTERNS)

payload 中包含以下模式自动降级到 sandbox：

`docker exec/run/build`, `ssh/scp/rsync`, `rm -rf`, `dd`, `mkfs`,
`kill -9`, `pkill`, `reboot`, `shutdown`, `curl`, `wget`, `eval(`, `exec(`, `compile(`

## 违宪记录

所有违宪行为写入 Redis `yaxiio:constitution:violations` (LPUSH, 最近 100 条)，不可绕过审计。

## 统计

```python
c = get_constitution(redis)
c.stats()
# {"total_checks": 142, "allowed": 6, "delegated": 130,
#  "rejected": 6, "violations": 12, "compliance_rate": 0.04}
```

## 设计原则

1. **MCP-First** — 所有任务默认走 L1→L5
2. **纯编排** — Commander 只做路由和调度
3. **白名单准入** — 只有宪法明确授权可绕过
4. **LLM 决策** — 任务理解和分派由 LLM 驱动
5. **沙箱隔离** — 代码执行必须在 L4 sandbox
6. **审计不可绕过** — 所有操作记录在案
