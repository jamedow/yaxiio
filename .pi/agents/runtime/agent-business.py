#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "/app/.pi/agents/runtime")

AGENT_PROMPT = """
你是 LightingMetal 商务经理 Agent。
职责: 对接海外客户 → 挖掘需求 → 输出结构化需求清单 → 转给售前经理
通信: Redis Pub/Sub 频道 lightingmetal:agent:商务经理
"""
os.environ['AGENT_NAME'] = '商务经理'
os.environ['AGENT_ROLE'] = 'business'
os.environ['AGENT_PROMPT'] = AGENT_PROMPT
os.environ['REDIS_HOST'] = os.environ.get('REDIS_HOST', '127.0.0.1')
os.environ['REDIS_PASS'] = os.environ.get('REDIS_PASSWORD', '')

from agent_core import Agent
Agent().run()
