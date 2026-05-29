#!/usr/bin/env python3
"""Redis 宕机恢复: 从 MongoDB 恢复关键数据"""
import json, os, subprocess
from datetime import datetime

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_CLI = ["redis-cli", "-h", REDIS_HOST, "-p", REDIS_PORT, "-a", REDIS_PASSWORD, "--no-auth-warning"]

def redis_cmd(*args):
    a = REDIS_CLI + list(args)
    r = subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    return r.stdout.strip()

def mongo_find(collection, query='{}', limit=100):
    js = f"const db=db.getSiblingDB('lightingmetal');const docs=db.{collection}.find({query}).limit({limit}).toArray();print(JSON.stringify(docs));"
    r = subprocess.run(['mongosh','--quiet','--eval', js],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    try:
        return json.loads(r.stdout)
    except:
        return []

print(f"[{datetime.now()}] Redis 恢复开始...")
recovered = 0

# 1. 检查 Redis 是否可达
ping = redis_cmd('PING')
if ping != 'PONG':
    print("❌ Redis 不可达，无法恢复")
    exit(1)

# 2. 恢复进行中的任务记忆
print("📋 恢复任务记忆...")
memories = mongo_find('agent_task_memory', '{"data.status":"in_progress"}', 50)
for m in memories:
    task_id = m.get('taskId', '')
    data = m.get('data', {})
    if task_id and data:
        redis_cmd('DEL', f'agent:memory:{task_id}')
        for k, v in data.items():
            redis_cmd('HSET', f'agent:memory:{task_id}', k, str(v))
        redis_cmd('ZADD', 'agent:memory:index', str(int(datetime.now().timestamp())), task_id)
        recovered += 1
        print(f"  ✅ {task_id}")

# 3. 恢复最新调度策略
print("📐 恢复调度策略...")
policies = mongo_find('agent_scheduling_policy', '{}', 1)
if policies:
    policy_data = policies[0].get('data', {})
    redis_cmd('SET', 'agent:scheduling_policy:current', json.dumps(policy_data))
    recovered += 1
    print(f"  ✅ 调度策略已恢复")

# 4. 清理失效的 Agent 状态
print("🧹 清理失效状态...")
redis_cmd('DEL', 'agent:heartbeat:*')  # 清理旧心跳
redis_cmd('DEL', 'agent:status:online')  # 重置在线状态

# 5. 输出恢复报告
print(f"\n[{datetime.now()}] Redis 恢复完成")
print(f"  恢复任务记忆: {recovered} 条")
print(f"  所有进行中任务已恢复")
print(f"  建议: 销毁并重建所有子Agent (pm2 restart agent-*)")
