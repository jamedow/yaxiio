#!/usr/bin/env python3
"""
Yaxiio 评分面板 v1.0 — 人类评审仪表盘
========================================
- 待评审任务列表
- 按 Agent 维度的评分表单
- 评价者信用分与统计
- AI vs 人类分差异常告警
- 端口 3005
"""

import os, json, time
import redis as _r
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "$REDIS_PASSWORD")

r = _r.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)

# ── 页面模板 ──
HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Yaxiio 评分面板</title>
<style>
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--muted:#8b949e;--green:#3fb950;--red:#f85149;--yellow:#d2991d;--blue:#58a6ff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);line-height:1.5}
header{background:var(--card);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;justify-content:space-between;align-items:center}
header h1{font-size:18px;color:var(--blue)}
.stats{display:flex;gap:16px;font-size:13px;color:var(--muted)}
.stats span{color:var(--text);font-weight:600}
main{max-width:1200px;margin:0 auto;padding:24px}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:24px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:20px}
.card h2{font-size:15px;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid var(--border)}
.task-item{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--border)}
.task-item:last-child{border-bottom:none}
.task-id{font-family:monospace;font-size:13px;color:var(--blue)}
.task-agent{font-size:12px;color:var(--muted)}
.task-score{font-weight:bold;font-size:14px}
.score-ai{color:var(--yellow)}.score-human{color:var(--green)}.score-pending{color:var(--muted)}
.btn{padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--card);color:var(--text);cursor:pointer;font-size:13px;transition:all .2s}
.btn:hover{background:var(--blue);color:#fff;border-color:var(--blue)}
.btn-sm{padding:4px 10px;font-size:12px}
.anomaly{background:rgba(248,81,73,.1);border-color:var(--red)}
.anomaly h2{color:var(--red)}
.review-form{display:flex;flex-direction:column;gap:12px}
.review-form label{font-size:13px;color:var(--muted)}
.review-form input[type=range]{width:100%;accent-color:var(--blue)}
.review-form .range-val{font-size:12px;color:var(--blue);text-align:right}
.review-form textarea{background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px;border-radius:6px;font-size:13px;resize:vertical}
.dim-row{display:flex;align-items:center;gap:12px}
.dim-row .dim-name{flex:0 0 120px;font-size:13px}
.dim-row input[type=range]{flex:1}
.dim-row .dim-val{flex:0 0 40px;text-align:right;font-size:13px;color:var(--blue)}
.notice{padding:40px;text-align:center;color:var(--muted)}
.refresh{font-size:12px;color:var(--muted);text-align:right;margin-top:10px}
</style>
</head>
<body>
<header>
  <h1>🧠 Yaxiio 评分面板</h1>
  <div class="stats">
    <span>待评审: {{pending_count}}</span>
    <span>异常: {{anomaly_count}}</span>
    <span>信用分: {{reviewer_credit}}</span>
  </div>
</header>
<main>
<div class="grid">
  <div>
    <!-- 待评审任务 -->
    <div class="card">
      <h2>📋 待评审任务</h2>
      {% for task in tasks %}
      <div class="task-item">
        <div>
          <div class="task-id">{{task.id}}</div>
          <div class="task-agent">{{task.agent}} · {{task.action}}</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          {% if task.human_score %}
          <span class="task-score score-human">{{task.human_score}}</span>
          {% else %}
          <span class="task-score score-ai">AI {{task.ai_score}}</span>
          {% endif %}
          <button class="btn btn-sm" onclick="reviewTask('{{task.id}}','{{task.agent}}')">评分</button>
        </div>
      </div>
      {% endfor %}
      {% if not tasks %}
      <div class="notice">暂无待评审任务</div>
      {% endif %}
    </div>
    
    <!-- 评分表单 -->
    <div class="card" id="reviewCard" style="display:none">
      <h2>✍️ 评分: <span id="reviewTaskId"></span></h2>
      <form class="review-form" id="reviewForm" onsubmit="submitReview(event)">
        <div id="dimensions"></div>
        <label>评价备注</label>
        <textarea id="comment" rows="3" placeholder="可选的评价备注..."></textarea>
        <button type="submit" class="btn" style="align-self:flex-end">提交评分</button>
      </form>
    </div>
  </div>
  
  <div>
    <!-- 异常告警 -->
    <div class="card anomaly">
      <h2>⚠️ 评分异常</h2>
      {% for a in anomalies %}
      <div class="task-item">
        <div>
          <div class="task-id">{{a.task_id}}</div>
          <div style="font-size:12px;color:var(--muted)">AI {{a.ai_score}} ↔ 人 {{a.human_score}} (差{{a.gap}})</div>
        </div>
      </div>
      {% endfor %}
      {% if not anomalies %}
      <div class="notice" style="font-size:13px">无异常 · 人机评分一致</div>
      {% endif %}
    </div>
    
    <!-- 评价者统计 -->
    <div class="card">
      <h2>👤 评价者统计</h2>
      <div style="display:flex;flex-direction:column;gap:8px;font-size:13px">
        <div>信用分: <strong style="color:var(--green)">{{reviewer_credit}}</strong></div>
        <div>累计评价: <strong>{{reviewer_count}}</strong> 次</div>
        <div>最近评价:</div>
        {% for rev in recent_reviews %}
        <div style="padding:6px;background:var(--bg);border-radius:4px;font-size:12px">
          {{rev.task_id}} · {{rev.overall}}分 · {{rev.dimensions|join(', ') if rev.dimensions else ''}}
        </div>
        {% endfor %}
      </div>
    </div>
  </div>
</div>
<div class="refresh">🔄 自动刷新 · {{now}}</div>
</main>
<script>
const AGENT_DIMS = {
  '审计官': ['accuracy','completeness','actionability','clarity'],
  '翻译官': ['accuracy','terminology','fluency','speed'],
  'LM内容工程师': ['completeness','correctness','efficiency'],
  '品牌策略师': ['insight','actionability','alignment'],
  'UI/UX设计师': ['visual','consistency','conversion','mobile'],
  '前端工程师': ['correctness','speed','stability'],
  '售前经理': ['accuracy','completeness','professionalism'],
};
const DEFAULT_DIMS = ['accuracy','completeness','clarity'];
let currentTask = '', currentAgent = '';

function reviewTask(id, agent) {
  currentTask = id; currentAgent = agent;
  document.getElementById('reviewTaskId').textContent = id + ' (' + agent + ')';
  let dims = AGENT_DIMS[agent] || DEFAULT_DIMS;
  let html = '';
  dims.forEach(d => {
    html += '<div class="dim-row"><span class="dim-name">'+d+'</span>'+
      '<input type="range" min="1" max="10" value="7" id="dim_'+d+'" oninput="document.getElementById(\'val_'+d+'\').textContent=this.value">'+
      '<span class="dim-val" id="val_'+d+'">7</span></div>';
  });
  document.getElementById('dimensions').innerHTML = html;
  document.getElementById('reviewCard').style.display = 'block';
}

function submitReview(e) {
  e.preventDefault();
  let dims = AGENT_DIMS[currentAgent] || DEFAULT_DIMS;
  let scores = {};
  dims.forEach(d => { scores[d] = parseInt(document.getElementById('dim_'+d).value); });
  let comment = document.getElementById('comment').value;
  let overall = Math.round(Object.values(scores).reduce((a,b)=>a+b,0) / dims.length * 10) / 10;
  
  fetch('/api/review', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({task_id:currentTask, reviewer_id:'jamedow', scores:scores, overall:overall, comment:comment})
  }).then(r=>r.json()).then(d=>{
    if(d.status==='ok') location.reload();
    else alert('提交失败: '+d.error);
  });
}
setTimeout(()=>location.reload(), 30000);
</script>
</body>
</html>"""

# ── 路由 ──
@app.route("/")
def index():
    tasks = []
    # Get recent completed tasks
    for key in r.scan_iter("yaxiio:task:*", count=20):
        try:
            raw = r.get(key)
            data = json.loads(raw)
            result = data.get("result", data)
            if data.get("status") == "DONE" or result.get("status") == "DONE":
                task_id = key.replace("yaxiio:task:", "")
                l5 = result.get("l5_result", {})
                ai_score = l5.get("overall", result.get("final_score", "?"))
                action = result.get("action", data.get("action", "?"))
                # Check if already reviewed
                human_review = r.get(f"review:{task_id}")
                human_score = None
                if human_review:
                    hr = json.loads(human_review)
                    human_score = hr.get("overall")
                agents = []
                sr = result.get("subtask_results", {})
                for res in sr.values():
                    a = res.get("agent", "")
                    if a and a not in agents and not a.startswith("_"):
                        agents.append(a)
                tasks.append({
                    "id": task_id, "agent": ", ".join(agents[:2]) or "unknown",
                    "action": str(action)[:40],
                    "ai_score": ai_score,
                    "human_score": human_score
                })
        except:
            pass
    tasks.sort(key=lambda t: t["id"], reverse=True)
    tasks = tasks[:20]
    
    pending = [t for t in tasks if t["human_score"] is None]
    
    anomalies_raw = r.lrange("review:anomalies", 0, 9)
    anomalies = [json.loads(a) for a in anomalies_raw]
    
    profile = r.hgetall("reviewer:jamedow:profile")
    credit = round(float(profile.get("credit", 0.8)), 2)
    count = int(profile.get("review_count", 0))
    
    recent_raw = r.lrange("reviewer:jamedow:history", 0, 4)
    recent = [json.loads(rv) for rv in recent_raw]
    
    return render_template_string(HTML,
        tasks=tasks,
        pending_count=len(pending),
        anomaly_count=len(anomalies),
        reviewer_credit=credit,
        reviewer_count=count,
        anomalies=anomalies[:10],
        recent_reviews=recent[:5],
        now=time.strftime("%H:%M:%S")
    )

@app.route("/api/review", methods=["POST"])
def submit_review():
    data = request.get_json()
    task_id = data.get("task_id", "")
    reviewer_id = data.get("reviewer_id", "anonymous")
    scores = data.get("scores", {})
    overall = data.get("overall", sum(scores.values()) / max(len(scores), 1))
    comment = data.get("comment", "")
    
    review = {
        "task_id": task_id, "reviewer": "human", "reviewer_id": reviewer_id,
        "scores": scores, "overall": overall, "comment": comment,
        "reviewed_at": time.time(), "dimensions": list(scores.keys())
    }
    r.setex(f"review:{task_id}", 86400 * 30, json.dumps(review, ensure_ascii=False))
    r.lpush(f"reviewer:{reviewer_id}:history", json.dumps(review, ensure_ascii=False))
    
    # Update credit
    profile = r.hgetall(f"reviewer:{reviewer_id}:profile")
    old_credit = float(profile.get("credit", 0.8))
    old_count = int(profile.get("review_count", 0))
    new_credit = round(old_credit * 0.95 + 0.05, 3)
    r.hset(f"reviewer:{reviewer_id}:profile", mapping={
        "credit": str(new_credit), "review_count": str(old_count + 1),
        "last_review_at": str(time.time())
    })
    
    # Check anomaly
    task_raw = r.get(f"yaxiio:task:{task_id}")
    if task_raw:
        task = json.loads(task_raw)
        result = task.get("result", task)
        ai_score = result.get("l5_result", {}).get("overall", result.get("final_score", 5))
        if abs(overall - ai_score) > 3:
            anomaly = {"task_id": task_id, "ai_score": ai_score, "human_score": overall,
                       "gap": abs(ai_score - overall), "reviewer": reviewer_id, "ts": time.time()}
            r.lpush("review:anomalies", json.dumps(anomaly, ensure_ascii=False))
    
    return jsonify({"status": "ok", "overall": overall})

@app.route("/api/tasks")
def api_tasks():
    tasks = []
    for key in r.scan_iter("yaxiio:task:*", count=50):
        try:
            raw = r.get(key)
            data = json.loads(raw)
            task_id = key.replace("yaxiio:task:", "")
            result = data.get("result", data)
            l5 = result.get("l5_result", {})
            ai_score = l5.get("overall", result.get("final_score", "?"))
            human_raw = r.get(f"review:{task_id}")
            human_score = json.loads(human_raw).get("overall") if human_raw else None
            tasks.append({"id": task_id, "ai_score": ai_score, "human_score": human_score,
                         "status": data.get("status")})
        except:
            pass
    return jsonify(tasks)

if __name__ == "__main__":
    print("🧠 Yaxiio 评分面板 v1.0")
    print("   地址: http://0.0.0.0:3005")
    app.run(host="0.0.0.0", port=3005, debug=False)
