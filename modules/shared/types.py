"""共享类型定义"""
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

class AgentQuadrant(Enum):
    CORE = "core"
    STRATEGIC = "strategic"
    UTILITY = "utility"
    EPHEMERAL = "ephemeral"

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    task_id: str
    action: str
    payload: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = ""

@dataclass
class AgentMeta:
    agent_id: str
    role: str
    quadrant: AgentQuadrant = AgentQuadrant.EPHEMERAL
    model: str = "deepseek-v4-pro"
    status: str = "idle"
