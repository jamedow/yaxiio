// MongoDB 初始化脚本 — Commander 所需集合
// 在容器首次启动时自动执行

db = db.getSiblingDB("lightingmetal");

// 任务优化日志
db.createCollection("agent_optimization_log");
db.agent_optimization_log.createIndex({ "timestamp": -1 });
db.agent_optimization_log.createIndex({ "overallStatus": 1, "date": 1 });

// 降级任务记录
db.createCollection("degraded_tasks");
db.degraded_tasks.createIndex({ "date": 1 });

// Token 用量
db.createCollection("token_usage");
db.token_usage.createIndex({ "date": 1 });

// 路由决策
db.createCollection("routing_decisions");
db.routing_decisions.createIndex({ "task_id": 1 });
db.routing_decisions.createIndex({ "timestamp": -1 });

// 故障记录
db.createCollection("agent_failures");
db.agent_failures.createIndex({ "agent_id": 1, "timestamp": -1 });

// 故障转移记录
db.createCollection("agent_failovers");
db.agent_failovers.createIndex({ "timestamp": -1 });

print("✅ MongoDB 初始化完成 — lightingmetal 数据库 6 个集合就绪");
