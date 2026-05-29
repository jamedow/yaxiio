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
"""
Dashboard 2.0 — 增强可观测性 + 智能告警
=========================================
- MetricsCollector: 实时 + 性能 + 成本 + 系统 四维指标
- AlertEngine: 5条告警规则 (成功率/队列/成本/失联/Redis)
- Flask API + 自动刷新可视化页面
- 读取 Commander V2 的 commander:* Redis 键（R1 合规）

与 v1 dashboard.py 共存，监听不同端口（默认 3003）。
"""

import json
import time
from datetime import datetime, timedelta
from typing import Optional

import redis
from flask import Flask, jsonify, render_template_string, request

try:
    from pymongo import MongoClient
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False


app = Flask(__name__)


# ═══════════════════════════════════════════════════════════════
# MetricsCollector ─ 四维指标采集
# ═══════════════════════════════════════════════════════════════

class MetricsCollector:
    """从 Commander V2 的 commander:* Redis 键采集指标。

    所有键均遵守 Constitution R1（commander:* 前缀，TTL 回收）。
    """

    def __init__(self, redis_client: redis.Redis,
                 mongo_client=None):
        self.redis = redis_client
        self.mongo = mongo_client
        self.metrics = {}
        self.alert_history = []
        self._init_metrics()

    def _init_metrics(self):
        self.metrics = {
            "real_time": {
                "agent_status": {},
                "task_queue_depth": 0,
                "active_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "degraded_tasks": 0,
            },
            "performance": {
                "avg_task_duration_ms": 0,
                "p95_task_duration_ms": 0,
                "success_rate": 1.0,
                "retry_rate": 0,
                "failover_count": 0,
            },
            "cost": {
                "total_tokens": 0,
                "total_cost_cny": 0,
                "cost_per_task": 0,
                "model_distribution": {},
            },
            "system": {
                "redis_status": "unknown",
                "mongodb_status": "unknown",
                "commander_uptime": 0,
                "agent_count": 0,
            },
        }

    def collect(self):
        """采集所有四维指标。"""
        self.metrics["real_time"]["agent_status"] = self._get_agent_status()
        self.metrics["real_time"]["task_queue_depth"] = (
            self.redis.llen("commander:task_queue")
        )
        self.metrics["real_time"]["active_tasks"] = (
            self._count_active_tasks()
        )

        # 今日统计（MongoDB 可选）
        if self.mongo:
            self._collect_daily_stats()
            self._collect_performance()
            self._collect_cost()

        # 系统状态
        self._collect_system()

    # ── Agent 状态 ──────────────────────────────────────────

    def _get_agent_status(self) -> dict:
        """收集所有活跃 + 有记录的 Agent 状态（R1 合规键）。"""
        status = {}

        # 活跃 Agent 集合
        agent_ids = self.redis.smembers("commander:agents:active")
        for aid in agent_ids:
            role = self.redis.hget(f"commander:agent:heartbeat:{aid}", "role") or ""
            state = self.redis.get(f"commander:agent:status:{aid}") or "unknown"
            last_hb = float(
                self.redis.hget(f"commander:agent:heartbeat:{aid}", "last_activity") or 0
            )
            uptime = int(time.time() - last_hb) if last_hb else 0

            status[aid] = {
                "role": role,
                "status": state,
                "uptime_seconds": uptime,
                "last_heartbeat_ago_s": int(time.time() - last_hb) if last_hb else -1,
            }

        # 也检查 by_role 表（包含非活跃但有记录的 Agent）
        by_role = self.redis.hgetall("commander:agent:status:by_role")
        for role_name, state in by_role.items():
            if role_name not in status:
                status[role_name] = {
                    "role": role_name,
                    "status": state,
                    "uptime_seconds": 0,
                    "last_heartbeat_ago_s": -1,
                }

        return status

    def _count_active_tasks(self) -> int:
        """估算活跃任务数（status key 为 running 的 Agent 数）。"""
        count = 0
        for key in self.redis.scan_iter("commander:agent:status:*"):
            if self.redis.get(key) == "running":
                count += 1
        return count

    # ── 每日统计（MongoDB）──────────────────────────────────

    def _collect_daily_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            self.metrics["real_time"]["completed_tasks"] = (
                self.mongo.agent_optimization_log.count_documents({
                    "date": today, "overallStatus": "success"
                })
            )
            self.metrics["real_time"]["failed_tasks"] = (
                self.mongo.agent_optimization_log.count_documents({
                    "date": today, "overallStatus": "failed"
                })
            )
            self.metrics["real_time"]["degraded_tasks"] = (
                self.mongo.degraded_tasks.count_documents({
                    "date": today
                })
            ) if "degraded_tasks" in self.mongo.list_collection_names() else 0
        except Exception:
            pass

    def _collect_performance(self):
        try:
            logs = list(
                self.mongo.agent_optimization_log.find()
                .sort("timestamp", -1).limit(1000)
            )
        except Exception:
            return

        if not logs:
            return

        durations = [log.get("totalDuration", 0) for log in logs
                     if log.get("totalDuration")]
        if durations:
            sorted_d = sorted(durations)
            self.metrics["performance"]["avg_task_duration_ms"] = (
                sum(durations) / len(durations)
            )
            self.metrics["performance"]["p95_task_duration_ms"] = (
                sorted_d[int(len(sorted_d) * 0.95)]
            )

        success_count = sum(1 for log in logs
                            if log.get("overallStatus") == "success")
        self.metrics["performance"]["success_rate"] = (
            success_count / len(logs)
        )

    def _collect_cost(self):
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            cost_logs = list(self.mongo.token_usage.find({"date": today}))
        except Exception:
            return

        self.metrics["cost"]["total_tokens"] = sum(
            log.get("tokens", 0) for log in cost_logs
        )
        self.metrics["cost"]["total_cost_cny"] = sum(
            log.get("cost", 0) for log in cost_logs
        )
        completed = self.metrics["real_time"]["completed_tasks"]
        if completed > 0:
            self.metrics["cost"]["cost_per_task"] = (
                self.metrics["cost"]["total_cost_cny"] / completed
            )

        # 模型分布
        dist = {}
        for log in cost_logs:
            model = log.get("model", "unknown")
            dist[model] = dist.get(model, 0) + log.get("tokens", 0)
        self.metrics["cost"]["model_distribution"] = dist

    # ── 系统状态 ────────────────────────────────────────────

    def _collect_system(self):
        # Redis 健康
        try:
            self.redis.ping()
            self.metrics["system"]["redis_status"] = "healthy"
        except Exception:
            self.metrics["system"]["redis_status"] = "down"

        # MongoDB 健康
        if self.mongo:
            try:
                self.mongo.command("ping")
                self.metrics["system"]["mongodb_status"] = "healthy"
            except Exception:
                self.metrics["system"]["mongodb_status"] = "down"
        else:
            self.metrics["system"]["mongodb_status"] = "unavailable"

        # Agent 数量
        self.metrics["system"]["agent_count"] = self.redis.scard(
            "commander:agents:active"
        )

        # 故障转移次数（7天窗口）
        self.metrics["performance"]["failover_count"] = self.redis.llen(
            "commander:log:failovers"
        )


