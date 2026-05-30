# Yaxiio 开发手册

> 版本: 1.0 | 日期: 2026-05-30
> 沉淀本次会话的核心编程思想和工程规范

---

## 一、编程思想

### 1.1 "先测后改" — 重构的第一原则

**来源**: Michael Feathers "Working Effectively with Legacy Code"

**原则**: 给要改的代码先写测试，验证当前行为。改完后跑测试，确认行为不变。

**Yaxiio 实践**:
```bash
# 每次重构前
python3 tests/test_core.py        # 17 tests — 宪法 + 状态机
python3 tests/test_workflow.py    # 97 tests — 工作流引擎
python3 tests/test_l1_handler.py  # 11 tests — L1 处理器

# 重构后全部通过才算成功
```

**反例**: 我们提取 workflow_engine 时，`_get_llm` 被误删——如果有测试覆盖，立刻就能发现。

### 1.2 "A+C 模式" — 纯函数 + 委托

**原则**: 每次只提取一个方法。先提取为纯函数（C），再封装为委托类（A）。

```
旧代码:
  class WorkflowEngine:
      def _do_L1(self, ...):  # 25 行内联逻辑

重构后:
  # C: 纯函数 (workflow_l1.py)
  def analyze_intent_l1(payload): ...  
  def resolve_intent(l1_result, payload): ...
  
  # A: 委托封装 (workflow_l1.py)
  class L1Handler:
      def analyze(self, task_id, payload):
          return resolve_intent(analyze_intent_l1(payload), payload)
  
  # WorkflowEngine 中:
  def _do_L1(self, task_id, payload):
      return self.l1_handler.analyze(task_id, payload)
```

**收益**: 
- 纯函数可独立测试（不需要 mock WorkflowEngine）
- 委托类保持 `self.xxx` 便利性，但 `self` 范围缩小到单层
- 修改 L1 不影响 L4

### 1.3 "防呆不是限制，是帮你填好了" — 渐进式信息披露

**原则**: 防呆 ≠ 权限控制。不是"你不能调"，而是"你不用调，我已经帮你调好了"。

**Yaxiio 实践**: 四层复杂度暴露（不是 RBAC）
```python
COMPLEXITY_TIERS = {
    1: {"name": "平民级", "auto_fill": True},   # 4 个字段
    2: {"name": "维护级", "auto_fill": True},   # 10 个字段
    3: {"name": "技术级", "auto_fill": False},  # 20 个字段
    4: {"name": "大神级", "auto_fill": False},  # 全部
}
```

**参考**: VSCode 设置面板 vs settings.json，macOS 系统偏好 vs defaults 命令，相机自动模式 vs 手动模式。

### 1.4 "Agent = 能调用 LLM 的软件" — 本质定义

**原则**: 把复杂概念回归到最简定义。Agent 不神秘——它就是一个软件，唯一的区别是决策逻辑由 LLM 实时生成而非程序员写死。

**三个新问题**: LLM 引入不确定性、状态管理、任务拆解。Yaxiio 就是解决这三个问题的运行时。

### 1.5 "Yaxiio = Agent 运行时" — JVM 启发

**原则**: Yaxiio 不是工具箱（LangChain），不是施工队（Ruflo），是运行时（JVM）。

**参考设计**:
- ClassLoader → Skill 热加载+版本隔离
- 字节码校验 → 宪法语义化（四道校验）
- JIT 编译 → 模型自适应路由（热点探测+逆优化）
- 分代 GC → Agent 生命周期主动回收
- JMX/JFR → 可观测性体系

---

## 二、代码规范

### 2.1 方法提取的检查清单

提取一个方法前，确认以下各项：

- [ ] 该方法依赖 `self` 的哪些成员？（列出清单）
- [ ] 这些依赖能否通过参数传入？（如果能 → 纯函数）
- [ ] 如果不能，能否封装到 Handler 类？（`self` 缩小范围）
- [ ] 提取前是否写了测试？（必须）
- [ ] 提取后所有测试是否通过？（必须）

### 2.2 文件大小

