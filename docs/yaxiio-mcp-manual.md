# 雅溪 Yaxiio — 外部调用手册

> 版本 v1.01 | 2026-05-25 | LightingMetal 智能调度系统

---

## ⚠️ 地址说明（必读）

### 公网访问（推荐）

通过宿主机 Nginx 反向代理，所有 Yaxiio 服务统一通过子域名对外：

```
http://yaxiio.lightingmetal.com           → Dashboard + Blackboard + MCP
ws://yaxiio.lightingmetal.com/ws          → WebSocket 实时推送
```

> ⚠️ **DNS 需配置**：将 `yaxiio.lightingmetal.com` 的 A 记录指向本服务器 IP。
> Nginx 配置已部署：`/etc/nginx/conf.d/yaxiio.lightingmetal.com.conf`

### 完整端口架构

```
互联网 → :80 (宿主机 nginx)
           │
           ├── yaxiio.lightingmetal.com/
           │     ├── /                   → 127.0.0.1:3003  (Dashboard + Blackboard)
           │     ├── /ws                 → 127.0.0.1:3398  (WebSocket)
           │     └── /mcp/l{1-5}/        → 172.17.0.8:340{1-5} (五层MCP)
           │
           └── (Redis 不对外暴露，通过 docker exec 或 API 间接访问)
```

### 各环境访问方式

| 环境 | Dashboard | WebSocket |
|------|-----------|-----------|
| **公网** | `http://yaxiio.lightingmetal.com` | `ws://yaxiio.lightingmetal.com/ws` |
| **宿主机** | `http://localhost:3003` | `ws://localhost:3398` |
| **Docker 网络** | `http://172.17.0.8:3003` | `ws://172.17.0.8:3398` |

## 一、架构总览

Yaxiio 对外暴露三个入口，覆盖"派发任务 → 查看结果 → 实时监控"的完整闭环：

```
┌─────────────────────────────────────────────────────────┐
│               外部调用入口（宿主机端口）                    │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ Redis PubSub │  │ Dashboard    │  │ WebSocket    │  │
│  │ 任务派发      │  │ 报告/指标    │  │ 实时推送     │  │
│  │ docker exec  │  │ :3003        │  │ :3398        │  │
│  │ 方式调用     │  │              │  │              │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │           │
│         ▼                 ▼                 ▼           │
│  ┌──────────────────────────────────────────────────┐  │
│  │              Yaxiio 容器内部 (yaxiio.lightingmetal.com)         │  │
│  │                                                    │  │
│  │  Commander ← PubSub → Agents (翻译官/审计官/...)    │  │
│  │       │                                            │  │
│  │       ├── L1 感知 (3401)  ← 五层 MCP 内部端口       │  │
│  │       ├── L2 规划 (3402)                            │  │
│  │       ├── L3 协调 (3403)                            │  │
│  │       ├── L4 执行 (3404)                            │  │
│  │       └── L5 进化 (3405)                            │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 二、入口一：Dashboard API（查报告、看指标）

### 端口

宿主机 `0.0.0.0:3003` ← Docker 映射自 yaxiio 容器 `:3003`

```bash
# 设置你的环境地址（三选一）
export YAXIIO_HOST="localhost"          # 在宿主机上
export YAXIIO_HOST="192.168.1.100"     # 宿主机同网段
export YAXIIO_HOST="172.17.0.8"        # Docker 网络内
```

### 端点清单

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/dashboard/realtime` | GET | 实时指标：Agent状态、任务数、系统健康 |
| `/api/dashboard/trends` | GET | 趋势数据 |
| `/api/dashboard/alerts` | GET | 告警列表 |
| `/api/dashboard/failovers` | GET | 故障转移记录 |
| `/api/blackboard/reports` | GET | 审计报告列表（JSON） |
| `/api/blackboard/reports/<name>` | GET | 获取指定报告 |
| `/api/blackboard/reports/<name>?format=html` | GET | HTML 渲染的报告 |
| `/blackboard` | GET | 黑板浏览页面（HTML） |
| `/dashboard` | GET | 仪表盘可视化页面 |

### 调用示例

