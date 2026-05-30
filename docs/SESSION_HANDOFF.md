# Yaxiio 会话移交手册

> 日期: 2026-05-30 | 会话: 2026-05-30 (session 2)
> 状态: L1→L5 全管道跑通 | BoundedThreadPool 死锁修复 | Pub/Sub→Stream 迁移

---

## 一、快速接续

```bash
# 1. 容器已运行 (yaxiio:prod)，PM2 管理 Guardian+Commander
docker ps | grep yaxiio

# 2. 查看 Commander 状态
pm2 status
redis-cli -a Yaxiio2026 PUBSUB NUMSUB yaxiio:agent:commander

# 3. 启动 Gateway (如未运行)
nohup python3.12 /opt/yaxiio/.pi/skills/commander/gateway.py \
  --ws-port 3398 --http-port 3399 \
  --redis-host 127.0.0.1 --redis-password Yaxiio2026 \
  --llm-api-key sk-3496ec4c7cbb471d818670ae80cfba39 &

# 4. 提交任务 (两种方式)
#   方式A: 直接 Pub/Sub (Commander 入口)
redis-cli -a Yaxiio2026 PUBLISH yaxiio:agent:commander \
  '{"type":"task","taskId":"test-001","payload":{"action":"site_audit","task":"审计solar-farm L4","target":"power","codebase":"/opt/lightingMetal/customer-portal"}}'
#   方式B: 直接写入 Stream (跳过 Commander 入口，直接到 L4)
python3.12 -c "
from stream_bridge import StreamBridge
b = StreamBridge(redis_host='127.0.0.1', redis_port=6379, redis_password='Yaxiio2026')
b.publish_task('L4', {'type':'task','taskId':'stream-001','payload':{'action':'site_audit','task':'test'}}, 'stream-001')
"

# 5. 查看结果
redis-cli -a Yaxiio2026 GET yaxiio:task:test-001
redis-cli -a Yaxiio2026 XRANGE yaxiio:stream:L4 - +   # Stream 消息
```

## 二、本次会话修复的 Bug

### 阻塞级 (Blocker)
| Bug | 根因 | 修复 |
|-----|------|------|
| **BoundedThreadPool 死锁** | `with self._lock:` 内重复 `self._lock.acquire()`，threading.Lock 非重入 | 删除内部 acquire/release |
| **`_run_delegated` NameError** | `_run()` 闭包引用 `trace_id` 但 `_run_delegated` 未定义该变量 | 添加 `trace_id = data.get("trace_id") or str(uuid.uuid4())[:12]` |
| **Guardian KeyError crash** | `evaluate()` 返回 key 不一致 (`score` vs `overall`) | 统一用 `overall`，Guardian 用 `.get()` |

### 神经元 (Neuron)
| Bug | 修复 |
|-----|------|
| `SKILL_DIR = "/app/.pi/skills"` 路径不存在 | → `"/opt/yaxiio/.pi/skills"` |
| `_load_capability_card()` 总是 `return {}` | → `return card` |
| stdout pipe 死锁 (64KB 缓冲区满) | → `subprocess.DEVNULL` |
| `sys.path` 引用 `/app/.pi/skills/commander` | → `/opt/yaxiio/.pi/skills/commander` |

### 通信升级
| 改动 | 说明 |
|------|------|
| L4 任务发布: Pub/Sub → **Stream** | `StreamBridge.publish_task("L4", msg, task_id)` |
| L4 等待响应: Pub/Sub → **Stream 轮询** | `xreadgroup` + Pub/Sub 回退 |
| Neuron 消费: Pub/Sub → **Stream 优先** | `consume_task()` + Pub/Sub 回退 |
| Neuron 响应: Pub/Sub → **Stream 优先** | `publish_task("L4_response", ...)` |

### 容器 & Git
| 操作 | 状态 |
|------|------|
| Docker commit `yaxiio:prod` | ✅ `033d05e` |
| Git commit | ✅ `5eb23a2` (main) |
| Git push 阿里云 Codeup | ✅ |
| Git push GitHub | ✅ (配置了 `~/.ssh/id_ed25519_github`) |

## 三、已验证的功能

| 模块 | 状态 |
|------|------|
| L1 Perception | ✅ 意图识别 + 动作覆盖 |
| L2 Planning | ✅ LLM 拆解 + 经验注入 + 语义路由 |
| L3 AsyncOrchestrator | ✅ 异步编排 (YAXIIO_ASYNC_ORCHESTRATOR=true) |
| L4 Execution (Stream) | ✅ StreamBridge 发布/消费/ACK |
| L5 UnifiedScorer | ✅ 4源融合评分 (rule fallback) |
| L5 GapAnalyzer | ✅ 差距分析 |
| L5 ExperienceFlywheel | ✅ 经验保存 |
| Constitution | ✅ 宪法审查 + 语义校验 |
| Foolproof | ✅ 渐进披露 |
| 重试机制 | ✅ 2次重试后超时 |

## 四、已知问题

1. **Agent LLM 超时** — Neuron 调用 DeepSeek API 偶尔 > 120s，L5 只有 rule fallback (5.0分)
   - 修复方向: 增大 `task_timeout`、优化 Prompt 减少 token 数、加流式响应
2. **Gateway 未自动启动** — 容器重启后需手动启动 Gateway
   - 修复方向: 加入 PM2 管理或 Docker entrypoint
3. **Stream 未全覆盖** — AsyncOrchestrator 路径走了自己的派发逻辑
   - 修复方向: 统一 `async_orchestrator.py` 的通信层也用 StreamBridge
4. **僵尸进程** — Neuron 超时后进程残留
   - 修复方向: Commander 增加孤儿进程清理
5. **旧 API Key** — Gateway 启动参数中 `sk-22BhHx...` 需替换为 Redis 中的新 key

## 五、关键配置

| 配置项 | 值 | 位置 |
|--------|-----|------|
| API Key | `sk-3496ec4c7cbb471d818670ae80cfba39` | Redis: `yaxiio:config:llm_api_key` |
| Redis 密码 | `Yaxiio2026` | 环境变量 |
| redis-py | 5.2.1 (protocol=2) | 不要升级到 8.x |
| Commander 重启 | `pm2 restart yaxiio-guardian` | PM2 管理 |
| Stream L4 | `yaxiio:stream:L4` | Consumer Group: `agents-L4` |
| Stream L4 响应 | `yaxiio:stream:L4_response` | Consumer Group: `commander-response` |
| Async Orchestrator | `YAXIIO_ASYNC_ORCHESTRATOR=true` | 环境变量 |

## 六、下一步建议

1. **修 Agent 超时** — 最优先，阻塞真实 L4 结果
2. **补全 Stream 迁移** — `async_orchestrator.py`、`gateway.py` 的通信层
3. **Gateway 加入 PM2** — 容器重启自启动
4. **清洗旧 API Key** — 全局搜索 `sk-22BhHx` 替换
5. **端到端测试真实审计** — Agent 正常返回后跑 LightingMetal 审计
6. **补完 LightingMetal 288 模板页面** — 用 Yaxiio 自动生成
