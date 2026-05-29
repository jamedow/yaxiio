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
"""Agent 全生命周期管理系统 — 四象限分级 + 状态机 + 性能追踪"""
import json, time, subprocess, os

DB = '/app/.pi/agents/runtime/agent-registry.json'
HISTORY = '/app/.pi/agents/runtime/agent-history.json'

# ============ 四象限分类 ============
# Core:      重要且常用 → 常驻运行, 持续优化
# Strategic: 重要但不常用 → 保留定义, 按需启动
# Utility:   不重要但常用 → 轻量运行, 自动管理
# Ephemeral: 不重要且不常用 → 用完即弃

DEFAULT_CLASSIFICATION = {
    '翻译官':   {'quadrant': 'core',       'keep_warm': True,  'max_idle': 300,  'optimize_priority': 1},
    '商务经理': {'quadrant': 'core',       'keep_warm': True,  'max_idle': 300,  'optimize_priority': 2},
    '售前经理': {'quadrant': 'core',       'keep_warm': True,  'max_idle': 300,  'optimize_priority': 3},
    '审计官':   {'quadrant': 'strategic',  'keep_warm': False, 'max_idle': 600,  'optimize_priority': 4},
    '数据迁移': {'quadrant': 'strategic',  'keep_warm': False, 'max_idle': 600,  'optimize_priority': 5},
    '心跳监控': {'quadrant': 'utility',    'keep_warm': True,  'max_idle': 120,  'optimize_priority': 8},
    '日志清理': {'quadrant': 'utility',    'keep_warm': False, 'max_idle': 300,  'optimize_priority': 9},
}

# ============ 状态机 ============
# running  → 活跃处理中
# idle     → 等待任务
# paused   → 冻结(快速恢复)
# archived → 定义保留,未运行
# error    → 故障状态

TRANSITIONS = {
    'running':  ['idle', 'paused', 'error', 'archived'],
    'idle':     ['running', 'paused', 'archived'],
    'paused':   ['running', 'idle', 'archived'],
    'archived': ['idle', 'running'],
    'error':    ['idle', 'archived'],
}

