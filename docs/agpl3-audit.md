# Yaxiio AGPLv3 兼容性审计

> AGPLv3 是强 Copyleft：衍生作品必须同协议开源，网络服务也需提供源码

## ✅ 兼容（可直接用）

| 方案 | 许可证 | 说明 |
|------|--------|------|
| Chroma 向量库 | Apache 2.0 | ✅ 宽松，兼容 |
| Qdrant 向量库 | Apache 2.0 | ✅ |
| Milvus 向量库 | Apache 2.0 | ✅ |
| LlamaIndex RAG | MIT | ✅ |
| LangChain RAG | MIT | ✅ |
| DSPy 编译器 | MIT | ✅ |
| TextGrad | MIT | ✅ |
| CrewAI | MIT | ✅ |
| MemGPT/Letta | Apache 2.0 | ✅ |
| LangGraph | MIT | ✅ |
| Airflow/Dagster | Apache 2.0 | ✅ |
| Temporal | MIT | ✅ |
| Prometheus+Grafana | Apache 2.0 | ✅ |
| Weave (W&B) | MIT | ✅ |
| Neo4j Community | GPLv3 | ✅ AGPLv3兼容 |

## ❌ 不兼容（不能直接集成）

| 方案 | 许可证 | 原因 | 替代 |
|------|--------|------|------|
| **Redis Cluster** | SSPLv1/RSALv2 | SSPL不是开源协议，AGPLv3不兼容 | **Valkey** (BSD) |
| **Dragonfly** | BSL | Business Source License，非开源 | **Valkey** |
| **LangSmith** | 专有 | 闭源SaaS | **Weave** (MIT) 或自建 |
| **Neo4j Enterprise** | 专有 | 闭源 | **Neo4j Community** (GPLv3) |

## 🟡 需要注意

| 方案 | 许可证 | 注意 |
|------|--------|------|
| **DSPy** | MIT | ✅ 可以，但他们的 `BootstrapFewShot` 用了 OpenAI 专有API做标注——自己实现标注器即可 |
| **OpenAI/Claude API** | 服务条款 | ⚠️ AGPLv3只管代码，不管调用的外部API。但你的prompt和输出策略属于你的代码 |
| **Playwright** | Apache 2.0 | ✅ Chromium是BSD，完全兼容 |

## 总结

**需要换的只有 Redis**。Redis 7.4+ 改成了 SSPLv1/RSALv2 双许可，AGPLv3 项目不能分发。换成 **Valkey**（Redis 7.2 的 BSD 分支，100% 兼容）。

其余 90% 的优化方案都用 MIT/Apache 2.0 许可证，直接集成即可。