```bash
# 获取实时系统状态
curl http://yaxiio.lightingmetal.com/api/dashboard/realtime

# 列出所有审计报告
curl http://yaxiio.lightingmetal.com/api/blackboard/reports

# 查看审计报告（HTML渲染，浏览器直接打开）
http://yaxiio.lightingmetal.com/api/blackboard/reports/site-audit.md?format=html

# 黑板主页（浏览器）
http://yaxiio.lightingmetal.com/blackboard

# 仪表盘（浏览器）
http://yaxiio.lightingmetal.com/dashboard
```

### 响应示例

```json
// GET /api/blackboard/reports
{
  "count": 1,
  "reports": [
    {
      "name": "site-audit.md",
      "size": 7848,
      "modified": "2026-05-25T21:41:09",
      "url": "/api/blackboard/reports/site-audit.md"
    }
  ]
}
```

---

## 三、入口二：Redis PubSub（派发任务）

Yaxiio 使用 Redis Pub/Sub 作为任务总线。Commander 订阅 `lightingmetal:agent:commander`，各 Agent 订阅自己的频道。

### 消息格式（JSON-RPC 风格）

```json
{
  "type": "task",
  "taskId": "唯一任务ID",
  "from": "发送者",
  "to": "commander",
  "replyTo": "回复目标",
  "payload": {
    "action": "动作名称",
    "...": "具体参数"
  }
}
```

### 频道映射

| 频道 | 订阅者 | 用途 |
|------|--------|------|
| `lightingmetal:agent:commander` | Commander | 任务入口（派发到这里） |
| `lightingmetal:agent:审计官` | 审计官 Agent | 审计任务 |
| `lightingmetal:agent:翻译官` | 翻译官 Agent | 翻译任务 |
| `lightingmetal:agent:商务经理` | 商务经理 Agent | 客户接待 |
| `lightingmetal:agent:售前经理` | 售前经理 Agent | 报价生成 |

### 调用示例

```bash
# 方式1：docker exec（从宿主机直接操作 Redis）
docker exec yaxiio redis-cli -a Yaxiio2026 --no-auth-warning PUBLISH \
  lightingmetal:agent:commander \
  '{"type":"task","taskId":"my-task-001","from":"api","to":"commander","replyTo":"api","payload":{"action":"audit_page","url":"https://www.lightingmetal.com/en/industries/power"}}'

# 方式2：从同一 Docker 网络的其他容器调用
redis-cli -h yaxiio.lightingmetal.com -a Yaxiio2026 --no-auth-warning PUBLISH \
  lightingmetal:agent:commander \
  '{"type":"task","taskId":"translate-001","from":"cms","to":"commander","replyTo":"cms","payload":{"action":"translate","text":"热镀锌螺旋地桩","target":"ru"}}'

# 方式3：Python SDK 风格
import redis
r = redis.Redis(host="yaxiio.lightingmetal.com", port=6379, password="Yaxiio2026", decode_responses=True)
r.publish("lightingmetal:agent:commander", json.dumps({
    "type": "task",
    "taskId": "quote-req-001",
    "from": "web-portal",
    "to": "commander",
    "replyTo": "web-portal",
    "payload": {
        "action": "generate_quote",
        "product": "solar-ground-screw",
        "quantity": 5000,
        "destination": "Dubai"
    }
}))

# 方式4：直接发给特定 Agent（P2P，绕过 Commander）
r.publish("lightingmetal:agent:售前经理", json.dumps({
    "from": "商务经理",
    "to": "售前经理",
    "type": "request",
    "taskId": "p2p-001",
    "replyTo": "商务经理",
    "payload": {
        "action": "generate_quote",
        "data": {"product": "solar-ground-screw", "qty": 5000}
    }
}))
```

### 消息类型

| type | 方向 | 用途 |
|------|------|------|
| `task` | API → Commander → Agent | 标准任务派发 |
| `request` | Agent → Agent (P2P) | Agent间直接请求 |
| `response` | Agent → API/Agent | 任务结果回复 |
| `heartbeat` | Agent → Commander | 心跳上报 |
| `heartbeat_check` | Commander → Agent | 心跳检测 |
| `shutdown` | Commander → Agent | 关闭指令 |

### 监听任务结果

