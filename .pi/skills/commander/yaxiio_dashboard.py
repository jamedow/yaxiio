"""
Yaxiio 监控面板 (Dashboard)
============================
实时查看系统全貌: 任务/Stream/Neuron/GC

用法:
  python3 yaxiio_dashboard.py          # 一次性快照
  python3 yaxiio_dashboard.py --watch  # 持续监控 (5s刷新)
"""

import json, os, time, sys

REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")


def get_redis():
    import redis
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS,
                       decode_responses=True, socket_connect_timeout=3)


def snapshot(r) -> dict:
    """采集一次系统全貌"""
    snap = {}

    # ── 任务 ──
    active_keys = r.smembers("yaxiio:task:active") or set()
    tasks = {"total": 0, "executing": 0, "done": 0, "failed": 0, "latest": []}
    for tid in list(active_keys)[:20]:
        try:
            raw = r.get(f"yaxiio:task:{tid}")
            if raw:
                d = json.loads(raw)
                status = d.get("status", "?")
                tasks["total"] += 1
                if status == "EXECUTING":
                    tasks["executing"] += 1
                elif status == "DONE":
                    tasks["done"] += 1
                elif status in ("FAILED", "error"):
                    tasks["failed"] += 1
                tasks["latest"].append({"id": tid, "status": status,
                                        "layer": d.get("current_layer", "-")})
        except Exception:
            pass
    tasks["latest"] = sorted(tasks["latest"], key=lambda x: x["id"])[-5:]
    snap["tasks"] = tasks

    # ── Stream 积压 ──
    streams = {}
    for stream, group in [("yaxiio:stream:L4", "agents-L4"),
                           ("yaxiio:stream:L4_response", "commander-response"),
                           ("yaxiio:stream:task_incoming", "commander-main")]:
        try:
            length = r.xlen(stream)
            pending_info = r.xpending(stream, group)
            pending = pending_info.get("pending", 0) if isinstance(pending_info, dict) else 0
            streams[stream.replace("yaxiio:stream:", "")] = {
                "length": length, "pending": pending,
                "health": "ok" if pending < 100 else ("warn" if pending < 500 else "critical"),
            }
        except Exception:
            streams[stream.replace("yaxiio:stream:", "")] = {"length": "?", "pending": "?", "health": "unknown"}
    snap["streams"] = streams

    # ── Neuron 状态 ──
    neurons = []
    neuron_keys = r.keys("agent:*:state")
    for key in (neuron_keys or []):
        try:
            raw = r.get(key)
            if raw:
                state = json.loads(raw)
                agent = key.split(":")[1]
                task = key.split(":")[2] if len(key.split(":")) > 3 else "-"
                neurons.append({
                    "agent": agent, "task": task,
                    "state": state.get("state", "?"),
                    "since": state.get("since", 0),
                })
        except Exception:
            pass
    snap["neurons"] = neurons[:10]

    # ── GC 状态 ──
    try:
        gc_stats = r.get("yaxiio:gc:stats")
        snap["gc"] = json.loads(gc_stats) if gc_stats else {"runs": 0}
    except Exception:
        snap["gc"] = {"runs": "?"}

    # ── Commander 心跳 ──
    try:
        cycle = r.get("yaxiio:debug:cycle")
        snap["commander"] = json.loads(cycle) if cycle else {"cycle": 0}
    except Exception:
        snap["commander"] = {"cycle": "?"}

    return snap


def render(snap: dict) -> str:
    """渲染为可读文本"""
    lines = []
    lines.append("╔══════════════════════════════════════════════╗")
    lines.append("║        Yaxiio 监控面板                        ║")
    lines.append("╠══════════════════════════════════════════════╣")

    # Commander
    c = snap.get("commander", {})
    lines.append(f"║ Commander: cycle={c.get('cycle','?')}, tasks={c.get('tasks','?')}")

    # Tasks
    t = snap.get("tasks", {})
    lines.append(f"║ 任务: 总计{t.get('total',0)} 执行中{t.get('executing',0)} 完成{t.get('done',0)} 失败{t.get('failed',0)}")
    for item in t.get("latest", []):
        lines.append(f"║   {item['id']}: {item['status']} @ {item['layer']}")

    # Streams
    lines.append("╟─ Streams ────────────────────────────────────")
    for name, info in snap.get("streams", {}).items():
        health_icon = {"ok": "✅", "warn": "⚠️", "critical": "🚨"}.get(info.get("health", ""), "❓")
        lines.append(f"║ {health_icon} {name}: {info.get('length','?')} msgs, {info.get('pending','?')} pending")

    # Neurons
    lines.append("╟─ Neurons ────────────────────────────────────")
    neurons = snap.get("neurons", [])
    if neurons:
        for n in neurons:
            lines.append(f"║ 🧠 {n['agent']}/{n['task']}: {n['state']}")
    else:
        lines.append("║ (无活跃神经元)")

    # GC
    gc = snap.get("gc", {})
    lines.append(f"╟─ GC: 运行{gc.get('runs','?')}次, stream_acked={gc.get('stream_acked','?')}, tasks_del={gc.get('tasks_deleted','?')}")

    lines.append("╚══════════════════════════════════════════════╝")
    return "\n".join(lines)


def main():
    r = get_redis()
    watch = "--watch" in sys.argv or "-w" in sys.argv

    if watch:
        try:
            while True:
                snap = snapshot(r)
                print("\033[2J\033[H")  # 清屏
                print(render(snap))
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n已退出")
    else:
        snap = snapshot(r)
        print(render(snap))
        return snap


if __name__ == "__main__":
    main()
