#!/usr/bin/env python3
"""Simple XREAD replacement for pubsub loop"""
path = '/opt/yaxiio/.pi/skills/commander/yaxiio.py'
with open(path) as f:
    lines = f.readlines()

# Find the pubsub loop
start = None; end = None
for i, line in enumerate(lines):
    if 'cycle = 0' in line and 'while self.running:' in lines[i+1] if i+1 < len(lines) else '':
        start = i
    if start and 'except Exception as e:' in line and 'Cycle error' in line:
        end = i + 5  # lines to skip
        break

if start and end:
    block = [
        '        # Redis Stream — simple XREAD\n',
        '        STREAM_KEY = "yaxiio:task_stream"\n',
        '        last_id = "0"\n',
        '        cycle = 0\n',
        '        while self.running:\n',
        '            try:\n',
        '                try: self.redis.client.ping()\n',
        '                except: self.redis = RedisClient()\n',
        '                \n',
        '                results = self.redis.client.xread(\n',
        '                    {STREAM_KEY: last_id}, count=10, block=2000\n',
        '                )\n',
        '                if results:\n',
        '                    for stream_name, messages in results:\n',
        '                        for msg_id, fields in messages:\n',
        '                            last_id = msg_id\n',
        '                            try:\n',
        '                                raw = fields.get(b"data", fields.get("data", "{}"))\n',
        '                                if isinstance(raw, bytes): raw = raw.decode("utf-8")\n',
        '                                data = json.loads(raw)\n',
        '                                if data.get("type") == "task":\n',
        '                                    self.handle_task(data)\n',
        '                            except Exception as e:\n',
        '                                print(f"[雅溪] Task error: {e}", flush=True)\n',
        '                \n',
        '                cycle += 1\n',
        '                if cycle % 10 == 0:\n',
        '                    print(f"[雅溪] Cycle {cycle}, tasks={self.task_count}", flush=True)\n',
        '            except Exception as e:\n',
        '                print(f"[雅溪] Stream error: {e}", flush=True)\n',
        '                time.sleep(3)\n',
    ]
    new_lines = lines[:start] + block + lines[end:]
    with open(path, 'w') as f: f.writelines(new_lines)
    print(f'Replaced {start+1}-{end}: {len(block)} lines inserted')
else:
    print(f'Not found: start={start}, end={end}')
