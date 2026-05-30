#!/usr/bin/env python3
"""Add regret mechanism to ExperienceFlywheel"""
path = "/opt/yaxiio/modules/layer5/experience_flywheel.py"
with open(path) as f:
    c = f.read()

old = '    def _promote_template(self, agent_name: str, task_id: str,'
idx = c.find(old)
end_idx = c.find('    def _create_ab_variant', idx)
old_method = c[idx:end_idx]

new_method = '''    def _promote_template(self, agent_name: str, task_id: str,
                          l5_signals: dict):
        """High-score template promotion + regret mechanism: old version kept 7 days"""
        card_key = f"agent:card:{agent_name}"
        try:
            card_raw = self.redis.get(card_key)
            if not card_raw:
                return
            card = json.loads(card_raw)
            version = card.get("_template_version", 1) + 1
            
            # Fool-proof: save old version to history (7-day TTL)
            backup_key = f"agent:card:{agent_name}:v{version-1}"
            self.redis.setex(backup_key, 86400 * 7, card_raw)
            
            card["_template_version"] = version
            card["_last_promoted_score"] = l5_signals.get("overall", 0)
            card["_last_promoted_task"] = task_id
            card["_promoted_at"] = time.time()
            self.redis.set(card_key, json.dumps(card, ensure_ascii=False))
            print("[Flywheel] {} template v{} (score={}) backup v{} kept 7d".format(
                agent_name, version, l5_signals.get("overall", 0), version-1), flush=True)
        except Exception as e:
            print("[Flywheel] template promote failed ({}): {}".format(agent_name, e), flush=True)

    def rollback_template(self, agent_name: str, target_version: int = None) -> bool:
        """Regret mechanism: rollback capability card to a previous version"""
        card_key = f"agent:card:{agent_name}"
        try:
            current_raw = self.redis.get(card_key)
            if not current_raw:
                return False
            current = json.loads(current_raw)
            current_ver = current.get("_template_version", 1)
            
            if target_version is None:
                target_version = current_ver - 1
            
            backup_key = f"agent:card:{agent_name}:v{target_version}"
            backup_raw = self.redis.get(backup_key)
            if not backup_raw:
                print("[Flywheel] rollback failed: v{} backup not found or expired".format(target_version), flush=True)
                return False
            
            self.redis.setex(f"agent:card:{agent_name}:v{current_ver}", 86400 * 7, current_raw)
            self.redis.set(card_key, backup_raw)
            print("[Flywheel] {} v{} -> v{} rolled back".format(agent_name, current_ver, target_version), flush=True)
            return True
        except Exception as e:
            print("[Flywheel] rollback failed: {}".format(e), flush=True)
            return False

'''

c = c.replace(old_method, new_method)
with open(path, "w") as f:
    f.write(c)
print("OK: regret mechanism + rollback_template() added")
