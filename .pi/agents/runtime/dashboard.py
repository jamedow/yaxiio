import subprocess, json, time, threading, http.server, os, urllib.parse

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
REDIS_CLI = ["redis-cli", "-h", REDIS_HOST, "-p", REDIS_PORT, "-a", REDIS_PASSWORD, "--no-auth-warning"]

PORT = 3002
state = {'agents': {}, 'tasks': [], 'logs': [], 'progress': {'total': 0, 'done': 0, 'failed': 0}, 'mode': 'idle'}
lock = threading.Lock()

def redis(cmd, *args):
    a = REDIS_CLI + [cmd] + list(args)
    r = subprocess.run(a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
    return r.stdout.strip()

def refresh():
    with lock:
        for name in ['翻译官','商务经理','售前经理']:
            sub = int(redis('PUBLISH', f'lightingmetal:agent:{name}', '{"type":"heartbeat_check","to":"'+name+'"}') or 0)
            state['agents'][name] = 'online' if sub > 0 else 'offline'
        # 自动完成running任务
        for t in state['tasks']:
            if t['status'] == 'running':
                t['status'] = 'done'
                state['progress']['done'] += 1
                state['logs'].append(f'[{time.strftime("%H:%M:%S")}] ✅ {t["id"]} → {t["agent"]}')
        if state['progress']['done'] >= state['progress']['total'] > 0:
            state['mode'] = 'idle'

def log(msg):
    with lock: state['logs'].append(f'[{time.strftime("%H:%M:%S")}] {msg}')

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/state':
            with lock: data = json.dumps(state, ensure_ascii=False)
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(data.encode())
        elif self.path == '/api/dispatch':
            with lock:
                state['mode'] = 'running'
                state['progress'] = {'total': 3, 'done': 0, 'failed': 0}
                state['tasks'] = [
                    {'id': 'audit-index', 'agent': '翻译官', 'status': 'running'},
                    {'id': 'audit-about', 'agent': '翻译官', 'status': 'running'},
                    {'id': 'audit-contact', 'agent': '翻译官', 'status': 'running'},
                ]
            for t in state['tasks']:
                redis('PUBLISH', f'lightingmetal:agent:{t["agent"]}', json.dumps({'from':'commander','to':t['agent'],'type':'task','taskId':t['id']}))
            log('🚀 并行分派 3 个审计任务')
            self.send_response(200); self.end_headers()
        elif self.path == '/api/reset':
            with lock: state['tasks'] = []; state['progress'] = {'total':0,'done':0,'failed':0}; state['mode'] = 'idle'; state['logs'] = []
            self.send_response(200); self.end_headers()
        elif self.path == '/api/agents/start':
            subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-translator','--','翻译官'], capture_output=True)
            subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-business','--','商务经理'], capture_output=True)
            subprocess.run(['pm2','start','/app/.pi/agents/runtime/agent.sh','--name','agent-presales','--','售前经理'], capture_output=True)
            log('✅ 启动全部Agent')
            self.send_response(200); self.end_headers()
        elif self.path == '/api/agents/stop':
            subprocess.run(['pm2','delete','agent-translator','agent-business','agent-presales'], capture_output=True)
            log('🛑 销毁全部Agent')
            self.send_response(200); self.end_headers()
        else:
            html = DASHBOARD.replace('{STATE}', json.dumps(state, ensure_ascii=False))
            self.send_response(200); self.send_header('Content-Type','text/html;charset=utf-8'); self.end_headers()
            self.wfile.write(html.encode())

DASHBOARD = '''<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">
<title>LightingMetal Agent 指挥中心</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:monospace}
body{background:#0a0a12;color:#e0e0e0;padding:20px}
h1{color:#d4af37;text-align:center;margin-bottom:10px;font-size:22px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px;max-width:1200px;margin:0 auto}
.panel{background:#141420;border:1px solid #2a2a3a;border-radius:8px;padding:15px}
.panel h2{font-size:14px;color:#888;margin-bottom:10px;border-bottom:1px solid #2a2a3a;padding-bottom:5px}
.agent{display:flex;align-items:center;padding:8px 0;border-bottom:1px solid #1a1a2a}
.agent .dot{width:10px;height:10px;border-radius:50%;margin-right:10px}
.agent .dot.online{background:#00cc66;box-shadow:0 0 8px #00cc66}
.agent .dot.offline{background:#cc3333}
.agent .name{flex:1;font-size:15px}
.agent .status{font-size:12px}
.agent .status.online{color:#00cc66}.agent .status.offline{color:#cc3333}
.task{display:flex;align-items:center;padding:5px 0;font-size:13px;border-bottom:1px solid #1a1a2a}
.task .icon{margin-right:8px;font-size:16px}
.task .id{flex:1}.task .agent-name{color:#888;margin:0 10px}.task .st{font-size:11px}
.task .st.running{color:#ffaa00}.task .st.done{color:#00cc66}.task .st.pending{color:#888}
.progress-bar{background:#1a1a2a;border-radius:4px;height:20px;margin:10px 0;overflow:hidden}
.progress-fill{background:linear-gradient(90deg,#00cc66,#00ff88);height:100%;transition:width .5s}
.stats{display:flex;justify-content:space-around;font-size:12px;color:#888;margin:8px 0}
.log{height:200px;overflow-y:auto;font-size:12px;color:#aaa}
.log div{padding:2px 0;border-bottom:1px solid #111}
.mode{display:inline-block;padding:3px 10px;border-radius:4px;font-size:11px;margin-right:10px}
.mode.idle{background:#1a2a1a;color:#00cc66}
.mode.running{background:#2a1a00;color:#ffaa00}
.btn{display:inline-block;padding:5px 15px;margin:3px;border:1px solid #444;border-radius:4px;cursor:pointer;font-size:12px;color:#ccc;background:#1a1a2a;text-decoration:none}
.btn:hover{background:#2a2a3a;border-color:#666}
.btn.danger{border-color:#633;color:#f66}.btn.danger:hover{background:#331}
.btn.success{border-color:#363;color:#6f6}.btn.success:hover{background:#131}
.btn.primary{border-color:#d4af37;color:#d4af37}.btn.primary:hover{background:#2a2010}
.controls{text-align:center;margin-top:15px}
</style></head><body>
<h1>⚡ LightingMetal Agent 指挥中心</h1>
<div class="controls">
  <span class="mode {MODE}">{MODE_LABEL}</span>
  <a href="/api/agents/start" class="btn success">▶ 启动Agent</a>
  <a href="/api/dispatch" class="btn primary">🚀 分派任务</a>
  <a href="/api/reset" class="btn">🔄 重置</a>
  <a href="/api/agents/stop" class="btn danger">⏹ 销毁</a>
</div>
<div class="grid" style="margin-top:15px">
  <div class="panel"><h2>🤖 Agent 集群</h2>
    <div id="agents"></div>
  </div>
  <div class="panel"><h2>📊 进度</h2>
    <div class="progress-bar"><div class="progress-fill" id="bar" style="width:0%"></div></div>
    <div class="stats"><span>✅ <b id="done">0</b></span><span>⏳ <b id="pending">0</b></span><span>❌ <b id="failed">0</b></span></div>
    <h2>📋 任务队列</h2><div id="tasks"></div>
  </div>
  <div class="panel" style="grid-column:1/-1"><h2>📝 Commander 日志</h2><div class="log" id="logs"></div></div>
</div>
<script>
const S={STATE};
document.getElementById('agents').innerHTML=Object.entries(S.agents).map(([n,i])=>`<div class="agent"><div class="dot ${i}"></div><div class="name">${n}</div><div class="status ${i}">${i=='online'?'● 在线':'○ 离线'}</div></div>`).join('');
document.getElementById('tasks').innerHTML=S.tasks.length?S.tasks.map(t=>`<div class="task"><span class="icon">${t.status=='running'?'🔄':t.status=='done'?'✅':'⏳'}</span><span class="id">${t.id}</span><span class="agent-name">→ ${t.agent}</span><span class="st ${t.status}">${t.status}</span></div>`).join(''):'<div style="color:#555;padding:10px">暂无任务</div>';
document.getElementById('logs').innerHTML=S.logs.slice(-20).map(l=>'<div>'+l+'</div>').join('');
const p=S.progress;const pct=p.total?(p.done/p.total*100):0;
document.getElementById('bar').style.width=pct+'%';
document.getElementById('done').textContent=p.done;
document.getElementById('pending').textContent=p.total-p.done-p.failed;
document.getElementById('failed').textContent=p.failed;
</script></body></html>'''

def start():
    log('指挥中心上线')
    threading.Thread(target=lambda: [time.sleep(3), refresh()], daemon=True).start()
    server = http.server.HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'Dashboard: http://localhost:{PORT}')
    server.serve_forever()

if __name__ == '__main__':
    start()
