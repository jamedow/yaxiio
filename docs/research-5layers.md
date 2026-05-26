# Yaxiio 五层架构 — 业界对标与优化方向

> 研究时间: 2026-05 | 对标: LangChain/AutoGPT/CrewAI/DSPy/LlamaIndex

---

## L1 基础组件层

| 现状 | 业界标杆 | 优化方向 |
|------|---------|---------|
| Redis 单机 | Redis Cluster / Dragonfly | **Redis Cluster 分片**：Agent心跳、任务队列、缓存分离到不同节点，避免单点 |
| MongoDB 基本CRUD | LlamaIndex向量存储 | **向量数据库**(Chroma/Qdrant/Milvus)：Agent记忆、RAG检索、语义搜索 |
| MCP stdio/HTTP | ModelContextProtocol 全协议 | **MCP Streamable HTTP**：支持流式响应，长任务不超时 |
| Skill 文件加载 | LangChain Tool 动态注册 | **Skill 热加载**：监听目录变化，无需重启自动注册新 Skill |
| 无图数据库 | Neo4j / Kuzu | **知识图谱**：Agent间关系、任务依赖、能力图谱可视化 |

**优先级**: 向量数据库 > MCP流式 > Skill热加载

---

## L2 智能体层

| 现状 | 业界标杆 | 优化方向 |
|------|---------|---------|
| 静态角色注册 | AutoGPT 动态Agent生成 | **LLM驱动的Agent设计**：AgentDesigner根据任务自动生成角色+能力卡片 |
| Shell进程隔离 | CrewAI 角色协作 | **Agent角色继承**：FrontendEngineer继承CodeAuditor能力，减少重复定义 |
| 单一 LLM 路由 | OpenRouter 多模型路由 | **多Provider路由**：DeepSeek/Claude/GPT按成本+延迟自动切换 |
| 无记忆管理 | MemGPT / Letta | **分层记忆**：工作记忆(Redis)+短期(MongoDB)+长期(向量库)，自动摘要压缩 |
| 无RAG | LlamaIndex / LangChain | **RAG检索增强**：Agent执行任务前从知识库检索相关上下文 |

**优先级**: RAG > 分层记忆 > 多Provider路由

---

## L3 工作流层

| 现状 | 业界标杆 | 优化方向 |
|------|---------|---------|
| 硬编码拆解 | DSPy 自动优化Pipeline | **LLM自优化工作流**：根据历史成功率自动调整拆解策略 |
| 线性依赖分析 | Airflow/Dagster DAG | **条件分支**：子任务支持 if/else，失败自动降级路径 |
| 单步调度 | LangGraph 状态图 | **状态机调度**：每个任务有明确状态(PENDING→RUNNING→SUCCESS/FAIL→RETRY) |
| 无并行 | Ray/Dask 分布式 | **并行执行**：无依赖子任务同时分发到不同Agent |
| 无断点续传 | Temporal 工作流引擎 | **检查点恢复**：长时间任务中断后从上次状态继续 |

**优先级**: 状态机 > 并行执行 > 条件分支

---

## L4 评估层

| 现状 | 业界标杆 | 优化方向 |
|------|---------|---------|
| 简单算术评分 | LLM-as-Judge (MT-Bench) | **LLM裁判**：用强模型评估弱模型输出，多维度打分 |
| 本地JSON日志 | LangSmith / Weave 追踪 | **全链路追踪**：每次LLM调用、工具执行、Agent决策的完整trace |
| 阈值重启 | RLHF 强化学习反馈 | **强化学习信号**：评估结果反馈到L5进化层，形成闭环优化 |
| 无回归测试 | Chromatic/Percy 视觉回归 | **自动化回归**：代码修改后自动截屏对比，检测UI退化 |
| 单点检测 | Prometheus + Grafana | **指标仪表盘**：实时Agent性能、Token消耗、错误率监控 |

**优先级**: LLM裁判 > 全链路追踪 > 自动化回归

---

## L5 进化层

| 现状 | 业界标杆 | 优化方向 |
|------|---------|---------|
| 单文件补丁 | DSPy 自动优化 | **DSPy编译器**：自动优化prompt结构，找最优的few-shot示例 |
| 简单A/B计数 | Google/VWO 统计框架 | **统计显著性**：卡方检验/t-test，不是简单比大小 |
| 无TextGrad | TextGrad 文本梯度 | **文本梯度下降**：用LLM生成"梯度"文本指导prompt改进方向 |
| 手动技能生成 | AutoGPT Plugin系统 | **自动技能提炼**：Agent成功完成新任务后自动生成Skill模板 |
| 独立进化 | OpenAI Swarm 群体进化 | **群体进化**：多个沙箱并行进化，优胜劣汰 |

**优先级**: DSPy编译器 > 统计显著性 > TextGrad

---

## 实施路线图

| 阶段 | 层 | 项目 | 预期收益 |
|:----:|:--:|------|:-------:|
| **即刻** | L4 | LLM-as-Judge 评分 | 评估质量 ↑300% |
| **本周** | L3 | 状态机调度 | 任务可靠性 ↑ |
| **本周** | L1 | Skill热加载 | 运维效率 ↑ |
| **本月** | L2 | RAG检索增强 | Agent回答质量 ↑ |
| **本月** | L5 | DSPy自动优化 | Prompt质量 ↑↑ |
| **季度** | L1 | 向量数据库 | 记忆+语义搜索 |
| **季度** | L3 | 并行执行 | 吞吐量 ↑3-5x |

---

> 生成: Yaxiio Research | 写入: blackboard/reports/research-5layers.md
