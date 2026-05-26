"""Agent 能力注册"""
class AgentRegistry:
    def __init__(self, redis_client=None): self.redis = redis_client; self.cards = {}
    def register(self, agent_id: str, capabilities: list):
        self.cards[agent_id] = capabilities
        if self.redis: self.redis.set(f"agent:caps:{agent_id}", str(capabilities))
    def find(self, capability: str) -> list:
        return [aid for aid, caps in self.cards.items() if capability in caps]