# ═══════════════════════════════════════════════════════════════
# AlertEngine ─ 智能告警
# ═══════════════════════════════════════════════════════════════

class AlertEngine:
    """五条告警规则，覆盖五大优化引擎的异常场景。"""

    ALERT_RULES = [
        {
            "name": "任务成功率骤降",
            "condition": (
                lambda m: (
                    m["performance"]["success_rate"] < 0.85
                    and m["real_time"]["completed_tasks"] > 10
                )
            ),
            "severity": "critical",
            "action": "自动回滚最近一次策略变更 + 发送钉钉告警",
            "related_engine": "ABTester",
        },
        {
            "name": "队列深度过高",
            "condition": (
                lambda m: m["real_time"]["task_queue_depth"] > 10
            ),
            "severity": "warning",
            "action": "自动扩容 Agent + 发送告警",
            "related_engine": "AutoScaler",
        },
        {
            "name": "成本异常飙升",
            "condition": (
                lambda m: m["cost"]["total_cost_cny"] > 50
            ),
            "severity": "warning",
            "action": "限制低优先级任务 + 发送告警",
            "related_engine": "TaskDegradation",
        },
        {
            "name": "Agent 大面积失联",
            "condition": (
                lambda m: (
                    sum(1 for a in m["real_time"]["agent_status"].values()
                        if a["status"] not in ("running", "online"))
                    > max(len(m["real_time"]["agent_status"]) * 0.5, 1)
                )
            ),
            "severity": "critical",
            "action": "紧急重启所有 Agent + 触发故障转移 + 发送钉钉告警",
            "related_engine": "AgentFailover",
        },
        {
            "name": "Redis 连接异常",
            "condition": (
                lambda m: m["system"]["redis_status"] == "down"
            ),
            "severity": "critical",
            "action": "触发 Sentinel 故障转移 + 暂停新任务 + 发送钉钉告警",
            "related_engine": "RedisHAWrapper",
        },
    ]

    def __init__(self):
        self.active_alerts = []
        self.resolved_alerts = []

    def evaluate(self, metrics: dict) -> list:
        """评估所有告警规则，返回当前触发的告警。"""
        triggered = []
        for rule in self.ALERT_RULES:
            try:
                if rule["condition"](metrics):
                    alert = {
                        "name": rule["name"],
                        "severity": rule["severity"],
                        "action": rule["action"],
                        "related_engine": rule["related_engine"],
                        "triggered_at": datetime.now().isoformat(),
                    }
                    triggered.append(alert)

                    # 避免重复记录
                    if alert["name"] not in (a["name"] for a in self.active_alerts):
                        self.active_alerts.append(alert)
            except Exception:
                # 指标不完整时静默跳过
                continue

        # 已恢复的告警：当前未触发但从 active 列表移除
        current_names = {a["name"] for a in triggered}
        resolved = [a for a in self.active_alerts
                    if a["name"] not in current_names]
        self.resolved_alerts.extend(resolved)
        self.active_alerts = [a for a in self.active_alerts
                              if a["name"] in current_names]

        return triggered


