#!/usr/bin/env python3
"""Integrate foolproof into neuron.py"""
import sys
sys.path.insert(0, '/opt/yaxiio')

path = "/opt/yaxiio/.pi/skills/commander/neuron.py"
with open(path) as f:
    content = f.read()

changes = 0

# 1. Add foolproof import
old = "from trace_logger import TraceLogger"
new = (
    "from trace_logger import TraceLogger\n"
    "from modules.shared.foolproof import (\n"
    "    apply_quality_preset, validate_card, validate_in_range,\n"
    "    validate_not_empty, safe_default\n"
    ")"
)
if old in content and "foolproof" not in content:
    content = content.replace(old, new)
    changes += 1
    print("OK: import")

# 2. Enhanced card loading
old2_start = "    def _load_capability_card(self) -> dict:"
old2_end = "        return {}"

idx_s = content.find(old2_start)
idx_e = content.find(old2_end, idx_s)
if idx_s >= 0 and idx_e > idx_s:
    old2 = content[idx_s:idx_e + len(old2_end)]
else:
    print("FAIL: _load_capability_card not found")
    old2 = None

if old2:
    new2 = '''    def _load_capability_card(self) -> dict:
        """Load capability card + fool-proof validation + quality preset expansion"""
        card = {}
        
        # 1. Load from file
        config_path = os.environ.get("AGENT_CONFIG", "")
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path) as f:
                    card = json.load(f)
            except Exception as e:
                log(f"Card file load failed: {e}")
        
        # 2. Load from Redis (file takes priority)
        if not card and self.redis:
            try:
                raw = self.redis.get(f"agent:card:{self.name}")
                if raw:
                    card = json.loads(raw)
            except Exception:
                pass
        
        if not card:
            return {}
        
        # 3. Fool-proof: expand quality preset
        if "quality" in card:
            quality = card.pop("quality")
            try:
                preset = apply_quality_preset(quality)
                for k, v in preset.items():
                    if k not in card:
                        card[k] = v
                log("quality={} -> model={} thinking={}".format(
                    quality, card.get("model","?"), card.get("thinking","?")))
            except ValueError as e:
                log(str(e))
        
        # 4. Fool-proof: validate card
        issues = validate_card(card)
        for issue in issues:
            log("Card issue: {}".format(issue))
        
        # 5. Fool-proof: safe defaults
        lc = card.setdefault("lifecycle", {})
        lc.setdefault("task_timeout", safe_default("task_timeout"))
        lc.setdefault("max_retries", safe_default("max_retries"))
        
        return {}'''
    content = content.replace(old2, new2)
    changes += 1
    print("OK: card loading")

# 3. Enhanced init
old3_start = "        if self.card:"
old3_end = '            log("CARD: " + self.card.get("name","?") + " v" + self.card.get("version","?"))'

idx3 = content.find(old3_start)
idx3e = content.find(old3_end, idx3)
if idx3 >= 0 and idx3e > idx3:
    old3 = content[idx3:idx3e + len(old3_end)]
    new3 = '''        if self.card:
            self.task_timeout = validate_in_range(
                self.card.get("lifecycle", {}).get("task_timeout", 300),
                "task_timeout", 30, 3600
            )
            self.max_retries = validate_in_range(
                self.card.get("lifecycle", {}).get("max_retries", 3),
                "max_retries", 1, 10
            )
            card_name = validate_not_empty(self.card.get("name", ""), "Agent")
            card_ver = self.card.get("version", "?")
            log("CARD: {} v{} timeout={}s retries={}".format(
                card_name, card_ver, self.task_timeout, self.max_retries))'''
    content = content.replace(old3, new3)
    changes += 1
    print("OK: init")

with open(path, "w") as f:
    f.write(content)
print(f"{changes}/3 applied")
