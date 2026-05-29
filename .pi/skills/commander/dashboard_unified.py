import os, json, time, redis as _r
from flask import Flask, jsonify, render_template_string, send_from_directory

app = Flask(__name__, static_folder="/opt/commander/dashboard", static_url_path="")
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")
r = _r.Redis(protocol=2, host="127.0.0.1", port=6379, password=REDIS_PASS, decode_responses=True)

# ── Static files ──
@app.route("/")
def index():
    return render_template_string(NAV_HTML)

NAV_HTML = """<!DOCTYPE html>
<html lang="zh">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yaxiio Console</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0d1117;color:#c9d1d9;min-height:100vh;display:flex;align-items:center;justify-content:center}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;max-width:1000px;padding:40px}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:28px;text-decoration:none;color:var(--c);transition:all .3s;display:flex;flex-direction:column;gap:10px}
.card:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.3);border-color:var(--c)}
.card h2{font-size:18px}.card p{font-size:13px;color:#8b949e;line-height:1.5}
.card .icon{font-size:32px}
.card.blue{--c:#58a6ff}.card.green{--c:#3fb950}.card.yellow{--c:#d2991d}.card.purple{--c:#bc8cff}.card.red{--c:#f85149}
.header{text-align:center;margin-bottom:10px}
.header h1{font-size:24px;color:#58a6ff}.header p{font-size:14px;color:#8b949e;margin-top:6px}
</style></head>
<body>
<div style="text-align:center">
<div class="header"><h1>Yaxiio Console</h1><p>Agent Orchestration System</p></div>
<div class="grid">
<a href="/dashboard" class="card blue">
    <span class="icon">◈</span>
    <h2>Workflow Dashboard</h2>
    <p>Real-time L1-L5 topology, Agent status, task flow visualization</p>
</a>
<a href="/scores" class="card green">
    <span class="icon">◆</span>
    <h2>Human Review</h2>
    <p>Score agent outputs, compare AI vs human ratings, track reviewer credit</p>
</a>
<a href="/api/agents" class="card yellow" target="_blank">
    <span class="icon">◎</span>
    <h2>Agent Registry</h2>
    <p>View all 11 agents, capability cards, quadrant assignments</p>
</a>
<a href="/api/workflow" class="card purple" target="_blank">
    <span class="icon">⬡</span>
    <h2>Workflow API</h2>
    <p>Current task state, subtask results, agent status in JSON</p>
</a>
<a href="/api/scores" class="card red" target="_blank">
    <span class="icon">▣</span>
    <h2>Score History</h2>
    <p>Recent AI scores, human reviews, anomaly detection</p>
</a>
</div>
<p style="margin-top:24px;font-size:12px;color:#30363d">Yaxiio v1.7 · AGPLv3</p>
</div>
</body></html>"""

@app.route("/dashboard")
def dashboard_page():
    return app.send_static_file("index.html")

@app.route("/scores")
def scores_page():
    return app.send_static_file("scores.html")

# ── APIs ──
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
    
    agents = []
    for key in r.scan_iter("agent:*:state", count=20):
        try:
            name = key.split(":")[1]
            state = json.loads(r.get(key) or "{}")
            agents.append({"name": name, "state": state.get("state","idle"), "progress": state.get("progress",0)})
        except: pass
    
    # Add tools from capability cards
    for a in agents:
        card_raw = r.get(f"agent:card:{a['name']}")
        if card_raw:
            a["tools"] = json.loads(card_raw).get("tools", [])
            a["quadrant"] = json.loads(card_raw).get("quadrant", "")
    
    return jsonify({"tasks": tasks[:10], "current_flow": {"layers": {"L1":"ready","L2":"ready","L3":"ready","L4":"ready","L5":"ready"}, "agents": agents[:10]}})

@app.route("/api/agents")
def api_agents():
    agents = []
    for name in r.smembers("agent:registry"):
        card_raw = r.get(f"agent:card:{name}")
        card = json.loads(card_raw) if card_raw else {}
        import subprocess
        running = False
        try:
            pg = subprocess.run(["pgrep","-f",f"AGENT_NAME={name}"],capture_output=True,text=True)
            running = bool(pg.stdout.strip())
        except: pass
        agents.append({"name": name, "quadrant": card.get("quadrant","?"), "running": running, "skills": card.get("skills",[]), "tools": card.get("tools",[])})
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
            scores.append({"task_id": task_id, "ai_score": l5.get("overall","?"), "human_score": human_score})
        except: pass
    return jsonify(scores[:15])

@app.route("/api/review", methods=["POST"])
def submit_review():
    data = request.get_json()
    task_id = data.get("task_id","")
    reviewer_id = data.get("reviewer_id","anonymous")
    scores = data.get("scores",{})
    overall = data.get("overall", sum(scores.values())/max(len(scores),1))
    comment = data.get("comment","")
    review = {"task_id":task_id,"reviewer":"human","reviewer_id":reviewer_id,"scores":scores,"overall":overall,"comment":comment,"reviewed_at":time.time()}
    r.setex(f"review:{task_id}", 86400*30, json.dumps(review, ensure_ascii=False))
    r.lpush(f"reviewer:{reviewer_id}:history", json.dumps(review, ensure_ascii=False))
    return jsonify({"status":"ok","overall":overall})

if __name__ == "__main__":
    print("Yaxiio Dashboard: http://0.0.0.0:3004")
    app.run(host="0.0.0.0", port=3004, debug=False)
