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
"""指挥官 Agent - 管理所有子Agent的生命周期和任务调度"""
import redis, json, time, os, subprocess

r = redis.Redis(host='127.0.0.1', port=6379, password='Lt@114514!', decode_responses=True)
AGENTS = ['翻译官', '商务经理', '售前经理']

def log(msg):
    print(f'[Commander] {msg}', flush=True)

def agent_status():
    """检查所有Agent状态"""
    alive = []
    for name in AGENTS:
        channel = f'lightingmetal:agent:{name}'
        subs = r.publish(f'lightingmetal:agent:{name}', json.dumps({'type':'heartbeat_check','to':name}))
        alive.append({'name': name, 'subscribers': subs})
    return alive

def create_agent(name):
    """创建Agent（通过PM2）"""
    pm2_name = f'agent-{"translator" if name=="翻译官" else "business" if name=="商务经理" else "presales"}'
    subprocess.run(['pm2', 'start', '/app/.pi/agents/runtime/ecosystem.agents.cjs', '--only', pm2_name], 
                   capture_output=True)
    log(f'创建Agent: {name}')

def destroy_agent(name):
    """销毁Agent"""
    pm2_name = f'agent-{"translator" if name=="翻译官" else "business" if name=="商务经理" else "presales"}'
    subprocess.run(['pm2', 'stop', pm2_name], capture_output=True)
    subprocess.run(['pm2', 'delete', pm2_name], capture_output=True)
    log(f'销毁Agent: {name}')

def publish_task(to_agent, task_type, payload, task_id=None):
    """发布任务到指定Agent"""
    if not task_id:
        task_id = f'{task_type}-{int(time.time())}'
    msg = {
        'from': 'commander', 'to': to_agent, 'type': task_type,
        'taskId': task_id, 'timestamp': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'replyTo': 'lightingmetal:agent:commander', 'payload': payload
    }
    channel = f'lightingmetal:agent:{to_agent}'
    return r.publish(channel, json.dumps(msg, ensure_ascii=False))

# 启动：初始化所有Agent
log('指挥官上线')
log(f'启动 {len(AGENTS)} 个Agent...')

# 用PM2启动（如果还没启动）
existing = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True)
if 'agent-translator' not in existing.stdout:
    subprocess.run(['pm2', 'start', '/app/.pi/agents/runtime/ecosystem.agents.cjs'], capture_output=True)
    time.sleep(3)

# 检查状态
status = agent_status()
for s in status:
    log(f'  {s["name"]}: subscribers={s["subscribers"]}')

log(f'所有Agent就绪，等待任务...')

# Commander主循环：监听commander频道
pubsub = r.pubsub()
pubsub.subscribe('lightingmetal:agent:commander')

for message in pubsub.listen():
    if message['type'] != 'message':
        continue
    try:
        data = json.loads(message['data'])
    except:
        continue
    
    msg_type = data.get('type', '')
    
    if msg_type == 'heartbeat':
        agent = data.get('from', '')
        payload = data.get('payload', {})
        log(f'💓 {agent}: tasks={payload.get("tasks",0)} fails={payload.get("fails",0)} uptime={payload.get("uptime",0)}s')
    
    elif msg_type == 'response':
        agent = data.get('from', '')
        task_id = data.get('taskId', '')
        status = data.get('payload', {}).get('status', '')
        log(f'✅ {agent} 完成任务 {task_id}: {status}')
    
    elif msg_type == 'error':
        agent = data.get('from', '')
        task_id = data.get('taskId', '')
        error = data.get('payload', {}).get('error', '')
        log(f'❌ {agent} 任务失败 {task_id}: {error}')

log('指挥官下线')
