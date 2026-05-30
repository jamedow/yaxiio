# JVM 设计思想 → Yaxiio 落地评估

> 日期: 2026-05-30 | 方法: 逐项可行性分析 + 风险矩阵 + 优先级排序

---

## 零、评估框架

每项按五个维度打分（1-5）：

| 维度 | 含义 |
|------|------|
| **收益** | 对 Yaxiio 核心竞争力的提升程度 |
| **可行性** | 当前代码基础能否支撑，技术栈是否匹配 |
| **改动量** | 实现需要多少代码改动 |
| **风险** | 引入 bug 或破坏现有功能的可能性 |
| **紧迫性** | 是否应该立刻做 |

综合评分 = 收益 × 2 + 可行性 + (5-改动量) + (5-风险) + 紧迫性

---

## 一、ClassLoader 分层委托 → SkillClassLoader

### 1.1 JVM 原始设计

```
Bootstrap ClassLoader (rt.jar)
    ↑ 委派
Extension ClassLoader (lib/ext)
    ↑ 委派
Application ClassLoader (classpath)
```

**三个核心价值**：
1. **安全模型**：核心类不可被替换（双亲委派）
2. **命名空间隔离**：同名类在不同 ClassLoader 中是不同类
3. **动态加载**：运行时加载新类，不重启 JVM

### 1.2 Yaxiio 现状

```python
# 当前 SkillLoader — 扁平加载，无层级
class SkillLoader:
    def _load(self):
        for name in os.listdir(SKILL_DIR):
            with open(f"{SKILL_DIR}/{name}/SKILL.md") as f:
                self.skills[name] = f.read()  # 所有 Skill 平铺在一个 dict 里
```

**问题**：
- 医疗行业的 `blood_pressure` 术语可能覆盖通用术语
- 加载 50 个 Skill 时无法区分哪些是核心、哪些是行业扩展
- 没有 Skill 版本管理

### 1.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | 4 | Skill 是 Yaxiio 的生态入口，隔离和版本管理是走向市场的前提 |
| 可行性 | 4 | Python 的动态导入和命名空间天然支持，不需要 hack |
| 改动量 | 3 | 需要重写 `SkillLoader`，约 200 行 |
| 风险 | 2 | 新模块，不改现有接口，可并行运行 |
| 紧迫性 | 3 | Phase 2 后期做，当前 Skill 数量少，冲突不明显 |

**综合: 4×2 + 4 + 2 + 3 + 3 = 20**

### 1.4 建议

✅ **做**。但分两步：
1. 先在能力卡片中加 `namespace` 字段和 `inherits` 字段（改动小）
2. Phase 2 后期重写 `SkillLoader` 为分层委托模型

---

## 二、字节码校验 → 宪法语义化升级

### 2.1 JVM 原始设计

JVM 的四道校验是强制的——任何一道不通过，类就拒绝加载：

```
Pass 1: 结构检查 → 魔数、版本号、常量池
Pass 2: 语义检查 → 继承关系、final 约束、接口实现
Pass 3: 字节码校验 → 类型安全、栈深度、变量初始化
Pass 4: 符号引用 → 类/方法/字段存在性、访问权限
```

### 2.2 Yaxiio 现状

```python
# 当前宪法 — 字符串匹配
DANGEROUS_PATTERNS = [
    "docker exec", "rm -rf", "eval(", ...
]
if pattern in payload_str:
    return Verdict.DEGRADED
```

**问题**：
- 攻击者用 `eval (  )`（加空格）就能绕过
- 没有检查 Agent 输出的结构完整性
- 没有检查 Agent 是否越权

### 2.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | **5** | 宪法是 Yaxiio 最大护城河。从字符串匹配升级到结构化校验，护城河加深一个数量级 |
| 可行性 | 5 | 校验逻辑可以完全独立实现，不改宪法现有接口 |
| 改动量 | 2 | 新增 `SemanticConstitutionVerifier` 类，约 150 行 |
| 风险 | 1 | 新增校验器，不影响现有审查链。校验器可以 feature-flag 控制 |
| 紧迫性 | **5** | P0。宪法是安全核心，当前字符串匹配可被绕过 |

**综合: 5×2 + 5 + 3 + 4 + 5 = 27** ← 最高优先级

### 2.4 建议

✅ **立刻做**。这是本次评估中综合得分最高的项目。

