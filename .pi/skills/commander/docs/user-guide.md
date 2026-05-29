# Yaxiio User Guide

> 从零到跑起来的生产级配置指南

## 1. 快速体验（5 分钟）

```bash
git clone https://github.com/jamedow/yaxiio.git
cd yaxiio
docker compose up -d
open http://localhost:3004
```

访问 Dashboard，发送第一个任务：

```bash
docker exec yaxiio redis-cli PUBLISH 'yaxiio:agent:commander' \
  '{"type":"task","taskId":"demo-001","payload":{"action":"site_audit","task":"审计网站内容质量"}}'
```

## 2. 配置 LLM API Key

### DeepSeek（必需）

```bash
export DEEPSEEK_API_KEY="sk-your-deepseek-key"
```

注册地址：https://platform.deepseek.com

### 硅基流动（推荐，加速翻译）

硅基流动每个 API Key 有独立配额，加 N 个 Key 就 N 倍并发。

```bash
# 方式1: 环境变量（单Key）
export SILICON_KEY="sk-your-silicon-key"
export LLM_BASE_URL="https://api.siliconflow.cn/v1"
export LLM_MODEL="deepseek-ai/DeepSeek-V4-Flash"

# 方式2: Redis Key池（多Key，推荐）
docker exec yaxiio redis-cli SET yaxiio:config:silicon_keys \
  '["sk-key1","sk-key2","sk-key3","sk-key4","sk-key5"]'
```

注册地址：https://siliconflow.cn — 免费注册，每个账号可开多个 Key。

**效果对比：**

| 配置 | 翻译速度 | 说明 |
|------|---------|------|
| 1 个 DeepSeek Key | ~70条/轮 | 基准速度 |
| +1 个硅基 Key | ~200条/轮 | 3x 加速 |
| +5 个硅基 Key | ~500条/轮 | 7x 加速 |
| +11 个硅基 Key | ~1000条/轮 | 14x 加速 |

## 3. 连接你的网站数据

Yaxiio 需要读取页面内容才能审计和修复。两种方式：

### 方式 A：已有 MongoDB

```bash
export MONGO_URI="mongodb://your-host:27017"
export MONGO_DATABASE="your_db"
```

### 方式 B：SQLite（无需 MongoDB）

```bash
export YAXIIO_MODE=lite
export YAXIIO_DB="/app/data/yaxiio.db"
```

然后导入数据：
```bash
python3 tools/import_pages.py --source your-data.json
```

## 4. 运行全站审计

```bash
# 通过 Dashboard (http://localhost:3004) 或命令行
docker exec yaxiio redis-cli PUBLISH 'yaxiio:agent:commander' '{
  "type":"task",
  "taskId":"full-audit-001",
  "payload":{
    "action":"site_audit",
    "task":"全站五语内容质量审计，检查语言混杂、术语一致性、参数真实性",
    "target":"all",
    "mode":"full"
  }
}'
```

Yaxiio 会自动：
1. L1 感知：识别为 audit 任务
2. L2 规划：拆解为 5 个子任务（完整性/术语/参数/专业性/汇总）
3. L3 协调：调度审计官 Agent
4. L4 执行：调用 multlang_audit 工具扫描 MongoDB
5. L5 评分：LLM 评分 + 差距分析 → 自动重试直到达标

## 5. 自动修复内容问题

审计发现语言混杂后，发送修复任务：

```bash
docker exec yaxiio redis-cli PUBLISH 'yaxiio:agent:commander' '{
  "type":"task","taskId":"fix-001",
  "payload":{
    "action":"fix_codebase",
    "task":"修复全站3793处语言混杂。使用targeted_fix工具翻译中文字段为目标语言。",
    "target":"all","mode":"full"
  }
}'
```

Yaxiio 会自动：
- 数据驱动拆解：3793 → 9 批次 × 474 条
- 并行调度：交替使用审计官 + LM内容工程师
- 验证循环：每轮后重新审计，数字不归零不停止

## 6. 自定义 Agent

### 创建新 Agent

```bash
docker exec yaxiio redis-cli SET 'agent:card:我的Agent' '{
  "name":"我的Agent","role":"自定义角色","quadrant":"strategic",
  "model":"deepseek-chat","thinking":"medium",
  "system_prompt":"你是...",
  "skills":["my-skill"],
  "tools":["mongo_query"],
  "lifecycle":{"task_timeout":300,"max_retries":2}
}'
docker exec yaxiio redis-cli SADD agent:registry "我的Agent"
```

### 给 Agent 加工具

```bash
# 注册工具
docker exec yaxiio redis-cli HSET tools:registry mytool \
  '{"name":"mytool","desc":"我的工具","usage":"python3 tools/mytool.py"}'

# 分配给 Agent
docker exec yaxiio redis-cli SADD tools:agent:我的Agent mytool
```

Agent 重启后自动发现新工具，无需改代码。

## 7. 人类评分

访问 `http://localhost:3004/scores` 对 Agent 产出打分。评分权重：

- 人类评分：70%
- AI 评分：30%
- 人机分差 > 3：触发异常审查

你的评分会直接影响 L5 进化方向。

## 8. 监控与调试

```bash
# 查看 Dashboard
open http://localhost:3004

# 查看 Agent 状态
docker exec yaxiio redis-cli KEYS 'agent:*:state'

# 查看任务结果
docker exec yaxiio redis-cli GET 'yaxiio:task:your-task-id'

# 查看 L5 评分历史
curl http://localhost:3004/api/scores

# 后台修复进度
docker exec yaxiio python3 tools/multilang_audit.py
```

## 9. 常见问题

**Q: 翻译很慢怎么办？**
A: 加硅基流动 Key。一个 Key = 一个独立配额，11 个 Key = 11 倍速度。免费注册，不花钱。

**Q: 不想用 MongoDB？**
A: 设置 `YAXIIO_MODE=lite`，Yaxiio 自动切到 SQLite。

**Q: Agent 超时了？**
A: v1.7 已修复。超时时间 60s → 600s，轮询改为指数退避。

**Q: 重启丢任务？**
A: v1.7 已支持断点续传。Commander 启动时自动扫描未完成任务并恢复。

**Q: 怎么让 Agent 学会新技能？**
A: 写一个新的 Skill.md 放到 skills/ 目录，然后在能力卡片里引用。L5 的 `generate_skill` 也能自动生成。