```python
import redis, json, threading

r = redis.Redis(host="yaxiio.lightingmetal.com", port=6379, password="Yaxiio2026", decode_responses=True)

def listen_results():
    pubsub = r.pubsub()
    pubsub.subscribe("lightingmetal:agent:api")  # 你的回复频道
    for msg in pubsub.listen():
        if msg["type"] == "message":
            data = json.loads(msg["data"])
            if data.get("type") == "response":
                print(f"任务 {data.get('taskId')}: {data.get('payload',{}).get('status')}")

threading.Thread(target=listen_results, daemon=True).start()

# 派发任务
r.publish("lightingmetal:agent:commander", json.dumps({
    "type": "task", "taskId": "t-001", "from": "api",
    "to": "commander", "replyTo": "api",
    "payload": {"action": "audit_site", "url": "https://www.lightingmetal.com"}
}))

# 主线程等待结果...
```

---

## 四、入口三：WebSocket（实时推送）

### 端口

`3398`（已暴露到宿主机）

### 用途

Commander 通过 WebSocket 向外部推送实时状态变更、任务进度、告警等。

### 连接示例

```javascript
// JavaScript 浏览器端
const ws = new WebSocket("ws://yaxiio.lightingmetal.com");

ws.onopen = () => {
  // 订阅 Commander 状态更新
  ws.send(JSON.stringify({
    type: "subscribe",
    channels: ["commander:status", "task:progress"]
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log("收到推送:", data.channel, data.payload);
};

ws.onclose = () => console.log("WebSocket 断开");
```

```python
# Python
import asyncio, json, websockets

async def listen():
    async with websockets.connect("ws://yaxiio.lightingmetal.com") as ws:
        await ws.send(json.dumps({
            "type": "subscribe",
            "channels": ["commander:status"]
        }))
        async for msg in ws:
            print(json.loads(msg))

asyncio.run(listen())
```

---

## 五、五层内部 MCP（进阶）

### 端口分配

| 层 | 端口 | 工具数 | 核心能力 |
|----|:----:|:------:|---------|
| L1 感知 | 3401 | 3 | 意图识别、关键词提取、去重检测 |
| L2 规划 | 3402 | 3 | 任务拆解、策略选择、Skill列表 |
| L3 协调 | 3403 | 4 | Agent调度、负载均衡、崩溃上报 |
| L4 执行 | 3404 | 4 | 任务执行、Agent启动、状态查询、沙箱执行 |
| L5 进化 | 3405 | 6 | 评分、Skill生成、工作流快照、拓扑优化、提示词优化、审计日志 |

> ⚠️ 五层 MCP Server 目前仅定义未启动（端口未暴露到宿主机）。如需使用，在容器内启动各层服务即可。

### JSON-RPC 2.0 协议

所有 MCP 通信遵循 JSON-RPC 2.0 over HTTP：

**请求格式：**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "工具名",
    "arguments": {
      "参数": "值"
    }
  }
}
```

**响应格式：**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [
      {"type": "text", "text": "结果文本"}
    ]
  }
}
```

### 调用示例

```bash
# 初始化握手
curl -s http://127.0.0.1:3401/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":1,"method":"initialize",
  "params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}
}'

# 列出工具
curl -s http://127.0.0.1:3402/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":2,"method":"tools/list"
}'

# L1 感知：分析意图
curl -s http://127.0.0.1:3401/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":3,"method":"tools/call",
  "params":{"name":"analyze_intent","arguments":{"text":"审计俄语页面中文残留并翻译"}}
}'

# L2 规划：拆解任务
curl -s http://127.0.0.1:3402/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":4,"method":"tools/call",
  "params":{"name":"decompose_task","arguments":{"intent":"audit_and_translate","context":{"locale":"ru"}}}
}'

# L4 执行：沙箱执行 Python 代码
curl -s http://127.0.0.1:3404/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":5,"method":"tools/call",
  "params":{"name":"sandbox_exec","arguments":{"code":"print(1+1)"}}
}'

# L5 进化：评分任务
curl -s http://127.0.0.1:3405/ -X POST -H "Content-Type: application/json" -d '{
  "jsonrpc":"2.0","id":6,"method":"tools/call",
  "params":{"name":"score_task","arguments":{"task":"翻译任务","result":"译文","agent_id":"翻译官"}}
}'
```

### 启动五层 MCP（容器内）

