#!/usr/bin/env python3
"""Replace pubsub loop with XREAD in clean yaxiio.py"""
path = '/opt/yaxiio/.pi/skills/commander/yaxiio.py'
with open(path) as f:
    content = f.read()

old = """        cycle = 0
        while self.running:
            pubsub = None
            try:
                try: self.redis.client.ping()
                except: self.redis = RedisClient()

                pubsub = self.redis.client.pubsub()
                pubsub.subscribe("yaxiio:agent:commander", "lightingmetal:agent:commander")
                deadline = time.time() + 55

                while time.time() < deadline:
                    msg = pubsub.get_message(timeout=2.0)
                    if msg and msg["type"] == "message":
                        try:
                            raw = msg["data"]
                            if isinstance(raw, bytes): raw = raw.decode("utf-8")
                            data = json.loads(raw)
                            msg_type = data.get("type", "")
                            if msg_type == "task":
                                self.handle_task(data)
                            elif msg_type == "response":
                                # Neuron 异步回调 → 继续流水线
                                tid = data.get("taskId", "")
                                if self.redis.client.exists(f"yaxiio:pending:{tid}"):
                                    self._resume_pending_task(tid, data)
                        except json.JSONDecodeError: pass
                        except Exception as e:
                            print(f"[雅溪] Task error: {e}", flush=True)

                cycle += 1
                if cycle % 10 == 0:
                    stats = self.pool.stats()
                    cstats = self.constitution.stats()
                    print(f"[雅溪] Cycle {cycle}, tasks: {self.task_count}, "
                          f"q={stats['queue_depth']}/{stats['max_queue']} "
                          f"cmp={cstats['compliance_rate']:.0%} "
                          f"viol={cstats['violations']}", flush=True)

            except Exception as e:
                print(f"[雅溪] Cycle error: {e}", flush=True)
                time.sleep(3)
            finally:
                try: pubsub.close()
                except: pass"""

new = """        # Redis Stream (replaces Pub/Sub — persistent, no message loss)
        STREAM_KEY = "yaxiio:task_stream"
        last_id = "0"
        cycle = 0
        while self.running:
            try:
                try: self.redis.client.ping()
                except: self.redis = RedisClient()
                
                results = self.redis.client.xread(
                    {STREAM_KEY: last_id}, count=10, block=2000
                )
                if results:
                    for _stream_name, messages in results:
                        for msg_id, fields in messages:
                            last_id = msg_id
                            try:
                                raw = fields.get(b"data", fields.get("data", "{}"))
                                if isinstance(raw, bytes):
                                    raw = raw.decode("utf-8")
                                data = json.loads(raw)
                                if data.get("type") == "task":
                                    self.handle_task(data)
                            except json.JSONDecodeError:
                                pass
                            except Exception as e:
                                print(f"[雅溪] Task error: {e}", flush=True)
                
                cycle += 1
                if cycle % 10 == 0:
                    print(f"[雅溪] Cycle {cycle}, tasks={self.task_count}", flush=True)
            except Exception as e:
                print(f"[雅溪] Stream error: {e}", flush=True)
                time.sleep(3)"""

if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f: f.write(content)
    compile(content, 'yx', 'exec')
    print(f'OK: pubsub → XREAD ({len(new)} chars)')
else:
    print('NOT FOUND')
