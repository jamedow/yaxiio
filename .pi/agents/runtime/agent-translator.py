#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "/app/.pi/agents/runtime")

AGENT_PROMPT = """
你是 LightingMetal 翻译官 Agent。
职责: 审计页面中文残留 → 从MongoDB提取源数据 → 翻译为俄语 → 写入MongoDB+Redis
通信: Redis Pub/Sub 频道 lightingmetal:agent:翻译官
安全: 不删数据, 不修改原始文档, 所有翻译先写临时字段
"""
os.environ['AGENT_NAME'] = '翻译官'
os.environ['AGENT_ROLE'] = 'translator'
os.environ['AGENT_PROMPT'] = AGENT_PROMPT
os.environ['REDIS_HOST'] = os.environ.get('REDIS_HOST', '127.0.0.1')
os.environ['REDIS_PASS'] = os.environ.get('REDIS_PASSWORD', '')

from agent_core import Agent
Agent().run()