# ═══════════════════════════════════════════════════════════════
# Flask API 端点
# ═══════════════════════════════════════════════════════════════

collector: Optional[MetricsCollector] = None
alert_engine = AlertEngine()


@app.route("/api/dashboard/realtime")
def get_realtime_metrics():
    """实时指标（含告警评估）。"""
    if collector is None:
        return jsonify({"error": "collector not initialized"}), 503

    collector.collect()
    collector.metrics["alerts"] = alert_engine.evaluate(collector.metrics)
    collector.metrics["collected_at"] = datetime.now().isoformat()
    return jsonify(collector.metrics)


@app.route("/api/dashboard/trends")
def get_trends():
    """历史趋势（最近 N 小时 -> 最多200条）。"""
    hours = int(request.args.get("hours", 24))
    if collector is None or collector.mongo is None:
        return jsonify({"error": "MongoDB unavailable"}), 503

    try:
        logs = list(collector.mongo.agent_optimization_log.find({
            "timestamp": {"$gte": datetime.now() - timedelta(hours=hours)}
        }).sort("timestamp", -1).limit(200))

        # 序列化 ObjectId
        for log in logs:
            log["_id"] = str(log["_id"])

        return jsonify(logs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dashboard/alerts")
def get_alerts():
    """活跃 + 已解决告警。"""
    return jsonify({
        "active": alert_engine.active_alerts,
        "resolved": alert_engine.resolved_alerts[-50:],  # 最近50条
    })


@app.route("/api/dashboard/failovers")
def get_failovers():
    """近期故障转移记录。"""
    if collector is None:
        return jsonify([])

    entries = collector.redis.lrange("commander:log:failovers", -30, -1)
    return jsonify([json.loads(e) for e in entries])


@app.route("/dashboard")
def dashboard_ui():
    """可视化仪表盘页面。"""
    return render_template_string(DASHBOARD_HTML)


# ═══════════════════════════════════════════════════════════════
# HTML 仪表盘（Brand 色系）
# ═══════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="utf-8">
    <meta http-equiv="refresh" content="3">
    <title>LightingMetal Agent Dashboard 2.0</title>
    <style>
        :root {
            --bg: #14110F;
            --card-bg: #1E1A17;
            --gold: #D4A843;
            --gold-bright: #E8A838;
            --border: #3A3028;
            --text: #C4B89E;
            --text-dim: #7A7060;
            --green: #7CCD7C;
            --red: #E34234;
            --orange: #E8A838;
            --blue: #5B9BD5;
        }
        *{margin:0;padding:0;box-sizing:border-box}
        body {
            background: var(--bg);
            color: var(--text);
            font-family: 'Courier New', 'Source Code Pro', monospace;
            padding: 20px;
            min-height: 100vh;
        }
        h1 {
            text-align: center;
            color: var(--gold);
            font-size: 20px;
            margin-bottom: 6px;
            letter-spacing: 2px;
        }
        .subtitle {
            text-align: center;
            color: var(--text-dim);
            font-size: 11px;
            margin-bottom: 18px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
            gap: 16px;
            max-width: 1400px;
            margin: 0 auto;
        }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 16px;
        }
        .card h2 {
            font-size: 13px;
            color: var(--gold);
            margin: 0 0 12px 0;
            border-bottom: 1px solid var(--border);
            padding-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 0;
            font-size: 13px;
            border-bottom: 1px solid rgba(255,255,255,0.03);
        }
        .metric .label { color: var(--text-dim); }
        .metric .value {
            font-weight: bold;
            color: var(--gold-bright);
            font-size: 14px;
        }
        .agent-row {
            display: flex;
            align-items: center;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        }
        .agent-dot {
            width: 10px; height: 10px;
            border-radius: 50%;
            margin-right: 10px;
            flex-shrink: 0;
        }
        .agent-dot.running, .agent-dot.online { background: var(--green); box-shadow: 0 0 6px var(--green); }
        .agent-dot.dead, .agent-dot.offline { background: var(--red); box-shadow: 0 0 4px var(--red); }
        .agent-dot.unknown { background: var(--text-dim); }
        .agent-name { flex: 1; font-size: 13px; }
        .agent-meta { font-size: 10px; color: var(--text-dim); text-align: right; }
        .alert-card {
            border-left: 3px solid var(--red);
            padding: 10px 12px;
            margin-bottom: 8px;
            background: rgba(227,67,52,0.06);
            border-radius: 0 6px 6px 0;
        }
        .alert-card.warning { border-left-color: var(--orange); background: rgba(232,168,56,0.06); }
        .alert-card h3 { font-size: 12px; margin-bottom: 4px; }
        .alert-card .action { font-size: 11px; color: var(--text-dim); }
        .alert-card .engine { font-size: 10px; color: var(--gold); margin-top: 3px; }
        .status-badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
            font-weight: bold;
        }
        .status-badge.healthy { background: rgba(124,205,124,0.15); color: var(--green); }
        .status-badge.down { background: rgba(227,67,52,0.15); color: var(--red); animation: blink 1.2s infinite; }
        .status-badge.unavailable { background: rgba(122,112,96,0.12); color: var(--text-dim); }
        @keyframes blink { 50% { opacity: 0.4; } }
        .no-data { color: var(--text-dim); font-size: 12px; padding: 10px; text-align: center; }
        .refresh-time { text-align: center; font-size: 10px; color: var(--text-dim); margin-top: 16px; }
    </style>
