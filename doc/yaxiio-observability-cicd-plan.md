# Yaxiio v2.0 可观测性与 CI/CD — 调研与方案设计

> 调研日期: 2026-05-29  
> 参考: MLflow, RagaAI Catalyst, Laminar, OpenLIT, CrewAI

---

## 一、业界方案调研

### 可观测性

| 工具 | Stars | 定位 | 适合 Yaxiio? |
|------|:----:|------|:-----------:|
| **Laminar** | 3K | Agent 专用可观测平台, OpenTelemetry, Rust, Tracing+Evals+Dashboards | ⭐⭐⭐⭐⭐ 最匹配 |
| RagaAI Catalyst | 16K | LLM 项目管理+评估+Trace+护栏, 企业级 | ⭐⭐⭐ 太重 |
| MLflow | 26K | 通用 ML 实验追踪, 不是 Agent 专用 | ⭐⭐ 太泛 |
| OpenLIT | 2.5K | OpenTelemetry LLM 可观测性 | ⭐⭐⭐ 可用但不如 Laminar 专注 |

**结论**: Laminar 是唯一专为 AI Agent 设计的开源可观测平台。它用 Rust 写，性能极高，支持 OpenTelemetry 自动追踪、自然语言定义监控事件、SQL 查询所有数据、自定义 Dashboard。Yaxiio 应该对标它的能力，但初期自建轻量版本。

### CI/CD

| 参考 | 工作流数量 | 亮点 |
|------|:--:|------|
| **CrewAI** | 14 | linter + tests + type-checker + CodeQL + PR size + vulnerability scan + nightly |
| LangChain | 20+ | 完整的 monorepo CI, 多 Python 版本矩阵 |
| AutoGen | 10+ | 分核心/扩展两套 CI |

**结论**: CrewAI 的 CI 结构最简洁实用。Yaxiio 起步只需要 4 个 workflow。

---

## 二、优化 #7: 可观测性方案

### 第一阶段：自建轻量（本周）

```
不需要引入外部依赖，用 Yaxiio 已有基础设施实现：

1. 结构化 JSON 日志
   - 替换 print() → structlog/JSON 格式
   - 每条日志带 trace_id, layer, agent, task_id
   - 写入 /opt/commander/logs/events.jsonl

2. /health 端点增强（已有基础）
   - 加 /health/metrics 返回 Prometheus 格式
   - 指标: task_duration_seconds, agent_count, redis_memory_bytes,
     task_queue_depth, l5_score_avg, restart_count

3. 关键事件告警
   - Redis 内存 > 80% → 发 commander:events:alert
   - Commander 1分钟内重启 > 3 次 → 发告警
   - 核心文件哈希变更 → 发告警

4. 内置 Dashboard（已有 React Flow）
   - 加一个 /dashboard/metrics 面板
   - 显示: 任务吞吐曲线, Agent 健康状态, Redis/内存趋势
```

### 第二阶段：引入 Laminar（下个迭代）

```
docker compose 加 laminar 服务:
  laminar:
    image: lmnr/laminar:latest
    ports: ["3005:3005"]
    
Yaxiio Agent 加 1 行代码:
  from laminar import Laminar
  Laminar.initialize(project="yaxiio")

自动获得:
  - 全链路 Trace (L1→L5 每层耗时)
  - LLM 调用追踪 (token 消耗, 延迟)
  - 自定义 Dashboard
  - SQL 查询所有追踪数据
```

---

## 三、优化 #8: CI/CD 方案

### GitHub Actions — 4 个 Workflow

**1. ci.yml — 每次 push 触发**
```yaml
name: CI
on: [push, pull_request]
jobs:
  lint:
    - ruff check .
  test:
    - python3 test_dispatch_suite.py
    - python3 -m pytest yaxiio/tests/
  type-check:
    - mypy yaxiio/ --ignore-missing-imports
```

**2. docker.yml — main 分支 push 触发**
```yaml
name: Docker Build
on:
  push:
    branches: [main]
jobs:
  build:
    - docker build -t yaxiio:latest .
    - docker push ghcr.io/jamedow/yaxiio:latest
```

**3. security.yml — 每周扫描**
```yaml
name: Security Scan
on:
  schedule: [{cron: "0 0 * * 0"}]
jobs:
  codeql:
    - uses: github/codeql-action/analyze
  deps:
    - pip-audit
```

**4. docs.yml — docs 目录变更触发**
```yaml
name: Docs
on:
  push:
    paths: ["docs/**", "**.md"]
jobs:
  gen:
    - python3 tools/gen_agent_table.py docs/agent-list.md
    - git diff --exit-code || (echo "Agent list outdated!" && exit 1)
```

---

## 四、优先级评估

| 项目 | 工作量 | 价值 | 优先级 |
|------|:--:|------|:--:|
| JSON 结构化日志 | 1h | ⭐⭐⭐⭐ | **立即** |
| /health/metrics | 1h | ⭐⭐⭐⭐⭐ | **立即** |
| CI lint+test | 2h | ⭐⭐⭐⭐⭐ | **本周** |
| Docker build CI | 1h | ⭐⭐⭐⭐ | **本周** |
| 事件告警 | 2h | ⭐⭐⭐ | 下迭代 |
| Laminar 集成 | 4h | ⭐⭐⭐⭐ | 下迭代 |
| CodeQL 安全扫描 | 0.5h | ⭐⭐⭐ | **本周** |

---

## 五、实施记录

| 编号 | 优化项 | 状态 | 完成日期 |
|------|--------|:--:|----------|
| 7a | JSON 结构化日志 | ⬜ | - |
| 7b | /health/metrics 端点 | ⬜ | - |
| 7c | 事件告警 | ⬜ | - |
| 8a | CI lint+test | ⬜ | - |
| 8b | Docker build CI | ⬜ | - |
| 8c | Security scan | ⬜ | - |
| 8d | Docs auto-check | ⬜ | - |

---

> 参考链接:
> - Laminar: https://github.com/lmnr-ai/lmnr
> - CrewAI CI: https://github.com/crewAIInc/crewAI/tree/main/.github/workflows
> - RagaAI: https://github.com/raga-ai-hub/RagaAI-Catalyst