```bash
# 在 yaxiio 容器内执行
cd /app/.pi/skills/commander

# 各层独立启动
python3 layers/L1_perception/mcp_server.py &
python3 layers/L2_planning/mcp_server.py &
python3 layers/L3_coordination/mcp_server.py &
python3 layers/L4_execution/mcp_server.py &
python3 layers/L5_evolution/mcp_server.py &

# 或通过 PM2 管理
pm2 start layers/L1_perception/mcp_server.py --name mcp-L1 --interpreter python3
pm2 start layers/L2_planning/mcp_server.py --name mcp-L2 --interpreter python3
pm2 start layers/L3_coordination/mcp_server.py --name mcp-L3 --interpreter python3
pm2 start layers/L4_execution/mcp_server.py --name mcp-L4 --interpreter python3
pm2 start layers/L5_evolution/mcp_server.py --name mcp-L5 --interpreter python3
```

---

## 六、Browser Harness MCP（浏览器自动化）

### 调用方式

Browser Harness 通过 **stdio** 通信（stdin 写入请求，stdout 读取响应），适合作为子进程调用。

### 工具列表

| 工具 | 说明 |
|------|------|
| `browser_navigate` | 导航到指定 URL |
| `browser_screenshot` | 截取页面截图 |
| `browser_click` | 点击元素 |
| `browser_fill` | 填写表单 |
| `browser_evaluate` | 执行 JavaScript |
| `browser_snapshot` | 获取页面可访问性快照 |
| `browser_take_screenshot` | 全页截图（返回 base64） |

### Python 调用示例

```python
import subprocess, json

# 启动 Browser Harness 进程
proc = subprocess.Popen(
    ["python3", "/app/.pi/skills/commander/mcp_servers/browser_harness.py"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True
)

# 1. 初始化
init = json.dumps({
    "jsonrpc": "2.0", "id": 0, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "auditor", "version": "1.0"}}
})
proc.stdin.write(init + "\n"); proc.stdin.flush()
resp = proc.stdout.readline()
print("INIT:", resp[:200])

# 2. 导航到页面
nav = json.dumps({
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "browser_navigate",
               "arguments": {"url": "https://www.lightingmetal.com/en"}}
})
proc.stdin.write(nav + "\n"); proc.stdin.flush()
resp = proc.stdout.readline()
print("NAV:", resp[:500])

# 3. 截图
shot = json.dumps({
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "browser_take_screenshot", "arguments": {}}
})
proc.stdin.write(shot + "\n"); proc.stdin.flush()
resp = proc.stdout.readline()
result = json.loads(resp)
# result["result"]["content"][0]["text"] 包含 base64 图片

# 4. 关闭
proc.terminate()
```

---

## 七、完整使用场景

### 场景1：全站审计

```
外部系统 → Redis PubSub → Commander → 审计官 Agent
                                      ↓
                            读取页面代码 → 分析 → 生成报告
                                      ↓
                            写入 Blackboard → Dashboard API 对外暴露
```

```bash
# 1. 派发审计任务
docker exec yaxiio redis-cli -a Yaxiio2026 PUBLISH lightingmetal:agent:commander \
  '{"type":"task","taskId":"audit-002","from":"ci","to":"commander","replyTo":"ci","payload":{"action":"full_site_audit","codebase":"/app/lightingmetal/customer-portal"}}'

# 2. 查看报告列表
curl http://yaxiio.lightingmetal.com/api/blackboard/reports

# 3. 读取最新报告
curl http://yaxiio.lightingmetal.com/api/blackboard/reports/site-audit.md
```

### 场景2：批量翻译

```bash
docker exec yaxiio redis-cli -a Yaxiio2026 PUBLISH lightingmetal:agent:commander \
  '{"type":"task","taskId":"trans-batch-001","from":"cms","to":"commander","replyTo":"cms","payload":{"action":"batch_translate","source":"zh","targets":["en","ru","ar","es","fr"],"texts":["热镀锌螺旋地桩","光伏支架系统"]}}'
```

### 场景3：生成报价

```python
import redis, json, time

r = redis.Redis(host="yaxiio.lightingmetal.com", port=6379, password="Yaxiio2026", decode_responses=True)

# 派发报价任务
task_id = f"quote-{int(time.time())}"
r.publish("lightingmetal:agent:commander", json.dumps({
    "type": "task", "taskId": task_id, "from": "web",
    "to": "commander", "replyTo": "web",
    "payload": {
        "action": "generate_quote",
        "customer": "ABC Construction Ltd",
        "items": [
            {"product": "ground-screw-76x1600", "qty": 3000},
            {"product": "solar-bracket-41x52", "qty": 500}
        ],
        "destination": "Saudi Arabia"
    }
}))
print(f"任务已派发: {task_id}")
```

