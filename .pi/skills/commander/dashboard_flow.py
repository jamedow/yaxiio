#!/usr/bin/env python3
"""Yaxiio Workflow Dashboard — L1-L5 real-time visualization + Agent management"""
import os, json, time
import redis as _r
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")
r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=REDIS_PASS, decode_responses=True)

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yaxiio Dashboard</title>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/reactflow@11/dist/reactflow.min.js"></script>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#c9d1d9}
header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}
header h1{font-size:16px;color:#58a6ff;margin:0}
.tabs{display:flex;gap:8px}
.tab{padding:6px 16px;border-radius:6px;border:1px solid #30363d;background:transparent;color:#8b949e;cursor:pointer;font-size:13px}
.tab.active{background:#1f6feb;color:#fff;border-color:#1f6feb}
main{display:flex;height:calc(100vh - 45px)}
.panel{flex:1;overflow:auto}
.sidebar{width:300px;background:#161b22;border-left:1px solid #30363d;padding:16px;overflow-y:auto}
.card{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:14px;margin-bottom:12px}
.card h3{font-size:14px;margin:0 0 8px 0;color:#58a6ff}
.agent-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #21262d;font-size:13px}
.agent-status{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.status-idle{background:#3fb950}.status-executing{background:#d2991d}.status-fault{background:#f85149}
.btn{padding:4px 12px;border-radius:4px;border:1px solid #30363d;background:#21262d;color:#c9d1d9;cursor:pointer;font-size:12px}
.btn:hover{background:#30363d}.btn-danger:hover{background:#f85149;color:#fff}
#flow-canvas{width:100%;height:100%}
.rf-node{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 14px;font-size:12px;min-width:120px}
.rf-node.l1{border-color:#58a6ff}.rf-node.l2{border-color:#3fb950}.rf-node.l3{border-color:#d2991d}
.rf-node.l4{border-color:#f85149}.rf-node.l5{border-color:#bc8cff}
.node-title{font-weight:bold;margin-bottom:4px}
.node-status{font-size:11px;color:#8b949e}
</style>
</head>
<body>
<header>
  <h1>Yaxiio Dashboard</h1>
  <div class="tabs">
    <button class="tab active" onclick="switchTab('flow')">工作流</button>
    <button class="tab" onclick="switchTab('agents')">Agent管理</button>
    <button class="tab" onclick="switchTab('scores')">评分</button>
  </div>
</header>
<main>
  <div class="panel" id="main-panel"><div id="flow-canvas"></div></div>
  <div class="sidebar" id="sidebar"></div>
</main>
<script>
let currentTab = 'flow';

function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    event.target.classList.add('active');
    loadTab();
}

async function loadTab() {
    if (currentTab === 'flow') await loadFlow();
    else if (currentTab === 'agents') await loadAgents();
    else if (currentTab === 'scores') await loadScores();
}

// ===== Flow View =====
async function loadFlow() {
    document.getElementById('main-panel').innerHTML = '<div id="flow-canvas"></div>';
    document.getElementById('sidebar').innerHTML = '<div class="card"><h3>最近任务</h3><div id="task-list"></div></div>';
    
    const resp = await fetch('/api/workflow');
    const data = await resp.json();
    
    // Render task list
    let taskHtml = '';
    data.tasks.slice(0, 10).forEach(t => {
        taskHtml += '<div class="agent-row"><span>' + t.id + '</span><span style="font-size:11px;color:#8b949e">' + (t.subtasks||0) + ' subtasks</span></div>';
    });
    document.getElementById('task-list').innerHTML = taskHtml || '暂无任务';
    
    // Render flow
    if (data.current_flow) {
        renderFlow(data.current_flow);
    }
}

function renderFlow(flow) {
    const nodes = [];
    const edges = [];
    const layers = ['L1','L2','L3','L4','L5'];
    
    layers.forEach((l, i) => {
        nodes.push({
            id: l, type: 'default',
            position: {x: 80 + i*220, y: 200},
            data: {label: l + '\\n' + (flow.layers[l] || 'idle')},
            className: 'rf-node ' + l.toLowerCase()
        });
        if (i > 0) {
            edges.push({id: 'e'+i, source: layers[i-1], target: l, animated: true,
                       style: {stroke: '#30363d'}});
        }
    });
    
    // Add agent nodes
    (flow.agents || []).forEach((a, i) => {
        nodes.push({
            id: 'a'+i, type: 'default',
            position: {x: 80 + i*160, y: 350},
            data: {label: a.name + '\\n' + a.state},
            className: 'rf-node l4'
        });
        edges.push({id: 'ea'+i, source: 'L3', target: 'a'+i, style: {stroke: '#d2991d'}});
    });
    
    const rf = new ReactFlow({
        nodes, edges,
        fitView: true,
        nodesDraggable: false,
        elementsSelectable: false
    });
    const root = ReactDOM.createRoot(document.getElementById('flow-canvas'));
    root.render(React.createElement(rf.Wrapper));
}

// ===== Agent View =====
async function loadAgents() {
    const resp = await fetch('/api/agents');
    const data = await resp.json();
    
    let html = '<div class="card"><h3>Agent管理 (' + data.length + ')</h3>';
    data.forEach(a => {
        let sc = a.running ? 'status-executing' : 'status-idle';
        html += '<div class="agent-row">' +
            '<span><span class="agent-status ' + sc + '"></span>' + a.name + '</span>' +
            '<span style="font-size:11px;color:#8b949e">' + a.quadrant + '</span>' +
            '</div>';
    });
    html += '</div>';
    document.getElementById('main-panel').innerHTML = html;
    document.getElementById('sidebar').innerHTML = '<div class="card"><h3>快速操作</h3><p style="font-size:12px;color:#8b949e">点击Agent查看能力卡片</p></div>';
}

// ===== Scores View =====
async function loadScores() {
    const resp = await fetch('/api/scores');
    const data = await resp.json();
    
    let html = '<div class="card"><h3>最近评分</h3>';
    data.forEach(s => {
        html += '<div class="agent-row"><span>' + s.task_id + '</span>' +
            '<span>AI: ' + s.ai_score + ' | Human: ' + (s.human_score||'-') + '</span></div>';
    });
    html += '</div>';
    document.getElementById('main-panel').innerHTML = html || '暂无评分';
    document.getElementById('sidebar').innerHTML = '';
}

loadFlow();
setInterval(loadTab, 10000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(HTML)

@app.route("/api/workflow")
def api_workflow():
    tasks = []
    for key in r.scan_iter("yaxiio:task:*", count=10):
        try:
            data = json.loads(r.get(key))
            result = data.get("result", data)
            sr = result.get("subtask_results", {})
            tasks.append({"id": key.replace("yaxiio:task:", ""), "subtasks": len(sr),
                         "status": data.get("status", "?"),
                         "agents": list(set(r.get("agent","?") for r in sr.values()))})
        except: pass
    tasks.sort(key=lambda t: t["id"], reverse=True)
    
    # Get current flow state
    agents = []
    for key in r.scan_iter("agent:*:state", count=20):
        try:
            name = key.split(":")[1]
            state = json.loads(r.get(key) or "{}")
            agents.append({"name": name, "state": state.get("state","idle"),
                          "progress": state.get("progress",0)})
        except: pass
    
    return jsonify({"tasks": tasks[:10], "current_flow": {"layers": {"L1":"ready","L2":"ready","L3":"ready","L4":"ready","L5":"ready"}, "agents": agents[:10]}})

@app.route("/api/agents")
def api_agents():
    agents = []
    registry = r.smembers("agent:registry")
    for name in registry:
        card_raw = r.get(f"agent:card:{name}")
        card = json.loads(card_raw) if card_raw else {}
        # Check if any neuron is running
        import subprocess
        running = False
        try:
            pg = subprocess.run(["pgrep","-f",f"AGENT_NAME={name}"],capture_output=True,text=True)
            running = bool(pg.stdout.strip())
        except: pass
        agents.append({"name": name, "quadrant": card.get("quadrant","?"),
                      "running": running, "skills": card.get("skills",[])})
    return jsonify(agents)

@app.route("/api/scores")
def api_scores():
    scores = []
    for key in r.scan_iter("yaxiio:task:*", count=20):
        try:
            data = json.loads(r.get(key))
            result = data.get("result", data)
            l5 = result.get("l5_result", {})
            task_id = key.replace("yaxiio:task:", "")
            human = r.get(f"review:{task_id}")
            human_score = json.loads(human).get("overall") if human else None
            scores.append({"task_id": task_id, "ai_score": l5.get("overall","?"),
                          "human_score": human_score})
        except: pass
    return jsonify(scores[:10])

if __name__ == "__main__":
    print("Yaxiio Dashboard: http://0.0.0.0:3006")
    app.run(host="0.0.0.0", port=3006, debug=False)
