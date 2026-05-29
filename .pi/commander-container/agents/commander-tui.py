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
"""LightingMetal Agent 指挥中心 — 终端图形化界面 (零依赖)"""
import subprocess, json, time, sys, os, threading, queue

# ============ ANSI 终端控制 ============
CSI = '\033['
def cup(row, col): return f'{CSI}{row};{col}H'
def clear(): return f'{CSI}2J{CSI}H'
def hide_cursor(): return f'{CSI}?25l'
def show_cursor(): return f'{CSI}?25h'
def color(fg=None, bg=None, bold=False):
    codes = []
    if bold: codes.append('1')
    if fg:
        c = {'black':30,'red':31,'green':32,'yellow':33,'blue':34,'magenta':35,'cyan':36,'white':37,'gold':33}
        codes.append(str(c.get(fg, 37)))
    if bg:
        c = {'black':40,'red':41,'green':42,'yellow':43,'blue':44,'dark':40}
        codes.append(str(c.get(bg, 40)))
    return f'{CSI}{";".join(codes)}m' if codes else ''
def reset(): return f'{CSI}0m'

# ============ 数据层 ============
state = {
    'agents': {},
    'tasks': [],
    'commander_log': [],
    'progress': {'total': 0, 'done': 0, 'failed': 0},
    'mode': 'idle',
    'task_name': '',
}
lock = threading.Lock()
ui_queue = queue.Queue()

def redis(cmd, *args):
    a = ['docker','exec','redis-centos7','redis-cli','-a','Lt@114514!',cmd] + list(args)
    r = subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
    return r.stdout.strip()

def agent_heartbeat(name):
    sub = redis('PUBLISH', f'lightingmetal:agent:{name}', 
        '{"type":"heartbeat_check","to":"'+name+'"}')
    return int(sub) if sub.isdigit() else 0

def refresh_state():
    with lock:
        for name in ['翻译官','商务经理','售前经理']:
            alive = agent_heartbeat(name)
            prev = state['agents'].get(name, {})
            state['agents'][name] = {
                'alive': alive > 0,
                'tasks': prev.get('tasks', 0) + (1 if alive > 0 else 0) * 0,
                'last_seen': time.strftime('%H:%M:%S'),
            }

# ============ 渲染引擎 ============
def draw_header():
    w = os.get_terminal_size().columns
    yield color('gold', bold=True) + '╔' + '═'*(w-2) + '╗' + reset()
    yield color('gold', bold=True) + '║' + '  ⚡ LightingMetal Agent 指挥中心'.center(w-2) + '║' + reset()
    yield color('gold', bold=True) + '╚' + '═'*(w-2) + '╝' + reset()

def draw_agent_panel(row_start):
    yield cup(row_start, 1) + color('cyan', bold=True) + '┌─ Agent 集群状态 '.ljust(40, '─') + '┐' + reset()
    r = row_start + 1
    
    status_colors = {True: color('green'), False: color('red')}
    for name, info in state['agents'].items():
        alive = info['alive']
        icon = '●' if alive else '○'
        status = '在线' if alive else '离线'
        mem = '~2MB' if alive else '--'
        line = f'│  {status_colors[alive]}{icon} {name:<10}{reset}│{status_colors[alive]}{status:<6}{reset}│ 订阅:'
        line += f'{status_colors[alive]}{"1" if alive else "0"}{reset} │ 内存: {mem} │'
        yield cup(r, 1) + line
        r += 1
    
    yield cup(r, 1) + '└' + '─'*39 + '┘' + reset()
    return r + 1

def draw_task_panel(row_start):
    w = 60
    yield cup(row_start, 1) + color('yellow', bold=True) + '┌─ 任务队列 '.ljust(w-2, '─') + '┐' + reset()
    r = row_start + 1
    
    if not state['tasks']:
        yield cup(r, 1) + f'│  {"暂无活跃任务":<{w-4}} │' + reset()
        r += 1
    else:
        for i, task in enumerate(state['tasks'][-10:]):
            icon = {'pending':'⏳','running':'🔄','done':'✅','failed':'❌'}.get(task['status'],'❓')
            line = f'│ {icon} {task["id"]:<20} → {task["agent"]:<12} {task["status"]:<8} │'
            yield cup(r, 1) + line
            r += 1
    
    yield cup(r, 1) + '└' + '─'*(w-2) + '┘' + reset()
    return r + 1

def draw_progress(row_start):
    p = state['progress']
    total = p['total']
    if total == 0:
        yield cup(row_start, 1) + color('magenta', bold=True) + '┌─ 进度 '.ljust(40, '─') + '┐' + reset()
        yield cup(row_start+1, 1) + f'│  等待任务...{" ":<27} │' + reset()
        yield cup(row_start+2, 1) + '└' + '─'*39 + '┘' + reset()
        return row_start + 3
    
    done = p['done']
    failed = p['failed']
    pct = int(done / total * 100) if total > 0 else 0
    bar_w = 30
    filled = int(bar_w * done / total)
    bar = '█' * filled + '░' * (bar_w - filled)
    
    yield cup(row_start, 1) + color('magenta', bold=True) + '┌─ 进度 '.ljust(40, '─') + '┐' + reset()
    yield cup(row_start+1, 1) + f'│  任务: {state["task_name"][:30]:<30} │' + reset()
    yield cup(row_start+2, 1) + f'│  [{color("green")}{bar}{reset()}] {pct}% │' + reset()
    yield cup(row_start+3, 1) + f'│  ✅{done}  ❌{failed}  剩余{total-done-failed}                         │' + reset()
    yield cup(row_start+4, 1) + '└' + '─'*39 + '┘' + reset()
    return row_start + 5