---

## 八、端口映射总览

| 端口 | 协议 | 用途 | 外网 |
|:----:|------|------|:----:|
| 3003 | HTTP | Dashboard API + Blackboard | ✅ |
| 3398 | WebSocket | 实时推送 | ✅ |
| 3399 | TCP | 内部扩展端口 | ✅ |
| 7681 | HTTP | ttyd 终端 | ✅ |
| 6379 | Redis | 任务消息总线 | ❌ (内部) |
| 3401-3405 | HTTP | 五层 MCP Server | ❌ (内部) |
| 27017 | MongoDB | 持久化存储 | ❌ (内部) |

> 如需暴露内部端口（如五层 MCP），在 `docker run` 或 `docker-compose.yml` 中添加 `-p 3401:3401` 等端口映射。

---

## 九、客户端 SDK（伪代码）

```python
# yaxiio_client.py — 最小化 Yaxiio 客户端
import redis, json, requests

class YaxiioClient:
    def __init__(self, host="yaxiio.lightingmetal.com", redis_port=6379, dash_port=3003,
                 redis_pass="Yaxiio2026"):
        self.host = host
        self.dash_url = f"http://{host}:{dash_port}"
        self.redis = redis.Redis(host=host, port=redis_port,
                                 password=redis_pass, decode_responses=True)

    # ── 任务派发 ──
    def dispatch(self, action: str, **payload) -> str:
        import uuid, time
        task_id = f"{action}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        self.redis.publish("lightingmetal:agent:commander", json.dumps({
            "type": "task", "taskId": task_id, "from": "sdk",
            "to": "commander", "replyTo": "sdk",
            "payload": {"action": action, **payload}
        }))
        return task_id

    # ── 报告查询 ──
    def list_reports(self) -> list:
        return requests.get(f"{self.dash_url}/api/blackboard/reports").json()

    def get_report(self, name: str, fmt="markdown") -> str:
        return requests.get(
            f"{self.dash_url}/api/blackboard/reports/{name}",
            params={"format": fmt}
        ).text

    # ── 系统状态 ──
    def status(self) -> dict:
        return requests.get(f"{self.dash_url}/api/dashboard/realtime").json()

# 使用示例
yaxiio = YaxiioClient(host="yaxiio.lightingmetal.com")

# 派发任务
tid = yaxiio.dispatch("audit_page", url="https://www.lightingmetal.com/en/industries/power")
print(f"任务: {tid}")

# 查看报告
for r in yaxiio.list_reports()["reports"]:
    print(f"  {r['name']} ({r['size']} bytes)")

# 系统状态
status = yaxiio.status()
print(f"Redis: {status['system']['redis_status']}")
```

---

## 十、快速检查清单

```bash
# 1. 确认 Yaxiio 存活
curl -s http://yaxiio.lightingmetal.com/api/dashboard/realtime | python3 -m json.tool

# 2. 确认 Commander 在监听
docker exec yaxiio redis-cli -a Yaxiio2026 PUBSUB NUMSUB lightingmetal:agent:commander

# 3. 确认活跃 Agent
docker exec yaxiio redis-cli -a Yaxiio2026 SMEMBERS commander:agents:active

# 4. 查看守护日志
docker exec yaxiio tail -5 /opt/commander/guard.log

# 5. 查看 PM2 进程
docker exec yaxiio pm2 list
```

---

> 文档维护：每次 Yaxiio 版本升级时同步更新


## 附：MCP 工具清单

### Browser Harness (Playwright)
| 工具 | 说明 |
|------|------|
| browser_navigate | 导航到URL |
| browser_click | 点击元素 |
| browser_type | 输入文本 |
| browser_screenshot | 截图 |
| browser_extract_text | 提取页面文本 |
| browser_extract_links | 提取所有链接 |
| browser_extract_html | 提取页面HTML |
| browser_evaluate | 执行JavaScript |
| browser_wait | 等待元素 |
| browser_scroll | 滚动页面 |
| browser_get_url | 获取当前URL |
| browser_get_title | 获取页面标题 |

### MongoDB MCP
| 工具 | 说明 |
|------|------|
| (标准 @anthropic/mcp-server-mongodb 工具集) | MongoDB 查询和操作 |

> 注册方式：`redis-cli HSET mcp:registry <name> '<json>'`