具体实现：
```python
# constitution.py 中增加
class SemanticConstitutionVerifier:
    def verify(self, action, payload, agent_card):
        issues = []
        issues.extend(self._check_structure(payload, agent_card))
        issues.extend(self._check_semantics(action, payload))
        issues.extend(self._check_safety(payload))
        return issues

# 在 review() 中增加一步
def review(self, action, payload):
    # ... 现有逻辑 ...
    semantic_issues = self.verifier.verify(action, payload, card)
    if semantic_issues:
        return Verdict.DEGRADED, f"语义校验失败: {semantic_issues[0]}"
```

---

## 三、JIT 编译 → 模型自适应路由

### 3.1 JVM 原始设计

HotSpot 的分层编译是 JIT 的巅峰：

```
Level 0: 解释执行 → 收集调用次数、分支概率
Level 1-3: C1 编译 → 快速编译 + profiling
Level 4: C2 编译 → 深度优化（内联、逃逸分析、循环展开）

关键机制:
- 热点探测: 被调用 > CompileThreshold 次才升级
- 逆优化 (Deoptimization): C2 假设被打破时退回解释执行
- OSR: 长循环中途切换编译版本
```

### 3.2 Yaxiio 现状

```python
# 当前 ModelRouter — 静态关键词匹配
RULES = {
    "complex": {"model": "deepseek-v4-pro", "keywords": ["分析","拆解","优化","审计"]},
    "stable":  {"model": "deepseek-v4-pro", "keywords": ["修复","创建","生成"]},
    "fast":    {"model": "deepseek-v4-flash", "keywords": ["翻译","查询","检查"]},
}
```

**问题**：
- 一次匹配定终身，不会根据实际表现调整
- "翻译"类任务统一用 Flash，但复杂翻译可能需要 High
- 没有退路：如果选了错误的模型，整个任务就错了

### 3.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | 4 | 直接降低模型成本（优先用便宜模型）+ 提升质量（不达标自动升级） |
| 可行性 | 5 | `model_router_v2.py` 已经有多目标评分框架，加统计追踪即可 |
| 改动量 | 1 | 改动极小——约 50 行代码 |
| 风险 | 1 | 新增统计数据和自适应逻辑，不影响现有路由 |
| 紧迫性 | 4 | 成本优化 + 质量提升，两者兼备 |

**综合: 4×2 + 5 + 4 + 4 + 4 = 25**

### 3.4 建议

✅ **立刻做**。改动最小、收益最大。

具体实现：
```python
# model_router_v2.py 中增加
class AdaptiveModelRouter(IntelligentModelRouter):
    def __init__(self):
        super().__init__()
        self.perf = {}  # task_type → {model: {success, total, avg_score}}
    
    def select(self, task, constraints=None):
        task_type = task.get("action", "general")
        stats = self.perf.get(task_type, {})
        
        # 热点探测: 优先用历史上成功率最高的模型
        best = self._best_performer(task_type)
        if best and stats[best]["success_rate"] > 0.85:
            return self._build_config(best, ...)
        
        # 新任务类型: 从 Flash 开始
        return self._build_config("deepseek-flash", ...)
    
    def record(self, task_type, model, score):
        # 记录 → 逆优化判断
        ...
```

---

## 四、分代垃圾回收 → Agent 主动回收

### 4.1 JVM 原始设计

分代 GC 基于弱分代假说：绝大多数对象朝生夕死。

```
Eden → Minor GC (频繁, ms 级)
Survivor → 熬过 15 次晋升
Old Gen → Major GC (偶尔, 秒级)
```

### 4.2 Yaxiio 现状

```python
# 当前: Guardian 被动检测 Agent 崩溃
# 四象限定义了生命周期但没有主动回收
class LifecycleManager:
    def get_quadrant(self, role):
        if role in core: return CORE
        ...
```

**问题**：
- Ephemeral Agent 完成任务后不主动销毁，占着内存
- Strategic Agent 闲置后不会自动休眠
- 没有类似 GC 的"碎片整理"——长期运行后资源碎片化

### 4.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | 3 | 资源优化，但对当前规模（<50 Agent）不紧迫 |
| 可行性 | 3 | 需要新增后台 GC 线程，增加系统复杂度 |
| 改动量 | 4 | 大。需要 GC 调度器、安全点机制、晋升策略 |
| 风险 | 4 | 高。GC 错误可能杀死正在执行任务的 Agent |
| 紧迫性 | 2 | 当前规模不需要。100+ Agent 时再考虑 |

**综合: 3×2 + 3 + 1 + 1 + 2 = 13**

### 4.4 建议

⚠️ **暂缓**。但可以做一个简化版——给 Guardian 增加定期清理闲置 Agent 的能力（不需要完整的分代 GC）。

