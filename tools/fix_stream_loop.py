#!/usr/bin/env python3
"""Replace pubsub loop with Stream consumer loop in yaxiio.py"""
path = '/opt/yaxiio/.pi/skills/commander/yaxiio.py'
with open(path) as f: c = f.read()

# Find the pubsub loop
marker = '        cycle = 0\n        while self.running:\n            pubsub = None\n            try:'
idx_s = c.find(marker)
end_marker = '            except Exception as e:\n                print(f\"[雅溪] Cycle error: {e}\", flush=True)\n                time.sleep(3)\n            finally:\n                try: pubsub.close()\n                except: pass'
idx_e = c.find(end_marker, idx_s)
old = c[idx_s:idx_e + len(end_marker)]

new = '''        # Redis Stream consumer group (replaces Pub/Sub)
        STREAM_KEY = "yaxiio:task_stream"
        GROUP_NAME = "commander-workers"
        CONSUMER_NAME = f"commander-{os.getpid()}"
        
        try:
            self.redis.client.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        except Exception:
            pass
        
        cycle = 0
        while self.running:
            try:
                try:
                    self.redis.client.ping()
                except Exception:
                    self.redis = RedisClient()
                
                results = self.redis.client.xreadgroup(
                    GROUP_NAME, CONSUMER_NAME,
                    {STREAM_KEY: ">"}, count=10, block=5000
                )
                
                if results:
                    for stream_name, messages in results:
                        for msg_id, fields in messages:
                            try:
                                raw = fields.get(b"data", fields.get("data", "{}"))
                                if isinstance(raw, bytes):
                                    raw = raw.decode("utf-8")
                                data = json.loads(raw)
                                msg_type = data.get("type", "")
                                if msg_type == "task":
                                    self.handle_task(data)
                                elif msg_type == "response":
                                    tid = data.get("taskId", "")
                                    if self.redis.client.exists(f"yaxiio:pending:{tid}"):
                                        self._resume_pending_task(tid, data)
                            except json.JSONDecodeError:
                                pass
                            except Exception as e:
                                print(f"[雅溪] Task error: {e}", flush=True)
                            self.redis.client.xack(STREAM_KEY, GROUP_NAME, msg_id)
                
                cycle += 1
                if cycle % 10 == 0:
                    stats = self.pool.stats() if self.pool else {}
                    cstats = self.constitution.stats()
                    print(f"[雅溪] Cycle {cycle}, tasks: {self.task_count}, "
                          f"q={stats.get('queue_depth',0)}/{stats.get('max_queue',0)} "
                          f"={cstats['compliance_rate']:.0%} "
                          f"viol={cstats['violations']}", flush=True)
            except Exception as e:
                print(f"[雅溪] Stream error: {e}", flush=True)
                time.sleep(3)'''

c = c.replace(old, new)
with open(path, 'w') as f: f.write(c)
try:
    compile(c, 'yx', 'exec')
    print('OK')
except SyntaxError as e:
    print(f'Syntax error: {e}')
    # Show the problematic area
    lines = c.split('\n')
    for i in range(max(0, e.lineno-3), min(len(lines), e.lineno+2)):
        print(f'{i+1}: {lines[i]}')