</head>
<body>
    <h1>⚡ LightingMetal Agent Dashboard 2.0</h1>
    <div class="subtitle">五大优化引擎 · 四维可观测性 · 智能告警</div>

    <div class="grid">
        <!-- Agent 集群 -->
        <div class="card">
            <h2>🤖 Agent 集群</h2>
            <div id="agent-panel">Loading...</div>
        </div>

        <!-- 性能指标 -->
        <div class="card">
            <h2>📊 性能</h2>
            <div id="perf-panel">Loading...</div>
        </div>

        <!-- 成本 -->
        <div class="card">
            <h2>💰 成本</h2>
            <div id="cost-panel">Loading...</div>
        </div>

        <!-- 系统状态 -->
        <div class="card">
            <h2>🖥 系统</h2>
            <div id="sys-panel">Loading...</div>
        </div>

        <!-- 任务队列 -->
        <div class="card">
            <h2>📋 任务概览</h2>
            <div id="task-panel">Loading...</div>
        </div>

        <!-- 告警 -->
        <div class="card" style="grid-column: 1/-1;">
            <h2>🚨 活跃告警</h2>
            <div id="alert-panel">Loading...</div>
        </div>
    </div>

    <div class="refresh-time" id="refresh-time"></div>

    <script>
        async function refresh() {
            try {
                const res = await fetch('/api/dashboard/realtime');
                const data = await res.json();

                // ── Agent 状态 ──
                const agents = data.real_time?.agent_status || {};
                let agentHtml = '';
                for (const [id, info] of Object.entries(agents)) {
                    const cls = info.status === 'running' ? 'running' : (info.status === 'dead' ? 'dead' : 'unknown');
                    const hb = info.last_heartbeat_ago_s >= 0 ? `${info.last_heartbeat_ago_s}s ago` : 'N/A';
                    agentHtml += `
                        <div class="agent-row">
                            <div class="agent-dot ${cls}"></div>
                            <div class="agent-name">${info.role || id}</div>
                            <div class="agent-meta">${info.status} · ${hb}</div>
                        </div>`;
                }
                document.getElementById('agent-panel').innerHTML = agentHtml || '<div class="no-data">无 Agent 在线</div>';

                // ── 性能 ──
                const perf = data.performance || {};
                document.getElementById('perf-panel').innerHTML = `
                    <div class="metric"><span class="label">成功率</span><span class="value">${(perf.success_rate * 100).toFixed(1)}%</span></div>
                    <div class="metric"><span class="label">平均耗时</span><span class="value">${(perf.avg_task_duration_ms / 1000).toFixed(1)}s</span></div>
                    <div class="metric"><span class="label">P95 耗时</span><span class="value">${(perf.p95_task_duration_ms / 1000).toFixed(1)}s</span></div>
                    <div class="metric"><span class="label">故障转移</span><span class="value">${perf.failover_count || 0} 次</span></div>
                `;

                // ── 成本 ──
                const cost = data.cost || {};
                document.getElementById('cost-panel').innerHTML = `
                    <div class="metric"><span class="label">今日 Token</span><span class="value">${(cost.total_tokens || 0).toLocaleString()}</span></div>
                    <div class="metric"><span class="label">今日成本</span><span class="value">¥${(cost.total_cost_cny || 0).toFixed(2)}</span></div>
                    <div class="metric"><span class="label">单任务成本</span><span class="value">¥${(cost.cost_per_task || 0).toFixed(4)}</span></div>
                `;

                // ── 系统 ──
                const sys = data.system || {};
                const redisCls = sys.redis_status === 'healthy' ? 'healthy' : 'down';
                const mongoCls = sys.mongodb_status === 'healthy' ? 'healthy' : (sys.mongodb_status === 'unavailable' ? 'unavailable' : 'down');
                document.getElementById('sys-panel').innerHTML = `
                    <div class="metric"><span class="label">Redis</span><span class="status-badge ${redisCls}">${sys.redis_status}</span></div>
                    <div class="metric"><span class="label">MongoDB</span><span class="status-badge ${mongoCls}">${sys.mongodb_status}</span></div>
                    <div class="metric"><span class="label">Agent 数量</span><span class="value">${sys.agent_count || 0}</span></div>
                `;

                // ── 任务概览 ──
                const rt = data.real_time || {};
                document.getElementById('task-panel').innerHTML = `
                    <div class="metric"><span class="label">队列深度</span><span class="value">${rt.task_queue_depth || 0}</span></div>
                    <div class="metric"><span class="label">活跃任务</span><span class="value">${rt.active_tasks || 0}</span></div>
                    <div class="metric"><span class="label">完成</span><span class="value" style="color:var(--green)">${rt.completed_tasks || 0}</span></div>
                    <div class="metric"><span class="label">失败</span><span class="value" style="color:var(--red)">${rt.failed_tasks || 0}</span></div>
                    <div class="metric"><span class="label">降级</span><span class="value" style="color:var(--orange)">${rt.degraded_tasks || 0}</span></div>
                `;

                // ── 告警 ──
                const alerts = data.alerts || [];
                let alertHtml = '';
                if (alerts.length === 0) {
                    alertHtml = '<div class="no-data" style="color:var(--green)">✅ 无活跃告警</div>';
                } else {
                    for (const a of alerts) {
                        alertHtml += `
                            <div class="alert-card ${a.severity}">
                                <h3>${a.severity === 'critical' ? '🔴' : '🟡'} ${a.name}</h3>
                                <div class="action">${a.action}</div>
                                <div class="engine">→ 相关引擎: ${a.related_engine}</div>
                            </div>`;
                    }
                }
                document.getElementById('alert-panel').innerHTML = alertHtml;

                // 刷新时间
                document.getElementById('refresh-time').textContent =
                    `最后刷新: ${data.collected_at || 'now'} · 每3秒自动刷新`;
            } catch (e) {
                console.error('Dashboard refresh error:', e);
            }
        }
        refresh();
    </script>
