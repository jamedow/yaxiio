#!/usr/bin/env python3
"""Extract _agent_skill_map, _do_L3_L4, _do_L2 from workflow_engine.py"""
path = '/opt/yaxiio/.pi/skills/commander/workflow_engine.py'
with open(path) as f: c = f.read()

# === _agent_skill_map ===
old1_s = '    def _agent_skill_map(self) -> dict:\n        """Dynamic agent'
idx1 = c.find(old1_s)
idx1e = c.find('\n    def process(self,', idx1)
old1 = c[idx1:idx1e]
new1 = '    def _agent_skill_map(self) -> dict:\n        from workflow_utils_extracted import agent_skill_map\n        return agent_skill_map(self)\n'
c = c.replace(old1, new1)
print(f"_agent_skill_map: {len(old1)} -> {len(new1)} chars")

# === _do_L3_L4 ===
idx3 = c.find('    def _do_L3_L4(self, task_id')
idx3e = c.find('    def _wait_for_neuron_response(self,', idx3)
old3 = c[idx3:idx3e]
new3 = '    def _do_L3_L4(self, task_id: str, payload: dict, plan: dict, state: dict) -> dict:\n        from workflow_utils_extracted import do_l3_l4\n        return do_l3_l4(self, task_id, payload, plan, state)\n'
c = c.replace(old3, new3)
print(f"_do_L3_L4: {len(old3)} -> {len(new3)} chars")

with open(path, 'w') as f: f.write(c)
print("OK")
