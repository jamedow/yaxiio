# Yaxiio 会话上下文 — 2026-05-30

> 状态: ✅ L1→L5 全链路真实LLM审计通过 | 准备后续开发

---

## 一、快速接续

```bash
# 系统已在运行,PM2管理
docker exec yaxiio pm2 status
docker exec yaxiio redis-cli -a Yaxiio2026 PUBSUB NUMSUB yaxiio:agent:commander

# 提交测试任务
docker exec yaxiio python3.12 -c "
import redis, json
r = redis.Redis(host='127.0.0.1', port=6379, password='Yaxiio2026', decode_responses=True)
r.publish('yaxiio:agent:commander', json.dumps({
    'type': 'task', 'taskId': 'test-001',
    'payload': {'action': 'site_audit', 'task': '审计LightingMetal', 'target': 'power',
                 'codebase': '/opt/lightingMetal/customer-portal'}
}))
"

# 查看结果
docker exec yaxiio redis-cli -a Yaxiio2026 GET 'yaxiio:task:test-001' | python3.12 -m json.tool

# 查看 Stream 消息
docker exec yaxiio redis-cli -a Yaxiio2026 XLEN 'yaxiio:stream:L4'
```

---

## 二、本次会话核心成果

### 管道验证
```
Gateway → Commander → Recon → WarmupGate → L1→L2→L3→AsyncOrch
  → Stream L4 → Neuron → DeepSeek LLM → Stream L4_response
  → Commander → L5 → Flywheel → DONE ✅
```

### 新增模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 侦察框架 | `task_recon.py` | 4维可插拔, ms级探测体量/范围/复杂度/风险 |
| 预热门控 | `warmup_gate.py` | 小样本策略寻优, L5评分门控, 3轮重试 |
| 心跳进度 | `neuron.py` | Agent主动汇报进度, Commander动态延长超时 |
| Stream全链路 | `stream_bridge.py` | 替代Pub/Sub, 消息持久化, Consumer Group |
| API Key管理 | `yaxiio.py` | Redis统一管理, 空Key告警, 故障转移 |
| GC回收器 | `yaxiio_gc.py` | 自动清理Stream/任务/进度/僵尸进程 |

### 修复的Bug (9个)

| Bug | 根因 | 修复 |
|-----|------|------|
| BoundedThreadPool死锁 | with self._lock内重复acquire | 删除内部acquire |
| Guardian KeyError | evaluate()返回key不一致 | 统一overall |
| _run_delegated NameError | trace_id作用域缺失 | 添加变量定义 |
| Redis API Key丢失 | 未持久化到Redis | spawn_neuron从Redis读取 |
| Neuron task_timeout | 未初始化默认值 | self.task_timeout=300 |
| Neuron SKILL_DIR | /app路径不存在 | →/opt/yaxiio/.pi/skills |
| Pipe buffer阻塞 | 默认4KB | fcntl 1MB + PYTHONUNBUFFERED |
| Pub/Sub消息丢失 | 瞬态消息 | Stream全链路迁移 |
| 旧API Key 401 | 环境变量中旧key | Redis优先 + 验证 |

---

## 三、关键经验教训

### 1. 容器内修改要立即 docker cp + git commit
`/opt/commander/` 不在 bind mount 中, 容器重启后修改丢失。
**教训**: 修改后立刻 `docker cp` + `git commit` + `docker commit`。

### 2. `pkill -f` 会杀掉自己的 shell
`pkill -f 'neuron.py'` 匹配到 bash 进程自身时会导致整个命令中断。
**教训**: 用 `docker exec yaxiio pkill ...` 分步执行, 或加 `|| true`。

### 3. API Key 必须集中管理 + 健康检查
Key 为 None 时系统静默失败, 没有告警。
**教训**: 所有 Key 从 Redis 读取, 分配前验证, 空值立即告警。

### 4. Stream 积压需要 GC
69条消息68条pending, 系统无清理机制。
**教训**: 所有资源都需要 TTL 或 GC。

### 5. Commander 进程可能比代码旧
`pm2 restart` 不一定重启 Commander (Guardian检测到已在运行就跳过)。
**教训**: 必要时 `kill -9` + 清 pycache。

### 6. 写日志比猜原因快 100 倍
浪费大量时间猜测, 不如直接加文件日志。
**教训**: 任何卡点先加 `open('/tmp/debug.log','a').write(...)`。

---

## 四、当前配置速查

| 配置 | 值 | 位置 |
|------|-----|------|
| API Key | `sk-3496ec4c7cbb471d818670ae80cfba39` | Redis: `yaxiio:config:llm_api_key` |
| API Key备用 | 同上 | Redis: `yaxiio:config:llm_api_key_backup` |
| Redis密码 | `Yaxiio2026` | 环境变量 |
| Stream L4 | `yaxiio:stream:L4` | CG: `agents-L4` |
| Stream响应 | `yaxiio:stream:L4_response` | CG: `commander-response` |
| Stream入口 | `yaxiio:stream:task_incoming` | CG: `commander-main` |
| Agent进度 | `agent:{name}:{task_id}:state` | TTL 3600 |
| 侦察报告 | `yaxiio:recon:{task_id}` | TTL 86400 |
| GC间隔 | `YAXIIO_GC_INTERVAL=300` | 5分钟 |
| thinking默认 | medium | ModelConfig |

---

## 五、后续开发方向

1. **Agent 输出格式标准化** — 当前 L5 评分只有 5.0(规则), 需要让 Agent 输出可被 L5 解析的格式
2. **Gateway 加入 PM2** — 容器重启自动拉起
3. **预热策略锦标赛** — 多策略并行试跑 + 自动择最优
4. **Agent 私有笔记持久化** — 跨任务经验复用
5. **Commander 监控面板** — 实时查看任务状态/Stream积压/Neuron健康
6. **LightingMetal 288模板补完** — 用 Yaxiio 自动生成
