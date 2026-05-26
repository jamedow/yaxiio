"""Skill 加载和管理"""
import os, json
from modules.shared.config import SKILL_DIR
class SkillLoader:
    def __init__(self): self.skills = {}; self._load()
    def _load(self):
        if not os.path.exists(SKILL_DIR): return
        for name in os.listdir(SKILL_DIR):
            path = os.path.join(SKILL_DIR, name)
            if os.path.isdir(path):
                md = os.path.join(path, "SKILL.md")
                if os.path.exists(md):
                    with open(md) as f: self.skills[name] = {"name":name,"path":path,"doc":f.read()[:500]}
    def list_all(self): return list(self.skills.keys())
    def get(self, name: str): return self.skills.get(name)
