# Yaxiio 会话上下文 — 2026-05-30/31

> 状态: ✅ 生产部署完成 | L1→L5 全链路验证 | 45个产品页面已上线

---

## 一、快速接续

```bash
# 系统运行在 Docker 容器中, PM2 管理
docker exec yaxiio pm2 status
# Gateway(3398-3399) + Guardian → Commander

# 查看监控面板
docker exec -e REDIS_PASSWORD=Yaxiio2026 yaxiio python3.12 /opt/commander/yaxiio_dashboard.py

# 提交任务
docker exec yaxiio python3.12 -c "
import redis, json
r = redis.Redis(host='127.0.0.1', port=6379, password='Yaxiio2026', decode_responses=True)
r.publish('yaxiio:agent:commander', json.dumps({
    'type': 'task', 'taskId': 'xxx', 'payload': {'action': 'site_audit', 'task': '...', 'codebase': '...'}
}))
"

# 香港生产服务器: 47.79.20.2
# Nuxt容器: docker restart nuxt-app
# 部署: bash /opt/lightingMetal/deploy.sh --hk
```

---

## 二、本次会话成果

### 核心管道
```
Gateway → Commander → Recon → WarmupGate → L1→L2→L3→AsyncOrch
  → Stream L4 → Neuron(审计官/LM内容工程师) → DeepSeek LLM
  → Stream L4_response → Commander → L5 Scoring → Flywheel → DONE
```

### 新增/修复模块

| 模块 | 文件 | 功能 |
|------|------|------|
| 侦察框架 | `task_recon.py` | 4维可插拔(volume/scope/complexity/risk), Commander按action调配 |
| 预热门控 | `warmup_gate.py` | 小样本试跑 + L5评分门控 + 3轮重试 |
| 策略锦标赛 | `warmup_gate.py` | 3策略并行试跑(medium/high/complex), L5择最优 |
| 心跳进度 | `neuron.py` | Agent主动汇报5%/20%/50%/75%, Commander动态延长超时 |
| Stream全链路 | `stream_bridge.py` | 替代Pub/Sub, Consumer Group, ACK, Pending恢复 |
| 评分注册表 | `score_registry.py` | Commander按action动态选维度, Redis热更新 |
| GC回收器 | `yaxiio_gc.py` | 自动清理Stream/任务/进度/僵尸进程, 5分钟间隔 |
| 监控面板 | `yaxiio_dashboard.py` | 任务/Stream/Neuron/GC 实时快照 |
| 健康断言 | `health_assert.py` | APIKey/Stream/Neuron/Score/GC 5项检查 |
| Agent私有笔记 | `neuron.py` | 每任务保存经验到Redis, 跨任务复用 |

### 修复的Bug (11个)

| Bug | 根因 | 修复 |
|-----|------|------|
| BoundedThreadPool死锁 | with self._lock内重复acquire非重入锁 | 删除内部acquire |
| Guardian KeyError | evaluate()返回key不一致(score vs overall) | 统一overall |
| _run_delegated NameError | trace_id闭包作用域缺失 | 添加变量定义 |
| Redis API Key丢失 | 重启后key被清 | 持久化BGSAVE + 备份key |
| Neuron task_timeout | 未初始化默认值 | self.task_timeout=300 |
| Neuron SKILL_DIR | /app路径不存在 | →/opt/yaxiio/.pi/skills |
| Pipe buffer阻塞 | 默认4KB | fcntl 1MB + PYTHONUNBUFFERED |
| Pub/Sub消息丢失 | 瞬态消息 | Stream全链路迁移 |
| 旧API Key 401 | 环境变量中旧key | Redis优先 + 验证 + 故障转移 |
| L4双重industries | API slug带/industries/前缀 + sitemap编译阻塞 | 去重 + 修复编译 |
| sitemap损坏 | _redis/getRedis重复声明阻止编译 | 删除重复15行 |

---

## 三、生产部署配置

| 配置项 | 值 |
|--------|-----|
| HK服务器 | 47.79.20.2 |
| Nuxt容器 | nuxt-app (port 3000) |
| 部署方式 | SCP .output/ → nuxt-app容器 → restart |
| 磁盘 | 30% (清理了大量.output.bak.*) |
| API Key | `sk-3496ec...` (Redis: yaxiio:config:llm_api_key) |
| Stream L4 | `yaxiio:stream:L4` (CG: agents-L4) |
| Stream响应 | `yaxiio:stream:L4_response` (CG: commander-response) |

---

## 四、关键经验教训

### 1. 容器内修改必须立即持久化
`/opt/commander/` 不在bind mount中, 容器重启修改丢失。
**教训**: 改完立刻 `docker cp` + `git commit` + `docker commit`。

### 2. Nuxt容器使用镜像内代码, 非host挂载
HK的nuxt-app容器没有volume mount, 代码在镜像内。
**教训**: 部署 = 重建.output → tar → docker cp进容器 → restart。

### 3. API Key必须集中管理 + 空值告警
Key为None时系统静默失败, 没有任何告警。
**教训**: Redis统一管理 + spawn前验证 + 空值立即告警。

### 4. 写日志比猜原因快100倍
浪费大量时间猜测, 不如直接 `open('/tmp/debug.log','a').write(...)`。
**教训**: 任何卡点先加文件日志。

### 5. Commander进程可能比代码旧
`pm2 restart`不一定重启Commander (Guardian检测到已在运行就跳过)。
**教训**: 必要时 `kill -9` + 清pycache。

### 6. Stream积压需要GC
69条消息68条pending, 系统无清理机制。
**教训**: 所有资源都需要TTL或GC。

---

## 五、后续优化方向

1. **Agent输出格式化** — 让L5能解析JSON获得真实评分(当前5.0→8.3规则分)
2. **LLM Judge接入** — 用LLM评估Agent输出质量
3. **多Agent并行** — 审计官+翻译官+LM内容工程师同时工作
4. **预热策略真实化** — 预热阶段用真实LLM输出而非规则分
5. **CDN缓存清理** — 部署后自动purge CDN
