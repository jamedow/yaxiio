"""四象限生命周期管理"""
from modules.shared.types import AgentQuadrant
class LifecycleManager:
    def __init__(self, agent_factory=None, redis_client=None):
        self.factory = agent_factory; self.redis = redis_client
    def get_quadrant(self, role: str) -> AgentQuadrant:
        core = ["翻译官","商务经理","售前经理"]; strategic = ["审计官","SEO分析师"]
        if role in core: return AgentQuadrant.CORE
        if role in strategic: return AgentQuadrant.STRATEGIC
        return AgentQuadrant.EPHEMERAL
