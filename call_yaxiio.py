#!/usr/bin/env python3
""" call_yaxiio <消息> — 直接和雅溪对话 """
import redis, json, time, sys

HOST = "127.0.0.1"
PASS = os.environ.get("REDIS_PASSWORD", "")

def ask(msg):
    r = redis.Redis(host=HOST, port=6379, password=PASS, decode_responses=True)
    ps = r.pubsub()
    ps.subscribe("lightingmetal:agent:zelda")
    r.publish("lightingmetal:agent:commander", json.dumps({
        "from": "zelda", "to": "commander", "type": "task",
        "taskId": f"msg-{int(time.time())}", "replyTo": "zelda",
        "payload": {"task": msg}
    }))
    t0 = time.time()
    while time.time() - t0 < 45:
        m = ps.get_message(timeout=1)
        if m and m["type"] == "message":
            d = json.loads(m["data"])
            return d.get("payload", {}).get("result", "")
    return "(超时)"

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 call_yaxiio.py '你的消息'")
        sys.exit(1)
    msg = " ".join(sys.argv[1:])
    print("雅溪:", ask(msg))