</body>
</html>
"""


# ═══════════════════════════════════════════════════════════════
# 启动
# ═══════════════════════════════════════════════════════════════

def init_collector(mongo_uri: Optional[str] = None):
    """初始化指标采集器（Redis + 可选 MongoDB）。"""
    global collector

    r = redis.Redis(
        host="127.0.0.1", port=6379,
        password="Lt@114514!", decode_responses=True,
    )

    mongo = None
    if mongo_uri and HAS_MONGO:
        try:
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
            mongo = client["lightingmetal"]
        except Exception as e:
            print(f"[Dashboard] MongoDB 连接失败 (将跳过持久化指标): {e}")

    collector = MetricsCollector(r, mongo)
    return collector


if __name__ == "__main__":
    import sys

    mongo_uri = sys.argv[1] if len(sys.argv) > 1 else None
    if not mongo_uri:
        print("[Dashboard] 未提供 MongoDB URI，仅采集 Redis 指标")
        print("[Dashboard] 用法: python3 dashboard_v2.py 'mongodb://user:pass@host:27017/'")

    init_collector(mongo_uri)

    port = int(sys.argv[2]) if len(sys.argv) > 2 else 3003
    print(f"[Dashboard 2.0] 🚀 启动在 http://0.0.0.0:{port}/dashboard")
    app.run(host="0.0.0.0", port=port, debug=False)
