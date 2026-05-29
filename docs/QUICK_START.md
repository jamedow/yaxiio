# 雅溪 Yaxiio 快速上手指南

> 5 分钟第一个 Agent → 10 分钟定制 Agent → 15 分钟自我进化

---

## 前置条件

- Docker 已安装
- DeepSeek API Key（或其他 OpenAI 兼容的 Key）
- 2GB 可用内存

---

## 一、5 分钟：启动 Yaxiio + 创建第一个 Agent

### 1.1 启动容器

```bash
docker run -d --name yaxiio \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -p 3398:3398 -p 3399:3399 \
  -e DEEPSEEK_API_KEY=你的Key \
  --restart unless-stopped \
  yaxiio:prod
```

### 1.2 验证服务

```bash
curl http://localhost:3399/health
# → {"status":"ok","version":"3.0.0","uptime":12}
```

### 1.3 创建第一个 Agent

```bash
docker exec yaxiio bash -c '
  redis-cli -a Yaxiio2026 PUBLISH yaxiio:agent:commander \
    '"'"'{"type":"task","taskId":"hello-001","from":"cli","payload":{"action":"status"}}'"'"'
'
```

Yaxiio 会自动创建一个临时 Agent 来处理这个任务。

### 1.4 查看结果

```bash
docker exec yaxiio bash -c 'redis-cli -a Yaxiio2026 GET yaxiio:task:hello-001'
# → {"task_id":"hello-001","status":"DONE","result":{...}}
```

---

## 二、10 分钟：用能力卡片定制 Agent

### 2.1 创建能力卡片

能力卡片是一个 JSON 文件，定义了 Agent 的身份、大脑、工具箱。创建 `my-auditor.json`：

```json
{
  "name": "我的审计官",
  "role": "专业内容质量审计",
  "quadrant": "strategic",
  "version": "1.0.0",
  "model": "deepseek-chat",
  "thinking": "medium",
  "temperature": 0.3,
  "system_prompt": "你是一个严谨的内容审计专家。检查文本的术语一致性、数据准确性、格式规范性。输出问题列表和修改建议。",
  "skills": ["audit-engine"],
  "tools": ["mongo_query", "redis_query", "terminology_check"],
  "lifecycle": {
    "task_timeout": 300,
    "max_retries": 3,
    "idle_timeout": 600
  }
}
```

### 2.2 注册能力卡片到 Redis

```bash
docker exec yaxiio bash -c '
  redis-cli -a Yaxiio2026 SET "agent:card:我的审计官" "$(cat /opt/yaxiio/my-auditor.json)"
  redis-cli -a Yaxiio2026 SADD "agent:registry" "我的审计官"
'
```

### 2.3 启动 Agent

```bash
docker exec yaxiio bash -c '
  AGENT_NAME="我的审计官" AGENT_SKILL="audit-engine" \
  LLM_MODEL="deepseek-chat" python3 /opt/yaxiio/.pi/skills/commander/neuron.py &
'
```

### 2.4 派任务

```bash
docker exec yaxiio bash -c '
  redis-cli -a Yaxiio2026 PUBLISH "lightingmetal:agent:我的审计官" \
    '"'"'{"type":"task","taskId":"audit-001","payload":{"action":"audit","task":"审计 power 行业页面术语一致性"}}'"'"'
'
```

---

## 三、15 分钟：L4+L5 自我进化

### 3.1 场景

你有一个翻译 Agent，但翻译质量（L5 评分）稳定在 6-7 分。你想让系统自动优化它。

### 3.2 触发进化

提交一个复杂任务，让系统自己跑 3 轮自检循环：

```bash
docker exec yaxiio bash -c '
  redis-cli -a Yaxiio2026 PUBLISH yaxiio:agent:commander \
    '"'"'{"type":"task","taskId":"evolve-001","payload":{"action":"translate","task":"翻译 100 条产品描述到阿拉伯语，确保术语一致","_thinking":"high"}}'"'"'
'
```

### 3.3 观察进化过程

```bash
# 实时日志
docker logs -f yaxiio

# 查看 L5 评分变化
docker exec yaxiio bash -c '
  for i in $(seq 1 3); do
    redis-cli -a Yaxiio2026 GET "yaxiio:task:evolve-001" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"Round: l5={d.get(\"l5_result\",{}).get(\"overall\",\"?\")}\")"
    sleep 10
  done
'
```

### 3.4 进化结果

系统自动做了这些事：
1. 第一轮执行 → L5 评分 6 分 → 触发 gap 分析
2. 差距：术语不一致 → L0 搜索 web 知识 → 补充术语表
3. 第二轮执行（带术语表）→ L5 评分 8 分 → 通过
4. 任务经验写入 L0 经验库，下次同类任务直接参考

---

## 四、常用调试命令

```bash
# 进入容器
docker exec -it yaxiio bash

# 健康检查
curl -s 172.17.0.5:3399/health | python3 -m json.tool

# 查看指标
curl -s 172.17.0.5:3399/metrics

# 查看链路日志
curl -s 172.17.0.5:3399/trace/hello-001

# 查看活跃 Agent
redis-cli -a Yaxiio2026 SMEMBERS "commander:agents:active"

# 查看宪法统计
redis-cli -a Yaxiio2026 GET "yaxiio:constitution:violations" | python3 -c "import sys; print(len(sys.stdin.read().splitlines()))"

# 查看任务状态
redis-cli -a Yaxiio2026 KEYS "yaxiio:task:*" | while read k; do echo "$k: $(redis-cli -a Yaxiio2026 GET $k | python3 -c 'import sys,json;print(json.load(sys.stdin).get(\"status\",\"?\"))')"; done
```

---

## 五、下一步

| 你想做什么 | 阅读 |
|-----------|------|
| 理解设计思想 | `DESIGN-PHILOSOPHY.md` |
| 深入架构 | `ARCHITECTURE.md` |
| 写能力卡片 | `CAPABILITY-CARD-SPEC.md` |
| 理解状态机 | `STATE-MACHINE.md` |
| 查看 API | `API.md` |
| 了解当前代码质量 | `CODE_REVIEW.md` |
