#!/usr/bin/env python3
"""Apply Stream loop to clean yaxiio.py"""
path = '/opt/yaxiio/.pi/skills/commander/yaxiio.py'
with open(path) as f:
    lines = f.readlines()

# Find the pubsub loop section (lines 833-870 in original)
start = None; end = None
for i, line in enumerate(lines):
    if 'cycle = 0' in line and 'while self.running:' in lines[i+1]:
        start = i
    if start and 'except Exception as e:' in line and 'Cycle error' in line:
        end = i + 4  # include the except + sleep + finally
        break

if start and end:
    stream_block = """        # ── Redis Stream 消费者组（替代 Pub/Sub）──
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
                          f"viol={cstats['violations']}", flush=True)
            except Exception as e:
                print(f"[雅溪] Stream error: {e}", flush=True)
                time.sleep(3)
"""
    new_lines = lines[:start] + [stream_block] + lines[end:]
    with open(path, 'w') as f: f.writelines(new_lines)
    print(f'Replaced lines {start+1}-{end+1} with Stream loop')
else:
    print(f'Markers not found: start={start}, end={end}')
    for i in range(830, min(880, len(lines))):
        print(f'{i+1}: {lines[i].rstrip()}')
