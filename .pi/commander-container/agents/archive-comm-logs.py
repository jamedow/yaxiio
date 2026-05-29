# Copyright 2026 LightingMetal
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#!/usr/bin/env python3
"""通信日志归档: Redis AOF → MongoDB 每日归档"""
import json, subprocess, time
from datetime import datetime, timedelta

def redis_cmd(*args):
    a = ['docker','exec','redis-centos7','redis-cli','-a','Lt@114514!'] + list(args)
    r = subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    return r.stdout.strip()

def mongo_cmd(js):
    r = subprocess.run(['docker','exec','-i','mongodb','mongosh','--quiet','--eval', js],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    return r.stdout.strip()

yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
today = datetime.now().strftime('%Y-%m-%d')

print(f"[{datetime.now()}] 归档日期: {yesterday}")

# 1. 归档 Agent 通信日志 (Pub/Sub 消息无法回溯，转为归档 agent-memory)
archived = 0

# 归档任务记忆 (agent:memory:*) 
memory_keys = redis_cmd('KEYS', 'agent:memory:*')
memory_keys = [k for k in memory_keys.split('\n') if k and ':kw:' not in k and k != 'agent:memory:index']

for key in memory_keys:
    if ':agent:' in key:
        continue  # Agent历史列表已在MongoDB
    task_id = key.replace('agent:memory:', '')
    data = {}
    raw = redis_cmd('HGETALL', key)
    if raw:
        lines = raw.split('\n')
        for i in range(0, len(lines)-1, 2):
            data[lines[i]] = lines[i+1]
    
    if data:
        doc = json.dumps({
            'taskId': task_id,
            'date': yesterday,
            'data': data,
            'archivedAt': datetime.now().isoformat()
        }, ensure_ascii=False)
        mongo_cmd(f"const db=db.getSiblingDB('lightingmetal');db.agent_task_memory.updateOne({{taskId:'{task_id}',date:'{yesterday}'}},{{$set:{doc}}},{{upsert:true}});")
        archived += 1

# 2. 归档调度策略
policy = redis_cmd('GET', 'agent:scheduling_policy:current')
if policy:
    mongo_cmd(f"const db=db.getSiblingDB('lightingmetal');db.agent_scheduling_policy.insertOne({{date:'{yesterday}',data:{policy},updatedAt:new Date()}});")

# 3. 清理30天前的旧索引
old_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
mongo_cmd(f"const db=db.getSiblingDB('lightingmetal');db.agent_task_memory.deleteMany({{date:{{$lt:'{old_date}'}}}});")

# 4. 标记归档状态
redis_cmd('SET', 'agent:last_archive', today)
redis_cmd('SET', 'agent:archive_count', str(archived))

print(f"[{datetime.now()}] 归档完成: {archived} 条任务记忆 → MongoDB")
print(f"  清理: 30天前的旧记录已删除")
print(f"  状态: agent:last_archive = {today}")
