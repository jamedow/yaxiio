#!/usr/bin/env python3
"""Simulate Commander's pubsub loop to find the bug"""
import redis, time, json, os, threading

r = redis.Redis(protocol=2, host='127.0.0.1', port=6379, 
    password='Yaxiio2026', decode_responses=True)

# Start listener in background
def listener():
    for cycle in range(3):
        pubsub = r.pubsub()
        pubsub.subscribe('yaxiio:agent:commander')
        deadline = time.time() + 15
        count = 0
        while time.time() < deadline:
            msg = pubsub.get_message(timeout=1.0)
            if msg and msg['type'] == 'message':
                count += 1
                try:
                    data = json.loads(msg['data'])
                    print(f"  [Cycle{cycle}] task={data.get('taskId','?')}")
                except: pass
        pubsub.close()
        print(f"[Cycle{cycle}] {count} messages received")
        time.sleep(1)

t = threading.Thread(target=listener, daemon=True)
t.start()
time.sleep(3)

# Send test messages
for i in range(5):
    r.publish('yaxiio:agent:commander', 
        json.dumps({"type":"task","taskId":f"test-{i}","payload":{"action":"status"}}))
    time.sleep(1)
    print(f"  Sent test-{i}")

time.sleep(20)
print("Done")
