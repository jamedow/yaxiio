#!/usr/bin/env python3
"""Agent v2 — 稳健的 Redis PubSub 订阅循环"""
import json, os, sys, time

AGENT_NAME = sys.argv[1] if len(sys.argv) > 1 else "agent"
REDIS_HOST = os.environ.get("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_PASS = os.environ.get("REDIS_PASSWORD", "Yaxiio2026")

CHANNEL = f"lightingmetal:agent:{AGENT_NAME}"
CONTROL = "lightingmetal:agent:commander"

import redis as rlib
r = rlib.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASS, decode_responses=True)

task_count = 0
print(f"[{AGENT_NAME}] 启动 (Python), 频道: {CHANNEL}", flush=True)

pubsub = r.pubsub()
pubsub.subscribe(CHANNEL, CONTROL)

for msg in pubsub.listen():
    if msg["type"] != "message":
        continue
    
    try:
        data = json.loads(msg["data"])
    except json.JSONDecodeError:
        continue
    
    msg_to = data.get("to", "")
    msg_type = data.get("type", "")
    msg_from = data.get("from", "")
    task_id = data.get("taskId", "")
    
    if msg_to not in (AGENT_NAME, "*", "all"):
        continue
    
    if msg_type == "shutdown":
        print(f"[{AGENT_NAME}] 收到关闭指令", flush=True)
        break
    
    elif msg_type == "heartbeat_check":
        r.publish(CONTROL, json.dumps({
            "from": AGENT_NAME, "type": "heartbeat",
            "payload": {"status": "alive", "tasks": task_count}
        }))
    
    elif msg_type in ("task", "request"):
        task_count += 1
        action = data.get("payload", {}).get("action", "unknown")
        print(f"[{AGENT_NAME}] 📋 任务 #{task_count}: {task_id} ← {msg_from} ({action})", flush=True)
        
        # 回复完成
        reply_to = data.get("replyTo", "commander")
        r.publish(f"lightingmetal:agent:{reply_to}", json.dumps({
            "from": AGENT_NAME, "to": msg_from, "type": "response",
            "taskId": task_id, "payload": {"status": "received", "note": f"Agent {AGENT_NAME} 已接收"}
        }))
    
    elif msg_type == "response":
        status = data.get("payload", {}).get("status", "?")
        print(f"[{AGENT_NAME}] 📬 回复: {task_id} ← {msg_from} [{status}]", flush=True)

print(f"[{AGENT_NAME}] 下线, 处理了{task_count}个任务", flush=True)
