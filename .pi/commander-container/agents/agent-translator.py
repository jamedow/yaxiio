# Copyright 2026 LightingMetal
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
os.environ['REDIS_HOST'] = '127.0.0.1'
os.environ['REDIS_PASS'] = 'Lt@114514!'

from agent_core import Agent
Agent().run()
