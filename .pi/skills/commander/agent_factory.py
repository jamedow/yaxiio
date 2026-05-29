# Yaxiio v1.1 - AGPLv3
"""AgentFactory — standardized Agent creation from capability cards"""
class AgentFactory:
    """Phase 4: Standardized Agent creation from capability cards"""
    def __init__(self, redis_client=None):
        self.redis = redis_client
    
    def create(self, name: str, task_id: str, overrides: dict = None) -> dict:
        """Create Agent instance from capability card"""
        card_raw = self.redis.get(f"agent:card:{name}") if self.redis else None
        card = json.loads(card_raw) if card_raw else {}
        
        config = dict(card)
        if overrides:
            config.update(overrides)
        config["task_id"] = task_id
        
        agent_id = f"{name}-{task_id}"
        config_path = f"/tmp/yaxiio-agent/{agent_id}/agent.json"
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(config, f, ensure_ascii=False)
        
        quadrant = card.get("quadrant", "ephemeral")
        print(f"[Factory] created {agent_id} ({quadrant})", flush=True)
        return {"agent_id": agent_id, "config_path": config_path, "quadrant": quadrant, "card": card}
    
    def destroy(self, agent_id: str):
        """Destroy Agent instance"""
        name, task_id = agent_id.split("-", 1) if "-" in agent_id else (agent_id, "")
        if self.redis:
            self.redis.delete(f"agent:{name}:{task_id}:memory")
            self.redis.delete(f"agent:{name}:{task_id}:state")
        shutil.rmtree(f"/tmp/yaxiio-agent/{agent_id}", ignore_errors=True)
        print(f"[Factory] destroyed {agent_id}", flush=True)
    
    def get_or_create(self, name: str, task_id: str, commander=None) -> str:
        """Get existing or create new Agent. Returns agent_id."""
        agent_id = f"{name}-{task_id}"
        # Check if already running
        try:
            result = subprocess.run(["pgrep", "-f", f"AGENT_NAME={name}.*TASK_ID={task_id}"],
                                   capture_output=True, text=True, timeout=3)
            if result.stdout.strip():
                return agent_id
        except:
            pass
        # Create new
        self.create(name, task_id)
        if commander:
            skill = commander._agent_skill_map().get(name, "") if hasattr(commander, "_agent_skill_map") else ""
            commander.spawn_neuron(name, skill, task_id=task_id)
        return agent_id
    
    def recommend_agents(self, task_desc: str, available: list) -> list:
        """Recommend agent types for a task based on capability cards"""
        recommendations = []
        for name in available:
            card_raw = self.redis.get(f"agent:card:{name}") if self.redis else None
            if not card_raw:
                continue
            card = json.loads(card_raw)
            skills = " ".join(card.get("skills", []))
            role = card.get("role", "")
            # Simple keyword matching
            score = 0
            for kw in task_desc.lower().split():
                if kw in skills.lower() or kw in role.lower():
                    score += 1
            if score > 0:
                recommendations.append({"name": name, "score": score, "quadrant": card.get("quadrant")})
        return sorted(recommendations, key=lambda x: -x["score"])