| 文件类型 | 最大行数 | 说明 |
|---------|---------|------|
| 编排/主控 | 500 | 超过则拆分 |
| 纯函数模块 | 300 | 超过则拆子模块 |
| 单个函数 | 40 | 超过则拆子函数 |
| 测试文件 | 200 | 每个测试方法 < 30 行 |

### 2.3 命名规范

**禁止**: 单字母变量（`p`, `a`, `tid`）
**推荐**: 描述性名称（`payload`, `action`, `task_id`）

**禁止**: 函数名不揭示意图（`f()`, `do()`）
**推荐**: `analyze_intent_l1()`, `resolve_intent()`, `wait_for_neuron_response()`

### 2.4 错误处理

**禁止**: 空 `except:` 吞掉所有异常
**推荐**: 
```python
try:
    result = call_llm(prompt)
except LLMUnavailableError:
    # 防呆降级
    result = rule_based_fallback()
except Exception as e:
    log.error("LLM call failed", error=str(e))
    raise  # 不吞掉未知异常
```

### 2.5 防呆默认值

任何对外接口的参数必须有默认值：
```python
# ❌
def process(task, timeout): ...

# ✅  
def process(task, timeout=None):
    timeout = timeout or safe_default("task_timeout")
```

---

## 三、架构原则

### 3.1 五层独立

每一层的修改不应影响其他层。验证方式：只运行该层的测试。

### 3.2 宪法不可绕过

任何新增的代码路径必须经过 `constitution.review()`。新增的危险操作必须加入 `FORBIDDEN_DIRECT` 或 `DANGEROUS_PATTERNS`。

### 3.3 纯编排不执行

Commander 只做路由和调度。业务逻辑必须在 L4 的 Agent 进程中执行。

### 3.4 经验飞轮必须闭合

任何新增的评分/评估路径，必须同时考虑：
- 结果如何存入经验库（L0）
- 经验如何被未来的任务检索到（L2）
- 高分如何回写模板（L5）

---

## 四、测试规范

### 4.1 测试金字塔

```
        /\
       /E2E\      端到端: 1-2 个
      /------\
     /集成测试\    集成: 5-10 个
    /----------\
   /  单元测试   \  单元: 100+ 个
  /--------------\
```

### 4.2 必须测试的模块

- `constitution.py` — 安全核心，每条规则必须覆盖
- `task_state_machine.py` — 状态转换合法性
- `unified_scorer.py` — 评分一致性
- `foolproof.py` — 每个校验函数

### 4.3 测试命令

```bash
# 全部测试
python3 tests/test_core.py && python3 tests/test_workflow.py && python3 tests/test_l1_handler.py

# 单文件
python3 tests/test_core.py

# 质量检查
python3 tools/qa_check.py --all
```

---

## 五、Git 规范

### 5.1 Commit 格式

```
type(scope): 简短描述

详细说明（可选）

- 改动点 1
- 改动点 2
```

类型: `feat` `fix` `refactor` `docs` `test` `chore`

### 5.2 每次提交前

```bash
# 1. 跑测试
python3 tests/test_core.py && python3 tests/test_workflow.py && python3 tests/test_l1_handler.py

# 2. 跑质量检查
python3 tools/qa_check.py --changed

# 3. 如果 Commander 在运行，重启加载新代码
# （通过 Guardian 自动重启或手动 kill -15）
```

---

## 六、从本次会话学到的

1. **Shell 转义是坑** — 中文或特殊字符在 `bash -c` 中经常出问题。解决办法：写 `.py` 文件 → `docker cp` → `docker exec python3`
2. **docker exec 的 heredoc 不可靠** — `docker exec cat > file << EOF` 重定向发生在宿主机。解决办法：`docker cp` 从宿主机拷入
3. **Nuxt 动态路由 200 不代表有内容** — 检查页面内容而非 HTTP 状态码
4. **pages-tree.md 的 Markdown 链接会嵌套** — 每次重建 URL 前必须从 git 恢复干净版本
5. **heroTitle 匹配需要上下文** — "横梁连接螺栓"同时存在于 solar-farm 和 hydropower，必须用 sector+l2 限定

---

> **核心理念**: 思从深而行从简。不是"大改一次到位"，而是"每次只改一个方法，改完就跑测试，通过就提交"。
