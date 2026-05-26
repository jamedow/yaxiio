"""Agent 工厂"""
import uuid, json
from modules.shared.types import AgentMeta, AgentQuadrant
class AgentFactory:
    def __init__(self, redis_client=None, model_router=None):
        self.redis = redis_client; self.model_router = model_router
    def create(self, role: str, task: dict = None, quadrant: str = "ephemeral") -> str:
        agent_id = f"{role}-{uuid.uuid4().hex[:6]}"
        model = self.model_router.select_model(task) if self.model_router and task else {"model":"deepseek-v4-pro"}
        meta = AgentMeta(agent_id=agent_id, role=role, quadrant=AgentQuadrant(quadrant), model=model.get("model","deepseek-v4-pro"))
        if self.redis: self.redis.set(f"agent:meta:{agent_id}", str(meta.__dict__))
        return agent_id
    def destroy(self, agent_id: str):
        if self.redis: self.redis.client.delete(f"agent:meta:{agent_id}")
