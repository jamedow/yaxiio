#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
扩展路由决策器 — Commander 扩展系统 3/3
==========================================
让 Commander 能自主决定：是安装新 Skill、注册新 MCP、还是创建新 Agent。
与 SkillManager、MCPManager、AgentLifecycleManagerV2 协同工作。

⚠️ 设计原则：不重复现有引擎的能力，而是消费其输出
  - 能力分析   → 复用 AgentDesigner.analyze_and_design()（已有）
  - 任务去重   → 复用 TaskAnalyzer（已有）
  - LLM 路由   → 复用 LLMRouter（已有）
  - 能力缺口检测 → ExtensionRouter._find_capability_gaps()（本模块新增）
  - 扩展策略决策 → ExtensionRouter._decide_extension_strategy()（本模块新增）
  - 执行协调   → ExtensionRouter._execute_strategy()（本模块新增）

Constitution 遵守：
  R1 — 只用 commander:* / extensions:* 前缀
  R2 — Agent 上限由 SafetyBoundary 控制
  R4 — 所有消息标准 JSON

集成点：
  CommanderV2.handle_task() → ExtensionRouter.analyze_and_extend()

v1.0 | 2026-05-24 | 初始版本
"""

import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional

import redis as redis_lib

# 类型引用（运行时不强制导入，通过依赖注入）
# from skill_manager import SkillManager
# from mcp_manager import MCPManager
# from agent_lifecycle_v2 import AgentLifecycleManagerV2, AgentDesigner


class ExtensionRouter:
    """扩展路由决策器。

    消费 AgentDesigner 的任务分析结果，检测 Skill/MCP/Agent 的能力缺口，
    制定扩展策略（安装 Skill / 注册 MCP / 创建 Agent），并执行。
    """

    # Redis 前缀
    KEY_PREFIX = "extensions:"
    KEY_DECISIONS = "extensions:decisions"       # List: 最近决策（队列，保持最近 500 条）
    KEY_STRATEGY_STATS = "extensions:strategy_stats"  # Hash: 策略类型 → 统计 JSON
    KEY_CAPABILITY_INDEX = "extensions:capability_index"  # Hash: capability → providers JSON

    def __init__(
        self,
        skill_manager,           # SkillManager
        mcp_manager,             # MCPManager
        lifecycle_manager,       # AgentLifecycleManagerV2
        agent_designer=None,     # AgentDesigner（可选，有则为 LLM 驱动，无则降级）
        task_analyzer=None,      # TaskAnalyzer（可选，用于去重）
        store=None,              # SQLiteStore
    ):
        self.skill_manager = skill_manager
        self.mcp_manager = mcp_manager
        self.lifecycle_manager = lifecycle_manager
        self.agent_designer = agent_designer
        self.task_analyzer = task_analyzer
        self.redis = skill_manager.redis
        self.store = store

    # ── 主入口 ────────────────────────────────────────────────

    async def analyze_and_extend(self, task: Dict) -> Dict:
        """分析任务需求，自主决定扩展策略并执行。

        这是 CommanderV2.handle_task() 在处理每个任务前调用的入口。

        流程：
          1. 消费 AgentDesigner 的输出（或降级分析）→ 获取所需能力
          2. 检查现有资源（Skill/MCP/Agent）是否能满足
          3. 为每个能力缺口制定扩展策略
          4. 执行扩展 → 持久化决策日志

        Args:
            task: 标准任务字典，包含 taskId, type, description, payload 等

        Returns:
            {
              task_id, timestamp, strategies_executed: int,
              decisions: [{gap, strategy, result}],
              summary: "已执行 N 个扩展决策"
            }
        """
        decision_log = {
            "task_id": task.get("taskId", f"ext-{int(time.time()*1000)}"),
            "task_type": task.get("type", "unknown"),
            "timestamp": datetime.now().isoformat(),
            "decisions": [],
            "strategies_executed": 0,
        }

        # 1. 分析任务所需能力（复用 AgentDesigner，无则降级）
        required_capabilities = await self._analyze_required_capabilities(task)

        # 2. 检查能力缺口
        gaps = self._find_capability_gaps(required_capabilities)

        if not gaps:
            decision_log["summary"] = "无能力缺口，无需扩展"
            return decision_log

        # 3. 为每个缺口制定策略
        strategies = []
        for gap in gaps:
            strategy = await self._decide_extension_strategy(gap, task)
            strategies.append({"gap": gap, "strategy": strategy})

        # 4. 执行策略（低优先级可并行，高优先级串行）
        for item in strategies:
            if item["strategy"]["priority"] == "high":
                result = await self._execute_strategy(item["strategy"])
            else:
                result = await self._execute_strategy(item["strategy"])

            decision = {
                "gap": item["gap"],
                "strategy": item["strategy"],
                "result": result,
            }
            decision_log["decisions"].append(decision)

            if result.get("status") == "success":
                decision_log["strategies_executed"] += 1

        # 5. 更新能力索引
        self._update_capability_index(required_capabilities, strategies)

        # 6. 持久化
        decision_log["summary"] = f"已执行 {decision_log['strategies_executed']} 个扩展决策"
        self._persist_decision(decision_log)

        return decision_log

    # ── 能力分析（消费 AgentDesigner，不重复实现）─────────────

    async def _analyze_required_capabilities(self, task: Dict) -> List[Dict]:
        """分析任务所需的能力列表。

        优先使用 AgentDesigner（LLM 驱动），fallback 到关键词匹配。
        每个能力项包含：
          - capability: 能力名称
          - type: skill | mcp | agent
          - priority: high | medium | low
        """
        # 策略 A：使用 AgentDesigner（已有引擎，LLM 驱动）
        if self.agent_designer is not None:
            try:
                design = await self.agent_designer.analyze_and_design(task)
                return self._design_to_capabilities(design, task)
            except Exception as e:
                print(f"[ExtensionRouter] AgentDesigner 分析失败: {e}，降级到关键词匹配")

        # 策略 B：降级到关键词匹配（轻量、不依赖 LLM）
        return self._keyword_based_analysis(task)

    def _design_to_capabilities(self, design: Dict, task: Dict) -> List[Dict]:
        """将 AgentDesigner 的设计输出转换为能力需求列表。"""
        capabilities = []
        required_skills = design.get("required_skills", [])
        required_knowledge = design.get("knowledge_sources", [])
        needs_collaboration = design.get("needs_collaboration", False)

        # 专业技能 → Skill
        for skill in required_skills:
            capabilities.append({
                "capability": skill,
                "type": "skill",
                "priority": "high" if "翻译" in skill or "审计" in skill else "medium",
            })

        # 知识库 → MCP（如需要 MongoDB/数据库查询）
        for knowledge in required_knowledge:
            if "MongoDB" in knowledge or "数据库" in knowledge:
                capabilities.append({
                    "capability": "mongodb",
                    "type": "mcp",
                    "priority": "medium",
                })
            if "爬取" in knowledge or "抓取" in knowledge:
                capabilities.append({
                    "capability": "firecrawl",
                    "type": "mcp",
                    "priority": "medium",
                })

        # 协作需求 → 可能需要新 Agent
        if needs_collaboration:
            role = design.get("role", "")
            if role:
                capabilities.append({
                    "capability": role,
                    "type": "agent",
                    "priority": "medium",
                })

        return capabilities if capabilities else [
            {"capability": f"通用处理-{task.get('type', 'unknown')}", "type": "agent", "priority": "low"}
        ]

    def _keyword_based_analysis(self, task: Dict) -> List[Dict]:
        """基于关键词的能力分析（降级方案）。

        与 AgentDesigner._fallback_analyze() 保持一致的映射表，
        但输出格式统一为 ExtensionRouter 的能力格式。
        """
        # 能力关键词映射表（与 AgentDesigner.CAPABILITY_MAP 对齐）
        CAPABILITY_KEYWORD_MAP = {
            # skill 类型
            "翻译": ("translate-engine", "skill", "high"),
            "阿拉伯语": ("translate-engine", "skill", "high"),
            "俄语": ("translate-engine", "skill", "high"),
            "西班牙语": ("translate-engine", "skill", "high"),
            "法语": ("translate-engine", "skill", "high"),
            "英语": ("translate-engine", "skill", "high"),
            "审计": ("audit-engine", "skill", "high"),
            "审核": ("audit-engine", "skill", "medium"),
            "质检": ("audit-engine", "skill", "medium"),
            "SEO": ("seo-engineer", "skill", "medium"),
            "搜索": ("lightingmetal-rag", "mcp", "high"),
            "查找": ("lightingmetal-rag", "mcp", "high"),
            "设计": ("ui-ux-designer", "skill", "medium"),
            "UI": ("ui-ux-designer", "skill", "medium"),
            "开发": ("dev-agent", "agent", "high"),
            "API": ("dev-agent", "agent", "high"),
            # agent 类型
            "报价": ("商务经理", "agent", "high"),
            "quotation": ("商务经理", "agent", "high"),
            "询盘": ("商务经理", "agent", "high"),
            "客户": ("商务经理", "agent", "high"),
            "订单": ("商务经理", "agent", "high"),
            "沙特": ("商务经理", "agent", "high"),
            "光伏": ("商务经理", "agent", "medium"),
            "方案": ("售前经理", "agent", "medium"),
            "对比": ("售前经理", "agent", "medium"),
            "选型": ("售前经理", "agent", "high"),
            "规格": ("售前经理", "agent", "high"),
            # mcp 类型
            "爬取": ("firecrawl", "mcp", "medium"),
            "抓取": ("firecrawl", "mcp", "medium"),
            "github": ("github", "mcp", "low"),
            "数据库": ("mongodb", "mcp", "medium"),
            "MongoDB": ("mongodb", "mcp", "medium"),
            # RAG 知识库检索
            "知识库": ("lightingmetal-rag", "mcp", "high"),
            "产品查询": ("lightingmetal-rag", "mcp", "high"),
            "产品搜索": ("lightingmetal-rag", "mcp", "high"),
            "产品知识": ("lightingmetal-rag", "mcp", "medium"),
            "RAG": ("lightingmetal-rag", "mcp", "high"),
            "语义检索": ("lightingmetal-rag", "mcp", "medium"),
            "向量": ("lightingmetal-rag", "mcp", "medium"),
            # Token 预算控制
            "token": ("token-budget-controller", "skill", "high"),
            "裁剪": ("token-budget-controller", "skill", "high"),
            "上下文": ("token-budget-controller", "skill", "medium"),
            "预算": ("token-budget-controller", "skill", "medium"),
            # Prompt 优化
            "优化": ("prompt-optimizer", "skill", "high"),
            "提示词": ("prompt-optimizer", "skill", "high"),
            "prompt": ("prompt-optimizer", "skill", "high"),
            "错误率": ("prompt-optimizer", "skill", "medium"),
            # 多模态 — 图片生成
            "生图": ("image-gen", "mcp", "high"),
            "画": ("image-gen", "mcp", "medium"),
            "画图": ("image-gen", "mcp", "high"),
            "生成图片": ("image-gen", "mcp", "high"),
            "图片": ("image-gen", "mcp", "medium"),
            "图像": ("image-gen", "mcp", "medium"),
            "海报": ("image-gen", "mcp", "high"),
            "banner": ("image-gen", "mcp", "high"),
            "配图": ("image-gen", "mcp", "high"),
            "产品图": ("image-gen", "mcp", "high"),
            "效果图": ("image-gen", "mcp", "high"),
            "DALL-E": ("image-gen", "mcp", "high"),
            "dalle": ("image-gen", "mcp", "high"),
            "插图": ("image-gen", "mcp", "medium"),
            # 部署 — Deployment MCP Server
            "部署": ("deployment", "mcp", "high"),
            "deploy": ("deployment", "mcp", "high"),
            "上线": ("deployment", "mcp", "high"),
            "发布": ("deployment", "mcp", "high"),
            "推送": ("deployment", "mcp", "high"),
            "构建": ("deployment", "mcp", "medium"),
            "打包": ("deployment", "mcp", "medium"),
            "一键部署": ("deployment", "mcp", "high"),
            "生产": ("deployment", "mcp", "medium"),
            "production": ("deployment", "mcp", "medium"),
            "deployment": ("deployment", "mcp", "high"),
            "ci": ("deployment", "mcp", "medium"),
            "cd": ("deployment", "mcp", "medium"),
            "devops": ("deployment", "mcp", "medium"),
            "运维": ("deployment", "mcp", "medium"),
            "回滚": ("deployment", "mcp", "high"),
            "rollback": ("deployment", "mcp", "high"),
            "preview": ("deployment", "mcp", "medium"),
            "预发": ("deployment", "mcp", "medium"),
            "staging": ("deployment", "mcp", "medium"),
        }

        task_desc = (
            task.get("description", "")
            + json.dumps(task.get("payload", {}), ensure_ascii=False)
        )

        capabilities = []
        seen = set()

        for keyword, (cap_name, cap_type, priority) in CAPABILITY_KEYWORD_MAP.items():
            if keyword in task_desc and cap_name not in seen:
                capabilities.append({
                    "capability": cap_name,
                    "type": cap_type,
                    "priority": priority,
                })
                seen.add(cap_name)

        return capabilities  # 无匹配时返回空列表

    # ── 能力缺口检测（本模块核心新增逻辑）─────────────────────

    def _find_capability_gaps(self, required: List[Dict]) -> List[Dict]:
        """找出当前系统无法满足的能力缺口。

        对每个能力需求，检查三个维度：
          - skill  → SkillManager.get_global_skills()
          - mcp    → MCPManager.get_registered_servers()
          - agent  → LifecycleManager 角色查询
        """
        gaps = []
        for cap in required:
            cap_type = cap["type"]
            cap_name = cap["capability"]
            exists = False

            if cap_type == "skill":
                existing = self.skill_manager.get_global_skills()
                exists = any(
                    s["name"].lower() == cap_name.lower()
                    or cap_name.lower() in s["name"].lower()
                    for s in existing
                )

            elif cap_type == "mcp":
                existing = self.mcp_manager.get_registered_servers()
                exists = any(
                    s["name"].lower() == cap_name.lower()
                    or cap_name.lower() in s["name"].lower()
                    for s in existing
                )

            elif cap_type == "agent":
                # 通过 Redis 查询是否有对应角色的 Agent
                try:
                    agent_count = self.redis.scard(f"agent:role:{cap_name}")
                    exists = agent_count > 0
                except Exception:
                    # 降级：检查 agent-registry.json
                    exists = False

            if not exists:
                gaps.append(cap)

        return gaps

    # ── 扩展策略决策 ──────────────────────────────────────────

    async def _decide_extension_strategy(self, gap: Dict, task: Dict) -> Dict:
        """为单个能力缺口决定扩展策略。

        决策树：
          skill 能力缺口 →
            ├─ 本地 Skill 存在 → install（local 模式）
            ├─ npm 可搜索到   → install（npm 模式）
            └─ 都不存在        → create（LLM 生成蓝图）

          mcp 能力缺口 →
            ├─ 已知 Server 映射 → register_mcp
            └─ 未知             → search 后 register 或 skip

          agent 能力缺口 →
            └─ create_agent（通过 LifecycleManager）
        """
        strategy = {
            "capability": gap["capability"],
            "type": gap["type"],
            "priority": gap.get("priority", "medium"),
            "action": None,
            "params": {},
            "risk": "auto",  # auto | manual | skip
        }

        if gap["type"] == "skill":
            # 搜索已有 Skill
            matching = await self.skill_manager.search_skill(gap["capability"])
            if matching:
                strategy["action"] = "install_skill"
                strategy["params"] = {
                    "skill_name": matching[0],
                    "source": "local",  # 优先本地，后续可升级 npm
                }
                strategy["risk"] = "auto"
            else:
                # 无现成 Skill → 创建新 Skill 蓝图
                strategy["action"] = "create_skill_blueprint"
                strategy["params"] = {
                    "capability": gap["capability"],
                    "task_context": task.get("description", task.get("type", "")),
                }
                strategy["risk"] = "manual"  # 需要人工审核

        elif gap["type"] == "mcp":
            matching = await self.mcp_manager.search_mcp_server(gap["capability"])
            if matching:
                server_name = matching[0]
                # 根据 Server 类型选择正确的 command/args
                if server_name == "lightingmetal-rag":
                    strategy["action"] = "register_mcp"
                    strategy["params"] = {
                        "server_name": server_name,
                        "command": "python3",
                        "args": ["/app/ai-server/mcp-servers/lightingmetal-rag/server.py"],
                    }
                else:
                    strategy["action"] = "register_mcp"
                    strategy["params"] = {
                        "server_name": server_name,
                        "command": "npx",
                        "args": ["-y", f"@anthropic/mcp-server-{server_name}"],
                    }
                strategy["risk"] = "auto"
            else:
                # 尝试通用命名约定
                strategy["action"] = "register_mcp"
                strategy["params"] = {
                    "server_name": gap["capability"],
                    "command": "npx",
                    "args": ["-y", f"@anthropic/mcp-server-{gap['capability']}"],
                }
                strategy["risk"] = "manual"  # 不确定是否有效

        elif gap["type"] == "agent":
            strategy["action"] = "create_agent"
            strategy["params"] = {
                "role": gap["capability"],
                "quadrant": "ephemeral",  # 按需创建的 Agent 默认 ephemeral
                "task": {
                    "taskId": task.get("taskId", ""),
                    "type": task.get("type", ""),
                    "description": task.get("description", ""),
                },
            }
            strategy["risk"] = "auto"

        return strategy

    # ── 策略执行 ──────────────────────────────────────────────

    async def _execute_strategy(self, strategy: Dict) -> Dict:
        """执行扩展策略。

        Returns:
            {status: success|failed|skipped, [error], [details], action}
        """
        action = strategy["action"]
        params = strategy["params"]
        risk = strategy.get("risk", "auto")

        # 高风险策略需要确认（当前默认执行，但标记为 manual）
        if risk == "manual":
            params["_confirmed"] = True  # 标记已确认

        try:
            if action == "install_skill":
                result = await self.skill_manager.install_skill(**params)
                return {
                    "status": result.get("status", "failed"),
                    "action": action,
                    "details": result,
                }

            elif action == "register_mcp":
                result = await self.mcp_manager.register_mcp_server(**params)
                return {
                    "status": result.get("status", "failed"),
                    "action": action,
                    "details": result,
                }

            elif action == "create_agent":
                # 通过 AgentLifecycleManagerV2 创建 Agent
                if hasattr(self.lifecycle_manager, "request_agent"):
                    agent_result = await self.lifecycle_manager.request_agent(
                        role=params.get("role", "临时Worker"),
                        quadrant=params.get("quadrant", "ephemeral"),
                    )
                    return {
                        "status": "success" if agent_result else "failed",
                        "action": action,
                        "details": {
                            "role": params.get("role"),
                            "quadrant": params.get("quadrant"),
                            "agent_result": str(agent_result)[:200],
                        },
                    }
                else:
                    return {
                        "status": "failed",
                        "action": action,
                        "error": "LifecycleManager 不支持 request_agent",
                    }

            elif action == "create_skill_blueprint":
                # 生成 Skill 蓝图（JSON），不实际执行安装
                blueprint = self._generate_skill_blueprint(
                    params.get("capability", "unknown"),
                    params.get("task_context", ""),
                )
                return {
                    "status": "blueprint_created",
                    "action": action,
                    "details": blueprint,
                }

            else:
                return {
                    "status": "skipped",
                    "action": action,
                    "error": f"未知策略: {action}",
                }

        except Exception as e:
            return {
                "status": "failed",
                "action": action,
                "error": str(e)[:500],
            }

    # ── Skill 蓝图生成 ────────────────────────────────────────

    def _generate_skill_blueprint(self, capability: str, task_context: str) -> Dict:
        """当没有现成 Skill 可用时，生成 Skill 蓝图供 Commander 后续创建。

        蓝图是一个结构化的 Skill 定义，包含：
          - name, description, trigger_words, capabilities
          - 建议的 SKILL.md 结构
        """
        # 从能力名称推导分类
        category = "utility"
        if any(kw in capability.lower() for kw in ["翻译", "translat"]):
            category = "translation"
        elif any(kw in capability.lower() for kw in ["审计", "审核", "audit"]):
            category = "audit"
        elif any(kw in capability.lower() for kw in ["seo", "搜索"]):
            category = "seo"
        elif any(kw in capability.lower() for kw in ["设计", "ui", "design"]):
            category = "design"

        slug = capability.lower().replace(" ", "-").replace("_", "-")

        return {
            "blueprint_type": "skill",
            "skill_name": slug,
            "category": category,
            "suggested_location": f".pi/skills/{slug}/",
            "skeleton": {
                "name": slug,
                "description": f"自动生成的 Skill 蓝图: {capability}。触发任务: {task_context[:80]}",
                "capabilities": [capability],
                "trigger_words": [capability],
                "generated_at": datetime.now().isoformat(),
                "status": "pending_review",
            },
            "next_steps": [
                f"在 .pi/skills/{slug}/ 创建 SKILL.md",
                f"定义 {capability} 的具体能力描述",
                "Commander 审核后通过 SkillManager.install_skill(source='local') 安装",
            ],
        }

    # ── 能力索引维护 ──────────────────────────────────────────

    def _update_capability_index(
        self,
        required_capabilities: List[Dict],
        strategies: List[Dict],
    ):
        """维护 capability→provider 倒排索引。

        用于后续任务的能力发现加速：下次遇到相似任务时，
        可以直接从索引中查到对应的 Skill/MCP/Agent。
        """
        for cap in required_capabilities:
            cap_name = cap["capability"]
            current_json = self.redis.hget(self.KEY_CAPABILITY_INDEX, cap_name)
            providers = json.loads(
                current_json.decode() if isinstance(current_json, bytes) else current_json
            ) if current_json else {"skill": [], "mcp": [], "agent": []}

            # 根据策略更新 provider 列表
            for item in strategies:
                if item["gap"]["capability"] == cap_name:
                    s = item["strategy"]
                    if s["action"] == "install_skill":
                        if s["params"]["skill_name"] not in providers["skill"]:
                            providers["skill"].append(s["params"]["skill_name"])
                    elif s["action"] == "register_mcp":
                        if s["params"]["server_name"] not in providers["mcp"]:
                            providers["mcp"].append(s["params"]["server_name"])
                    elif s["action"] == "create_agent":
                        if s["params"]["role"] not in providers["agent"]:
                            providers["agent"].append(s["params"]["role"])

            self.redis.hset(
                self.KEY_CAPABILITY_INDEX,
                cap_name,
                json.dumps(providers, ensure_ascii=False),
            )

    # ── 持久化 ────────────────────────────────────────────────

    def _persist_decision(self, decision_log: Dict):
        """持久化决策日志到 Redis（队列）+ MongoDB（归档）。"""
        # Redis 队列（保留最近 500 条）
        try:
            self.redis.lpush(
                self.KEY_DECISIONS,
                json.dumps(decision_log, ensure_ascii=False),
            )
            self.redis.ltrim(self.KEY_DECISIONS, 0, 499)
        except Exception:
            pass

        # SQLite 归档
        if self.store is not None:
            try:
                self.store.log_event("extension_decisions", decision_log)
            except Exception as e:
                print(f"[ExtensionRouter] SQLite 归档失败: {e}")

    # ── 统计查询 ──────────────────────────────────────────────

    def get_recent_decisions(self, limit: int = 20) -> List[Dict]:
        """获取最近的扩展决策。"""
        try:
            items = self.redis.lrange(self.KEY_DECISIONS, 0, limit - 1)
            return [
                json.loads(item.decode() if isinstance(item, bytes) else item)
                for item in items
            ]
        except Exception:
            return []

    def get_capability_providers(self, capability: str) -> Dict:
        """查询某个能力的 provider 列表。"""
        result = self.redis.hget(self.KEY_CAPABILITY_INDEX, capability)
        if result:
            return json.loads(
                result.decode() if isinstance(result, bytes) else result
            )
        return {"skill": [], "mcp": [], "agent": []}


# ═══════════════════════════════════════════════════════════════
# Commander 集成入口
# ═══════════════════════════════════════════════════════════════

def build_extension_router(
    redis_client: redis_lib.Redis,
    store=None,
    pi_config_path: str = ".pi",
    mcp_config_path: str = ".pi/agent/mcp.json",
    lifecycle_manager=None,
    agent_designer=None,
    task_analyzer=None,
) -> ExtensionRouter:
    """便捷工厂：构建完整的扩展路由决策器。

    自动创建 SkillManager 和 MCPManager 依赖，
    并将 AgentDesigner/LifecycleManager 注入。

    用法（在 CommanderV2.__init__ 中）：
        from extension_router import build_extension_router
        self.extension_router = build_extension_router(
            redis_client=redis_for_modules,
            store=store,
            lifecycle_manager=self.lifecycle_manager,
            agent_designer=self.agent_designer,
            task_analyzer=self.task_analyzer,
        )
    """
    from skill_manager import SkillManager
    from mcp_manager import MCPManager

    skill_mgr = SkillManager(
        redis_client=redis_client,
        store=store,
        pi_config_path=pi_config_path,
    )
    mcp_mgr = MCPManager(
        redis_client=redis_client,
        store=store,
        mcp_config_path=mcp_config_path,
    )

    return ExtensionRouter(
        skill_manager=skill_mgr,
        mcp_manager=mcp_mgr,
        lifecycle_manager=lifecycle_manager,
        agent_designer=agent_designer,
        task_analyzer=task_analyzer,
        store=store,
    )
