
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Skill Auto-Generator v3.0 — 吸收自 Hermes
==========================================
Agent 完成任务后自动提炼经验为 Markdown Skill 文件。
自动注册到 Commander，下次任务可直接复用。

Skills 目录: /opt/commander/skills/
容量限制:
  - 每个 Skill 文件 ≤ 2200 字符 (吸收 Hermes)
  - 每个 Agent 上下文 ≤ 1375 字符 (吸收 Hermes)
  - 容量有限倒逼信息压缩，提高上下文效率

Skill 格式 (Markdown):
  ---
  name: skill-name
  agent: agent-id
  score: 8
  created: 2026-05-25
  ---
  # Skill Name
  ## Trigger
  ...
  ## Solution
  ...
  ## Example
  ...
"""

import json
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional

try:
    from .planner_coordinator import Planner, Coordinator, PLANNER_LLM_MODEL
except ImportError:
    from planner_coordinator import Planner, Coordinator, PLANNER_LLM_MODEL

SKILL_DIR = os.environ.get("SKILL_DIR", "/opt/commander/skills")
SKILL_MAX_CHARS = int(os.environ.get("SKILL_MAX_CHARS", "2200"))
CONTEXT_MAX_CHARS = int(os.environ.get("CONTEXT_MAX_CHARS", "1375"))


class SkillGenerator:
    """从任务执行结果自动生成可复用的 Skill 文件。"""

    def __init__(self, llm_client=None, skill_dir: str = None):
        self.llm = llm_client
        self.dir = skill_dir or SKILL_DIR
        os.makedirs(self.dir, exist_ok=True)

    def generate(self, agent_id: str, task: str, result: dict,
                  score: int = 0) -> Optional[str]:
        """从任务结果提炼 Skill。

        Args:
            agent_id: 执行任务的 Agent
            task: 任务描述
            result: 执行结果
            score: LLM 评分 (1-10)

        Returns:
            Skill 文件路径，或 None (不值得提炼)
        """
        # 评分太低不提炼
        if score < 5:
            return None

        keywords = self._extract_keywords(task)
        skill_name = f"{agent_id}-{keywords}-{uuid.uuid4().hex[:4]}"

        if self.llm:
            content = self._llm_generate(agent_id, task, result, skill_name)
        else:
            content = self._template_generate(agent_id, task, result, skill_name)

        # 容量限制压缩
        content = self._compress(content, SKILL_MAX_CHARS)

        # 写入文件
        filename = f"{skill_name}.md"
        filepath = os.path.join(self.dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # 生成上下文摘要（供 Agent 注入）
        context_summary = self._summarize(content, CONTEXT_MAX_CHARS)

        return filepath

    def _llm_generate(self, agent_id: str, task: str, result: dict,
                       name: str) -> str:
        prompt = f"""Create a reusable Skill markdown file from this task execution.

Agent: {agent_id}
Task: {task[:300]}
Result: {json.dumps(result, ensure_ascii=False)[:300]}
Score: {score}

Format:
---
name: {name}
agent: {agent_id}
score: {score}
created: {datetime.now().isoformat()}
---
# {name}
## Trigger
(When to use this skill)
## Solution  
(How to solve it, step by step)
## Example
(Concrete example)

Keep under {SKILL_MAX_CHARS} characters total."""
        try:
            return self.llm.chat(prompt, max_tokens=800)
        except Exception:
            return self._template_generate(agent_id, task, result, name)

    def _template_generate(self, agent_id: str, task: str, result: dict,
                            name: str) -> str:
        """模板生成（fallback）。"""
        status = "success" if not result.get("error") else "failed"
        output = str(result.get("stdout", result.get("result", "")))[:500]

        return f"""---
name: {name}
agent: {agent_id}
created: {datetime.now().isoformat()}
---
# {name}

## Trigger
{task[:200]}

## Solution
Execute with agent {agent_id}. Result status: {status}.

