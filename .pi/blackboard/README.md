# AI Agent Blackboard — 文件系统协作总线

## 目录结构

```
.pi/blackboard/
├── tasks/           # 待处理任务
│   └── {task-id}/
│       ├── READY        # 空文件=任务待领
│       ├── CLAIMED      # 谁领了这个任务
│       ├── DONE         # 任务完成
│       ├── REPORT       # 审计/验证报告
│       ├── input/       # 输入文件
│       └── output/      # 输出文件
├── inbox/           # 新任务提交
│   └── {task-id}.json  # 任务描述
└── reports/         # 已完成报告存档
```

## 任务协议

### 1. 提交任务 (Agent A)
写入 inbox/{task-id}.json:
```json
{
  "id": "audit-power-solar",
  "from": "translate-engine",
  "to": "audit-engine", 
  "action": "audit_i18n",
  "scope": "power/solar-farm",
  "files": ["i18n/en/industries/power/solar-farm/**/*.json"],
  "created": "2026-05-22T10:00:00Z"
}
```
创建 tasks/{task-id}/ 目录，放入 input 文件。

### 2. 认领任务 (Agent B)
写 tasks/{task-id}/CLAIMED，内容是 "audit-engine 2026-05-22T10:05:00Z"

### 3. 完成任务 (Agent B)
写 tasks/{task-id}/DONE
写 tasks/{task-id}/REPORT（审计报告/修复建议）
输出文件放 tasks/{task-id}/output/

### 4. 读反馈 (Agent A)
读 tasks/{task-id}/REPORT
根据建议修复
写 tasks/{task-id}/ACK（确认收到）

## 任务类型

| action | 发送方 | 接收方 | 内容 |
|--------|--------|--------|------|
| audit_i18n | translate-engine | audit-engine | 翻译文件审计 |
| audit_pages | deploy | audit-engine | 部署后验证 |
| check_links | any | audit-engine | 死链扫描 |
| verify_sync | backend-engineer | infrastructure-engineer | 同步状态验证 |
| review_code | any | backend-engineer | 代码Review |

## 查询命令

```bash
# 看所有待处理任务
ls -la .pi/blackboard/tasks/*/READY

# 看我认领的任务  
grep -r "audit-engine" .pi/blackboard/tasks/*/CLAIMED

# 看报告
cat .pi/blackboard/tasks/{task-id}/REPORT
```