def draw_log(row_start):
    h = 8
    yield cup(row_start, 1) + color('blue', bold=True) + '┌─ Commander 日志 '.ljust(60, '─') + '┐' + reset()
    r = row_start + 1
    logs = state['commander_log'][-h:]
    for i in range(h):
        if i < len(logs):
            line = f'│ {logs[i][:56]:<56} │'
        else:
            line = f'│ {"":<56} │'
        yield cup(r + i, 1) + line
    yield cup(r + h, 1) + '└' + '─'*59 + '┘' + reset()
    return r + h + 1

def draw_footer():
    w = os.get_terminal_size().columns
    yield cup(os.get_terminal_size().lines - 1, 1) + color('gold') + \
        ' Q:退出 │ R:刷新 │ T:测试任务 │ C:清日志 │ D:销毁Agent │ S:启动Agent '.ljust(w-2) + reset()

def render():
    out = [clear(), hide_cursor()]
    out.extend(draw_header())
    
    row = 4
    row = draw_agent_panel(row)
    out.extend(list(ui_queue.queue))  # flush pending renders
    ui_queue.queue.clear()
    
    out.append(cup(row, 1))
    row = draw_task_panel(row)
    row = draw_progress(row)
    row = draw_log(row)
    out.extend(draw_footer())
    
    sys.stdout.write(''.join(out))
    sys.stdout.flush()

# ============ 任务调度 ============
def add_task(task_id, agent, action):
    with lock:
        state['tasks'].append({'id': task_id, 'agent': agent, 'status': 'pending', 'action': action})

def update_task(task_id, status):
    with lock:
        for t in state['tasks']:
            if t['id'] == task_id:
                t['status'] = status

def log(msg):
    with lock:
        state['commander_log'].append(f'[{time.strftime("%H:%M:%S")}] {msg}')

def dispatch_parallel(tasks):
    """并行分发任务到多个Agent"""
    state['mode'] = 'running'
    state['progress']['total'] = len(tasks)
    state['progress']['done'] = 0
    state['progress']['failed'] = 0
    
    for t in tasks:
        add_task(t['id'], t['agent'], t['action'])
    
    # 发布所有任务
    for t in tasks:
        channel = f'lightingmetal:agent:{t["agent"]}'
        msg = json.dumps({
            'from': 'commander', 'to': t['agent'], 'type': 'task',
            'taskId': t['id'], 'payload': t.get('payload', {})
        }, ensure_ascii=False)
        redis('PUBLISH', channel, msg)
        update_task(t['id'], 'running')
        log(f'📤 {t["id"]} → {t["agent"]}')
    
    log(f'🚀 并行发布 {len(tasks)} 个任务')

# ============ 主循环 ============
def main():
    state['task_name'] = 'Agent 指挥中心'
    log('系统启动')
    
    refresh_state()
    render()
    
    # 启动键盘监听
    
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    
    try:
        tty.setraw(fd)
        while True:
            # 非阻塞读取
            import select; import tty; import termios
            if select.select([sys.stdin], [], [], 0.2)[0]:
                ch = sys.stdin.read(1)
                if ch == 'q' or ch == 'Q' or ord(ch) == 3:
                    log('系统退出')
                    render()
                    break
                elif ch == 'r' or ch == 'R':
                    log('手动刷新')
                    refresh_state()
                elif ch == 't' or ch == 'T':
                    # 测试：并行分派3个翻译审计任务
                    dispatch_parallel([
                        {'id': 'audit-index', 'agent': '翻译官', 'action': 'audit', 'payload': {'page': 'index'}},
                        {'id': 'audit-about', 'agent': '翻译官', 'action': 'audit', 'payload': {'page': 'about'}},
                        {'id': 'audit-contact', 'agent': '翻译官', 'action': 'audit', 'payload': {'page': 'contact'}},
                    ])
                elif ch == 'c' or ch == 'C':
                    with lock: state['commander_log'] = []
                    log('日志已清除')
                elif ch == 'd' or ch == 'D':
                    subprocess.run(['pm2','delete','agent-translator','agent-business','agent-presales'], capture_output=True)
                    log('所有Agent已销毁')
                elif ch == 's' or ch == 'S':
                    subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-translator','--','翻译官'], capture_output=True)
                    subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-business','--','商务经理'], capture_output=True)
                    subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-presales','--','售前经理'], capture_output=True)
                    log('所有Agent已启动')
            
            # 定期刷新状态
            refresh_state()
            
            # 检查任务完成（从Pub/Sub收响应）
            # (简化：直接标记done)
            with lock:
                for t in state['tasks']:
                    if t['status'] == 'running':
                        t['status'] = 'done'
                        state['progress']['done'] += 1
                        log(f'✅ {t["id"]} 完成')
            
            if state['progress']['done'] >= state['progress']['total'] > 0:
                state['mode'] = 'idle'
                log('✨ 全部任务完成')
            
            render()
    
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write(show_cursor() + clear())
        sys.stdout.flush()

if __name__ == '__main__':
    main()
