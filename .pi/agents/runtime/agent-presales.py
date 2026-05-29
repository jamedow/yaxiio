#!/usr/bin/env python3
import sys, os
sys.path.insert(0, "/app/.pi/agents/runtime")

AGENT_PROMPT = """
你是 LightingMetal 售前经理 Agent。
职责: 查询MongoDB产品库 → 匹配客户需求 → 生成报价方案
通信: Redis Pub/Sub 频道 lightingmetal:agent:售前经理
"""
os.environ['AGENT_NAME'] = '售前经理'
os.environ['AGENT_ROLE'] = 'presales'
os.environ['AGENT_PROMPT'] = AGENT_PROMPT
os.environ['REDIS_HOST'] = os.environ.get('REDIS_HOST', '127.0.0.1')
os.environ['REDIS_PASS'] = os.environ.get('REDIS_PASSWORD', '')

from agent_core import Agent
Agent().run()