class AgentRegistry:
    def __init__(self):
        self.load()
    
    def load(self):
        try:
            with open(DB) as f:
                self.data = json.load(f)
        except:
            self.data = {'agents': {}, 'stats': {'total_created': 0, 'total_destroyed': 0, 'total_tasks': 0}}
        self.save()
    
    def save(self):
        with open(DB, 'w') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
    
    def register(self, name, role, quadrant='ephemeral', prompt=''):
        """注册新Agent"""
        now = time.strftime('%Y-%m-%dT%H:%M:%S')
        classification = DEFAULT_CLASSIFICATION.get(role, {
            'quadrant': quadrant, 'keep_warm': False, 'max_idle': 600, 'optimize_priority': 99
        })
        
        self.data['agents'][name] = {
            'name': name,
            'role': role,
            'status': 'archived',
            'quadrant': classification['quadrant'],
            'keep_warm': classification['keep_warm'],
            'max_idle': classification['max_idle'],
            'optimize_priority': classification['optimize_priority'],
            'pid': None,
            'prompt': prompt,
            'metrics': {'tasks': 0, 'success': 0, 'fail': 0, 'avg_response_ms': 0, 'last_active': None},
            'versions': [{'version': 1, 'created': now, 'changes': 'Initial'}],
            'created': now,
            'updated': now,
        }
        self.data['stats']['total_created'] += 1
        self.save()
        return self.data['agents'][name]
    
    def transition(self, name, new_status):
        """状态转换"""
        agent = self.data['agents'].get(name)
        if not agent: return False
        if new_status not in TRANSITIONS.get(agent['status'], []): return False
        
        agent['status'] = new_status
        agent['updated'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        
        # 状态变更动作
        if new_status == 'running':
            agent['metrics']['last_active'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        elif new_status == 'archived':
            pass  # PM2进程已在外部停止
        
        self.save()
        return True
    
    def optimize_check(self):
        """检查哪些Core Agent需要优化迭代"""
        candidates = []
        for name, agent in self.data['agents'].items():
            if agent['quadrant'] == 'core' and agent['status'] == 'running':
                m = agent['metrics']
                if m['tasks'] > 0:
                    success_rate = m['success'] / m['tasks'] if m['tasks'] > 0 else 1
                    if success_rate < 0.95:
                        candidates.append({'name': name, 'issue': 'low_success', 'rate': success_rate})
                    if m['avg_response_ms'] > 5000:
                        candidates.append({'name': name, 'issue': 'slow_response', 'ms': m['avg_response_ms']})
        return candidates
    
    def auto_scale(self):
        """自动扩缩容：根据idle时间决定暂停/销毁"""
        actions = []
        now = time.time()
        for name, agent in self.data['agents'].items():
            if agent['status'] != 'idle': continue
            if agent['quadrant'] in ('ephemeral',):
                actions.append({'agent': name, 'action': 'archive', 'reason': 'ephemeral_idle'})
            elif agent['status'] == 'idle' and agent.get('last_active'):
                idle_sec = now - time.mktime(time.strptime(agent['last_active'], '%Y-%m-%dT%H:%M:%S'))
                if idle_sec > agent['max_idle']:
                    action = 'pause' if agent['keep_warm'] else 'archive'
                    actions.append({'agent': name, 'action': action, 'reason': f'idle_{int(idle_sec)}s'})
        return actions
    
    def report(self):
        """生成管理报告"""
        quadrants = {'core': [], 'strategic': [], 'utility': [], 'ephemeral': []}
        for name, agent in self.data['agents'].items():
            quadrants[agent['quadrant']].append(name)
        
        return {
            'quadrants': {q: {'count': len(v), 'agents': v} for q, v in quadrants.items()},
            'statuses': {name: a['status'] for name, a in self.data['agents'].items()},
            'stats': self.data['stats'],
            'optimize_candidates': self.optimize_check(),
            'auto_scale': self.auto_scale(),
        }

# ============ CLI ============
if __name__ == '__main__':
    import sys
    reg = AgentRegistry()
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    
    if cmd == 'register':
        reg.register(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else 'ephemeral', sys.argv[5] if len(sys.argv) > 5 else '')
        print(f"✅ Registered: {sys.argv[2]}")
    
    elif cmd == 'start':
        name = sys.argv[2]
        agent = reg.data['agents'].get(name)
        if not agent:
            # 动态注册
            agent = reg.register(name, name, 'ephemeral')
        reg.transition(name, 'running')
        subprocess.run(['pm2', 'start', '/app/.pi/agents/runtime/agent.sh', '--name', f'agent-{name}', '--', agent['role']], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"✅ Started: {name} ({agent['quadrant']})")
    
    elif cmd == 'stop':
        name = sys.argv[2]
        reg.transition(name, 'archived')
        subprocess.run(['pm2', 'delete', f'agent-{name}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"⏹ Stopped: {name}")
    
    elif cmd == 'report':
        r = reg.report()
        print(json.dumps(r, indent=2, ensure_ascii=False))
    
    elif cmd == 'optimize':
        candidates = reg.optimize_check()
        if candidates:
            for c in candidates:
                print(f"⚠️  {c['name']}: {c['issue']} ({c.get('rate','')}{c.get('ms','')})")
        else:
            print("✅ All core agents healthy")
    
    elif cmd == 'scale':
        actions = reg.auto_scale()
        for a in actions:
            print(f"📐 {a['agent']} → {a['action']} ({a['reason']})")
            if a['action'] == 'archive':
                reg.transition(a['agent'], 'archived')
    
    else:
        print("Agent Registry CLI")
        print("  register <name> <role> [quadrant] [prompt]")
        print("  start <name>")
        print("  stop <name>")
        print("  report")
        print("  optimize")
        print("  scale")
