#!/usr/bin/env python3
"""自进化Agent核心 - 订阅Redis频道，处理任务，自我反馈"""
import redis, json, time, os, signal, subprocess, sys

AGENT_NAME = os.environ.get('AGENT_NAME', 'unknown')
AGENT_ROLE = os.environ.get('AGENT_ROLE', 'worker')
CHANNEL = f'lightingmetal:agent:{AGENT_NAME}'
CONTROL_CHANNEL = 'lightingmetal:agent:commander'
REDIS_HOST = os.environ.get('REDIS_HOST', '127.0.0.1')
REDIS_PASS = os.environ.get('REDIS_PASS', '')

class Agent:
    def __init__(self):
        self.r = redis.Redis(host=REDIS_HOST, port=6379, password=REDIS_PASS or None, decode_responses=True)
        self.running = True
        self.task_count = 0
        self.fail_count = 0
        self.start_time = time.time()
        
    def log(self, msg, level='INFO'):
        print(f'[{AGENT_NAME}] [{level}] {msg}', flush=True)
    
    def send_heartbeat(self):
        self.r.publish(CONTROL_CHANNEL, json.dumps({
            'from': AGENT_NAME, 'to': 'commander', 'type': 'heartbeat',
            'payload': {'status': 'alive', 'tasks': self.task_count, 'fails': self.fail_count,
                        'uptime': int(time.time() - self.start_time)}
        }))
    
    def process_task(self, msg):
        """处理单个任务 - 子类可重写"""
        self.task_count += 1
        task_id = msg.get('taskId', 'unknown')
        action = msg.get('payload', {}).get('action', 'unknown')
        
        self.log(f'处理任务: {task_id} ({action})')
        
        try:
            # 执行shell命令（Agent的主要能力）
            if 'command' in msg.get('payload', {}):
                cmd = msg['payload']['command']
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
                response = {
                    'from': AGENT_NAME, 'to': msg.get('from', 'commander'),
                    'type': 'response', 'taskId': task_id,
                    'payload': {'status': 'success', 'stdout': result.stdout[:2000], 'stderr': result.stderr[:500]}
                }
            else:
                response = {
                    'from': AGENT_NAME, 'to': msg.get('from', 'commander'),
                    'type': 'response', 'taskId': task_id,
                    'payload': {'status': 'done', 'action': action, 'note': f'{AGENT_NAME} completed'}
                }
        except Exception as e:
            self.fail_count += 1
            response = {
                'from': AGENT_NAME, 'to': msg.get('from', 'commander'),
                'type': 'error', 'taskId': task_id,
                'payload': {'status': 'failed', 'error': str(e)}
            }
            self.log(f'任务失败: {e}', 'ERROR')
        
        # 回复到发送者频道
        reply_to = msg.get('replyTo') or f'lightingmetal:agent:{msg.get("from", "commander")}'
        self.r.publish(reply_to, json.dumps(response, ensure_ascii=False))
    
    def run(self):
        self.log(f'启动完成, 角色: {AGENT_ROLE}, 频道: {CHANNEL}')
        self.send_heartbeat()
        
        pubsub = self.r.pubsub()
        pubsub.subscribe(CHANNEL, CONTROL_CHANNEL)
        
        last_heartbeat = time.time()
        
        for message in pubsub.listen():
            if not self.running:
                break
            
            if message['type'] != 'message':
                continue
            
            channel = message['channel']
            try:
                data = json.loads(message['data'])
            except:
                continue
            
            msg_type = data.get('type', '')
            
            # 控制频道消息
            if channel == CONTROL_CHANNEL:
                if msg_type == 'shutdown' and data.get('to') in (AGENT_NAME, '*'):
                    self.log('收到关闭指令')
                    self.running = False
                    break
                elif msg_type == 'upgrade_prompt' and data.get('to') in (AGENT_NAME, '*'):
                    new_prompt = data.get('payload', {}).get('prompt', '')
                    if new_prompt:
                        self.log(f'升级提示词: {new_prompt[:50]}...')
                        self.r.set(f'agent:{AGENT_NAME}:prompt', new_prompt)
                continue
            
            # 任务频道消息
            if data.get('to') not in (AGENT_NAME, '*', 'all'):
                continue
            
            if msg_type in ('request', 'task', 'translate_batch', 'audit_request'):
                self.process_task(data)
            elif msg_type == 'heartbeat_check':
                self.send_heartbeat()
        
        self.log(f'关闭 (处理了{self.task_count}个任务)')
        pubsub.close()

if __name__ == '__main__':
    Agent().run()
