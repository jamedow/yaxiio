#!/usr/bin/env python3
"""Fix: XREADGROUP → simple XREAD"""
path = '/opt/yaxiio/.pi/skills/commander/yaxiio.py'
with open(path) as f:
    lines = f.readlines()

# Find the block
start = end = None
for i, l in enumerate(lines):
    if 'Redis Stream' in l and '替代 Pub/Sub' in l:
        start = i
    if start and 'block=5000' in l:
        end = i + 1
        break
    if start and 'block=2000' in l:
        end = i + 1
        break

print(f"Found: start={start}, end={end}")

if start and end:
    new_section = [
        '        # Redis Stream (replaces Pub/Sub)\n',
        '        STREAM_KEY = "yaxiio:task_stream"\n',
        '        last_id = "0"\n',
        '        \n',
        '        cycle = 0\n',
        '        while self.running:\n',
        '            try:\n',
        '                results = self.redis.client.xread(\n',
        '                    {STREAM_KEY: last_id}, count=10, block=2000\n',
        '                )\n',
    ]
    lines = lines[:start] + new_section + lines[end:]

# Fix: remove xack
for i, l in enumerate(lines):
    if 'xack(STREAM_KEY' in l:
        lines[i] = '                            last_id = msg_id\n'

# Fix: remove xgroup_create block
new_lines = []
skip_until = -1
for i, l in enumerate(lines):
    if i < skip_until:
        continue
    if 'xgroup_create' in l:
        # Skip the try/except block for xgroup_create
        skip_until = i + 4
        continue
    new_lines.append(l)

with open(path, 'w') as f:
    f.writelines(new_lines)

import py_compile
try:
    py_compile.compile(path, doraise=True)
    print(f'OK: XREADGROUP → XREAD. {len(new_lines)} lines')
except py_compile.PyCompileError as e:
    print(f'ERROR: {e}')