## Example
Task: {task[:150]}
Output: {output[:200]}
"""

    def _extract_keywords(self, task: str) -> str:
        """提取关键词作为 skill 名称。"""
        # 简单停用词过滤
        stopwords = {"the", "a", "an", "is", "to", "of", "in", "for", "and", "or",
                     "的", "了", "是", "在", "和", "与", "或"}
        words = re.findall(r'[a-zA-Z\u4e00-\u9fff]+', task.lower())
        keywords = [w for w in words if w not in stopwords][:3]
        return "-".join(keywords) if keywords else "general"

    def _compress(self, content: str, max_chars: int) -> str:
        """容量限制压缩。超出部分用省略号标记。"""
        if len(content) <= max_chars:
            return content
        # 保留头尾，中间省略
        head = content[:max_chars - 50]
        return head + "\n\n... (content truncated to fit skill size limit) ...\n"

    def _summarize(self, content: str, max_chars: int) -> str:
        """生成 Agent 上下文摘要。"""
        # 提取 ## Trigger 和 ## Solution 行
        lines = content.split("\n")
        trigger = ""
        solution = ""
        in_section = None
        for line in lines:
            if line.startswith("## Trigger"):
                in_section = "trigger"
            elif line.startswith("## Solution"):
                in_section = "solution"
            elif line.startswith("##"):
                in_section = None
            elif in_section == "trigger" and len(trigger) < max_chars // 2:
                trigger += line.strip() + " "
            elif in_section == "solution" and len(solution) < max_chars // 2:
                solution += line.strip() + " "

        summary = f"[{self._extract_keywords(trigger)}] Trigger: {trigger[:300]}. Solution: {solution[:300]}"
        return summary[:max_chars]

    def list_skills(self) -> list:
        """列出所有已生成的 Skill。"""
        skills = []
        if not os.path.isdir(self.dir):
            return skills
        for f in sorted(os.listdir(self.dir)):
            if f.endswith(".md"):
                path = os.path.join(self.dir, f)
                skills.append({
                    "name": f.replace(".md", ""),
                    "path": path,
                    "size": os.path.getsize(path),
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat(),
                })
        return skills


# ═══════════════════════════════════════════════════════════════
# 五层模块化架构 (吸收自 EvoAgentX)
# ═══════════════════════════════════════════════════════════════

class FiveLayerArchitecture:
    """Commander 五层模块化架构。

    Layer 1: Perception    — 感知层 (输入解析、意图识别)
    Layer 2: Planning      — 规划层 (任务拆解、DAG生成)
    Layer 3: Coordination  — 协调层 (Agent调度、负载均衡)
    Layer 4: Execution     — 执行层 (Agent执行、结果收集)
    Layer 5: Evolution     — 进化层 (Skill提炼、策略优化、A/B测试)

    层与层之间通过 Redis Pub/Sub 解耦，可独立扩展。
    """

    def __init__(self, session_manager=None, llm_client=None, redis_client=None):
        self.session = session_manager
        self.llm = llm_client
        self.redis = redis_client

        # 五层实例
        self.planner = Planner(llm_client, redis_client)
        self.coordinator = Coordinator(redis_client)
        self.skill_gen = SkillGenerator(llm_client)

    def process(self, task: str, context: dict = None) -> dict:
        """完整的五层处理流程。

        Perception → Planning → Coordination → Execution → Evolution
        """
        result = {"task": task, "layers": {}}

        # Layer 1: Perception — 意图识别 + 上下文注入
        perception = self._perceive(task, context)
        result["layers"]["perception"] = perception

        # Layer 2: Planning — 拆解任务
        plan = self.planner.plan(task, context)
        result["layers"]["planning"] = {"plan_id": plan["plan_id"],
                                          "subtasks": len(plan["subtasks"])}

        # Layer 3: Coordination — 分配Agent
        assignments = []
        for subtask in plan["subtasks"]:
            agent_types = [subtask.get("agent_type", "通用Agent")]
            if self.redis:
                active = self.redis.keys("agent:pool:*") or []
                agent_types.extend([a.decode() if isinstance(a, bytes) else a
                                    for a in active])
            assignment = self.coordinator.assign(subtask, agent_types)
            assignments.append(assignment)
        result["layers"]["coordination"] = {"assigned": len(assignments)}

        # Layer 4: Execution — (异步，此处返回状态)
        result["layers"]["execution"] = {
            "status": "dispatched",
            "tasks": len(assignments),
        }

        # Layer 5: Evolution — Skill提炼 (异步)
        result["layers"]["evolution"] = {
            "skill_gen": "pending",
            "ab_test": "active",
        }

        # 发布架构状态
        if self.redis:
            self.redis.setex("commander:architecture:state", 60,
                             json.dumps(result, ensure_ascii=False))

        return result

    def _perceive(self, task: str, context: dict) -> dict:
        """Layer 1: 感知 — 意图识别 + 关键词提取。"""
        intents = []
        if any(kw in task.lower() for kw in ["翻译", "translate"]):
            intents.append("translate")
        if any(kw in task.lower() for kw in ["报价", "quote", "询价"]):
            intents.append("quote")
        if any(kw in task.lower() for kw in ["部署", "deploy", "发布"]):
            intents.append("deploy")
        if any(kw in task.lower() for kw in ["审计", "audit", "检查"]):
            intents.append("audit")
        if not intents:
            intents.append("general")

        return {
            "intents": intents,
            "language": "zh" if any('\u4e00' <= c <= '\u9fff' for c in task) else "en",
            "context_size": len(json.dumps(context or {})) if context else 0,
        }

    def get_architecture_state(self) -> dict:
        """获取五层架构运行状态。"""
        return {
            "layer1_perception": {"status": "active"},
            "layer2_planning": {"model": PLANNER_LLM_MODEL},
            "layer3_coordination": self.coordinator.get_load(),
            "layer4_execution": {"workers": "via agent-core.py"},
            "layer5_evolution": {
                "skills_count": len(os.listdir(SKILL_DIR)) if os.path.isdir(SKILL_DIR) else 0,
                "skill_max_chars": SKILL_MAX_CHARS,
                "context_max_chars": CONTEXT_MAX_CHARS,
            },
        }