```python
# Guardian 中增加轻量清理
def _cleanup_idle_agents(self):
    """简化版 GC: 每 5 分钟清理闲置 Ephemeral Agent"""
    for agent in self.list_agents("ephemeral"):
        if agent.idle_time > 300:
            self.destroy(agent)
```

---

## 五、栈上分配 → 进程内存优化

### 5.1 JVM 原始设计

逃逸分析：如果一个对象不会逃逸出当前方法，分配在栈上而非堆上——零 GC 开销。

```java
// 这个 Point 对象不会逃逸 → 栈上分配
void foo() {
    Point p = new Point(1, 2);
    System.out.println(p.x);
}  // p 随栈帧销毁
```

### 5.2 Yaxiio 现状

所有 Agent 上下文走 Redis——即使 Template Clone 的 Agent 上下文完全不会共享。

### 5.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | 2 | Redis 已经够快。进程内存 vs Redis 的差异在亚毫秒级 |
| 可行性 | 2 | 需要判断"逃逸"——复杂且容易出错 |
| 改动量 | 4 | 大。需要修改所有上下文读写路径 |
| 风险 | 3 | 中。逃逸判断错误会导致数据丢失 |
| 紧迫性 | 1 | 完全不是瓶颈 |

**综合: 2×2 + 2 + 1 + 2 + 1 = 10**

### 5.4 建议

❌ **不做**。这是过度优化。当前 Redis 延迟不是瓶颈。等 Yaxiio 需要支撑 1000+ Agent 时再考虑。

---

## 六、JMX/JFR → /metrics 增强

### 6.1 JVM 原始设计

JMX 暴露了数百个 MBean：内存池使用率、GC 次数和时间、线程数、类加载数、编译统计...

JFR 提供低开销的事件记录：方法调用、锁竞争、IO 操作、异常抛出...

### 6.2 Yaxiio 现状

```python
# 当前 /metrics — 只有 6 个指标
_M = {
    "yaxiio_tasks_total": 0,
    "yaxiio_tasks_failed": 0,
    "yaxiio_llm_calls": 0,
    ...
}
```

### 6.3 落地评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 收益 | 3 | 运维和调试的刚需。现在出问题只能看日志 |
| 可行性 | 5 | 已经在用 Prometheus 格式，只需增加指标 |
| 改动量 | 1 | 极小——每个模块加几行计数器 |
| 风险 | 1 | 零风险 |
| 紧迫性 | 3 | 短期不致命，但越早加越好 |

**综合: 3×2 + 5 + 4 + 4 + 3 = 22**

### 6.4 建议

✅ **立刻做**。改动最小，运维价值大。

需要增加的指标：
```
yaxiio_agents_active          # 活跃 Agent 数
yaxiio_agents_by_quadrant     # 按四象限分布
yaxiio_tasks_queue_depth      # 任务队列深度
yaxiio_l5_score_distribution  # L5 评分分布
yaxiio_model_usage            # 模型使用统计（Flash/High/Max）
yaxiio_agent_credit_avg       # Agent 平均信用分
```

---

## 七、优先级总览

| # | 项目 | 综合分 | 决策 | 理由 |
|---|------|--------|------|------|
| 2 | 宪法语义化升级 | **27** | ✅ 立刻做 | 护城河加深，改动小，风险低 |
| 3 | 模型自适应路由 | **25** | ✅ 立刻做 | 改动最小，成本+质量双赢 |
| 6 | /metrics 增强 | **22** | ✅ 立刻做 | 改动最小，运维刚需 |
| 1 | SkillClassLoader | **20** | ✅ Phase 2 | 生态基础，当前规模不紧迫 |
| 4 | 分代 Agent GC | **13** | ⚠️ 简化版 | 先做轻量清理，不做完整 GC |
| 5 | 栈上分配 | **10** | ❌ 不做 | 过度优化，Redis 不是瓶颈 |

---

## 八、立刻执行的三项

### 8.1 宪法语义化 (P0, ~150行)

在 `constitution.py` 同级新建 `constitution_verifier.py`，包含四道校验。`review()` 方法中增加语义校验步骤。

### 8.2 模型自适应路由 (P0, ~50行)

在 `model_router_v2.py` 中增加 `record()` 方法和统计追踪。`select()` 根据历史表现自动选择模型。

### 8.3 /metrics 增强 (P0, ~30行)

在 `prometheus_metrics.py` 中增加 6 个新指标。`gateway.py` 的 `/metrics` 端点输出新指标。

---

> **一句话**: JVM 的核心智慧是"分层"——ClassLoader 分层隔离、JIT 分层编译、GC 分代回收。Yaxiio 最缺的是宪法的分层校验——从字符串匹配升级为四道语义校验。这是立刻要做的第一件事。
