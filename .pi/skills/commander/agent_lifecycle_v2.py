#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Agent 生命周期管理器 V2 — 完整集成版
=======================================
包含五个模块：
  1. SafetyBoundary           — 安全边界：约束所有 Agent 操作
  2. AsyncAgentFactory        — 异步工厂：适配 PM2 + Redis 架构
  3. AgentDesigner            — 自主设计：LLM 驱动的能力规格生成
  4. AutonomousTaskDecomposer — 任务拆解：模糊意图→原子任务序列
  5. AgentLifecycleManagerV2  — 生命周期：四象限分级 + 状态机 + 评估循环
  6. SelfEvolvingCommander    — 自我进化：历史模式分析 + A/B 验证

Constitution 合规：
  R1 — 只用 commander:* / lifecycle:* / agent:metadata:* 前缀，不删 page:*/lightingmetal:*
  R2 — Agent 上限通过 SafetyBoundary 控制
  R3 — 报价草稿由 CommanderV2 层处理
"""

import asyncio
import collections.abc
import hashlib
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

# pypinyin 可选依赖
HAS_PYPINYIN = False
try:
    from pypinyin import lazy_pinyin
    HAS_PYPINYIN = True
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════════

class AgentQuadrant(Enum):
    """Agent 四象限分类"""
    CORE = "core"
    STRATEGIC = "strategic"
    UTILITY = "utility"
    EPHEMERAL = "ephemeral"


class AgentStatus(Enum):
    """Agent 运行状态"""
    RUNNING = "running"
    IDLE = "idle"
    PAUSED = "paused"
    ARCHIVED = "archived"
    ERROR = "error"


# ═══════════════════════════════════════════════════════════════
# 0. 基础组件：LLM 适配器 + 同步→异步桥接
# ═══════════════════════════════════════════════════════════════

class LLMAdapter:
    """
    统一的 LLM 客户端适配器，抹平不同 Provider 的接口差异。

    支持的 Provider：
      - OpenAI 兼容接口（DeepSeek / 通义千问 / Moonshot 等）
      - 自定义 chat() 方法

    用法：
        llm = LLMAdapter(api_key="sk-xxx", base_url="https://api.deepseek.com/v1", model="deepseek-v4-pro")
        if llm.available:
            result = await llm.chat("你好")
    """

    def __init__(self, api_key: str = None, base_url: str = None, model: str = "deepseek-chat", thinking: str = "medium"):
        self.model = model
        self.thinking = thinking  # off/low/medium/high/max
        self._client = None
        self._raw_client = None

        if api_key:
            try:
                from openai import OpenAI as _OpenAI
                self._client = _OpenAI(
                    api_key=api_key,
                    base_url=base_url or "https://api.deepseek.com/v1",
                )
                self._raw_client = self._client
            except ImportError:
                print("[LLMAdapter] openai 库未安装，LLM 功能不可用")

    @property
    def available(self) -> bool:
        """LLM 是否可用。"""
        return self._client is not None

    async def chat(self, prompt: str, temperature: float = 0.3) -> str:
        """
        统一异步 chat 接口。
        内部通过线程池执行同步 OpenAI 调用，避免阻塞事件循环。
        """
        if not self._client:
            raise RuntimeError("LLM 客户端不可用")

        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                extra_body={"reasoning_effort": self.thinking} if self.thinking and self.thinking != "off" else None,
            ),
        )
        return resp.choices[0].message.content

    # 兼容旧代码：允许用 self.llm.chat.completions.create() 直接调用
    @property
    def chat_completions(self):
        return self._client.chat.completions if self._client else None


class AsyncEventLoop:
    """
    同步→异步桥接器：在后台线程运行 asyncio 事件循环，
    让同步 Commander 主循环可以调用异步生命周期方法。

    用法：
        bridge = AsyncEventLoop()
        bridge.start()
        future = bridge.submit(some_async_function())
        result = bridge.wait(future, timeout=30)
        bridge.shutdown()
    """

    def __init__(self):
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self):
        """启动后台事件循环线程。"""
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="async-bridge")
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self):
        """后台线程：运行事件循环。"""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro) -> asyncio.Future:
        """
        从同步线程提交协程，立即返回 Future。
        不会阻塞调用线程。
        """
        if not self.loop or not self.loop.is_running():
            raise RuntimeError("AsyncEventLoop 未启动")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def wait(self, future: asyncio.Future, timeout: float = 30) -> Any:
        """
        等待 Future 完成并返回结果。
        阻塞调用线程直到超时或完成。
        """
        try:
            return future.result(timeout=timeout)
        except Exception as e:
            raise

    def run(self, coro, timeout: float = 30) -> Any:
        """
        快捷方法：提交并等待结果。
        等同于 submit(coro) + wait(future, timeout)。
        """
        future = self.submit(coro)
        return self.wait(future, timeout=timeout)

    def shutdown(self):
        """关闭事件循环。"""
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)


# ═══════════════════════════════════════════════════════════════
# 1. SafetyBoundary — 安全边界
# ═══════════════════════════════════════════════════════════════

class SafetyBoundary:
    """
    安全边界：约束 Commander 及所有子 Agent 的行为。

    分离了两个层级的约束：
      - commander_rules:  约束 Commander 自身（创建/销毁/修改配置）
      - agent_rules:      约束子 Agent（Shell 命令/外部 API/MongoDB 写入）
    """

    # ── Commander 级规则 ──
    COMMANDER_RULES = {
        "agent_count": {
            "max": 50,
            "max_per_role": 10,
        },
        "resource_limits": {
            "max_memory_per_agent_mb": 512,
            "max_tokens_per_minute": 100000,
        },
        "forbidden_actions": [
            "modify_system_config",
            "delete_mongodb_data",
        ],
        "approval_required": [
            "create_more_than_10_agents",
            "destroy_core_agent",
            "modify_own_prompt",
            "access_external_api",
        ],
    }

    # ── Agent 级规则（子 Agent 受此约束）──
    AGENT_RULES = {
        "forbidden_actions": [
            "execute_shell_command",   # 子 Agent 默认禁 Shell
            "delete_mongodb_data",
        ],
        "approval_required": [],
    }

    def __init__(self, redis_client=None):
        self.redis = redis_client

    # ── 实时计数查询 ──

    def _get_total_agent_count(self) -> int:
        """查询当前运行中 Agent 总数"""
        if not self.redis:
            return 0
        try:
            # 汇总四个象限的 agent 数
            return sum(
                self.redis.scard(f"agent:quadrant:{q.value}") or 0
                for q in AgentQuadrant
            )
        except Exception:
            return 0

    def _get_role_count(self, role: str) -> int:
        """查询某角色的实例数"""
        if not self.redis:
            return 0
        try:
            count = 0
            for q in AgentQuadrant:
                agents = self.redis.smembers(f"agent:quadrant:{q.value}")
                for aid in agents:
                    meta = self.redis.hget(f"agent:metadata:{aid.decode()}", "role")
                    if meta and meta.decode() == role:
                        count += 1
            return count
        except Exception:
            return 0

    # ── 核心检查 ──

    def check_action(self, action: str, context: dict = None) -> dict:
        """
        检查操作是否在安全边界内。
        返回: {"allowed": True} | {"allowed": False, "reason": "..."}
              | {"allowed": "approval_required", "reason": "..."}
        """
        context = context or {}
        rules = self._select_rules(context.get("scope", "commander"))

        # 1. 黑名单检查
        if action in rules.get("forbidden_actions", []):
            return {"allowed": False, "reason": f"禁止操作: {action}"}

        # 2. 审批名单检查
        if action in rules.get("approval_required", []):
            return {"allowed": "approval_required", "reason": f"需要审批: {action}"}

        # 3. 资源限制检查
        resource_check = self.check_resource(action, context)
        if not resource_check.get("allowed", True):
            return resource_check

        return {"allowed": True}

    def check_resource(self, action: str, context: dict = None) -> dict:
        """检查资源限制（Agent 数量/内存/Token）。"""
        context = context or {}
        rules = self._select_rules(context.get("scope", "commander"))
        agent_limit = rules.get("agent_count", {})

        if action in ("create_agent", "spawn_agent", "request_agent"):
            total = self._get_total_agent_count()
            max_total = agent_limit.get("max", 50)
            if total >= max_total:
                return {"allowed": False,
                        "reason": f"Agent 总数已达上限 {max_total} (当前 {total})"}

            # 单角色上限
            role = context.get("role", "")
            if role:
                per_role = agent_limit.get("max_per_role", 10)
                role_count = self._get_role_count(role)
                if role_count >= per_role:
                    return {"allowed": False,
                            "reason": f"角色 {role} 已达上限 {per_role} (当前 {role_count})"}

        return {"allowed": True}

    def _select_rules(self, scope: str) -> dict:
        """选择对应层级的规则。"""
        return self.AGENT_RULES if scope == "agent" else self.COMMANDER_RULES

    def get_effective_rules(self, scope: str = "commander") -> dict:
        """获取当前生效的全部规则（供 Dashboard 展示）。"""
        return self._select_rules(scope)


# ═══════════════════════════════════════════════════════════════
# 2. AsyncAgentFactory — 适配 PM2 + Redis 的异步工厂
# ═══════════════════════════════════════════════════════════════

class AsyncAgentFactory:
    """
    异步 Agent 工厂，将 shell 版 agent-factory.sh 封装为 Python 异步接口。

    与 Shell 工厂的对应关系：
      create  → agent-factory.sh create + spawn
      destroy → agent-factory.sh destroy
    """

    FACTORY_SCRIPT = "/app/.pi/agents/runtime/agent-factory.sh"
    REDIS_HOST = "127.0.0.1"
    REDIS_PASS = os.environ.get("REDIS_PASSWORD", "")

    def __init__(self, redis_client=None, safety: SafetyBoundary = None):
        if redis_client is None:
            try:
                import redis as _redis
                redis_client = _redis.Redis(
                    host=self.REDIS_HOST, port=6379,
                    password=self.REDIS_PASS, decode_responses=True)
            except Exception as e:
                print(f"[Factory] Redis连接失败: {e}")
        self.redis = redis_client
        self.safety = safety

    async def create(
        self,
        role: str,
        quadrant: AgentQuadrant,
        task: Optional[Dict] = None,
        custom_design: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        创建一个 Agent 实例。
        返回 agent_id（即 pm2 进程名: agent-{name}），失败返回 None。
        """
        # 安全边界检查
        if self.safety:
            check = self.safety.check_action("create_agent", {
                "scope": "commander",
                "role": role,
            })
            if not check["allowed"]:
                print(f"[Factory] 安全边界拒绝创建: {check['reason']}")
                return None

        # 生成唯一名称
        suffix = uuid.uuid4().hex[:6]
        agent_name = f"{self._slugify(role)}-{suffix}"

        # 1. 创建 Skill + 注册 Agent 模板
        prompt = (custom_design or {}).get("prompt_template", f"{role} - 自动生成")
        result = await self._run_shell("create", agent_name, role, prompt)
        if not result:
            return None

        # 2. 启动 Agent 进程
        result = await self._run_shell("spawn", agent_name, role)
        if not result:
            # 回滚：删除已创建的 Skill
            await self._run_shell("destroy", agent_name)
            return None

        # 3. 等待 Agent 上线并上报心跳
        agent_id = f"agent-{agent_name}"
        alive = await self._wait_for_agent(role, timeout=5)
        if not alive:
            print(f"[Factory] Agent {agent_id} 启动超时，销毁")
            await self.destroy(agent_id)
            return None

        print(f"[Factory] 创建成功: {agent_id} (角色: {role})")
        return agent_id

    async def destroy(self, agent_id: str) -> bool:
        """
        销毁 Agent。
        agent_id 格式: agent-{name} 或直接 {name}
        """
        name = agent_id.replace("agent-", "", 1)
        await self._run_shell("destroy", name)
        return True

    async def _run_shell(self, action: str, name: str, role: str = "", prompt: str = "") -> bool:
        """异步执行 shell 工厂命令。"""
        cmd = ["bash", self.FACTORY_SCRIPT, action, name]
        if role:
            cmd.append(role)
        if prompt:
            cmd.append(prompt)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30
            )
            return proc.returncode == 0
        except asyncio.TimeoutError:
            print(f"[Factory] 命令超时: {' '.join(cmd)}")
            return False
        except Exception as e:
            print(f"[Factory] 命令失败: {e}")
            return False

    async def _wait_for_agent(self, role: str, timeout: int = 15) -> bool:
        """等待 Agent 通过 Redis 上报心跳确认存活。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # 发布 heartbeat_check 并检查是否有订阅者响应
                if self.redis:
                    subs = self.redis.publish(
                        f"lightingmetal:agent:{role}",
                        json.dumps({"type": "heartbeat_check", "to": role}),
                    )
                    if subs and subs > 0:
                        return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    @staticmethod
    def _slugify(name: str) -> str:
        """
        中文转拼音简写。
        优先使用 pypinyin，fallback 到硬编码映射，再 fallback 到 hash 短码。
        """
        slug_map = {
            "翻译官": "translator", "商务经理": "business",
            "售前经理": "presales", "审计官": "auditor",
            "俄语审计官": "ru-auditor", "数据迁移Agent": "data-migrator",
            "心跳监控Agent": "heartbeat", "日志清理Agent": "log-cleaner",
            "一次性数据导出Agent": "data-exporter",
        }
        if name in slug_map:
            return slug_map[name]

        if HAS_PYPINYIN:
            pinyin = lazy_pinyin(name)
            return "-".join(pinyin).lower()

        # 最终 fallback：hash 短码（稳定，不会因中文编码变化）
        h = hashlib.md5(name.encode()).hexdigest()[:6]
        return f"agent-{h}"


# ═══════════════════════════════════════════════════════════════
# 3. AgentDesigner — 自主设计 Agent 能力规格
# ═══════════════════════════════════════════════════════════════

class AgentDesigner:
    """根据任务需求，自主设计 Agent 的能力规格（LLM 驱动）。"""

    # 能力映射表（可扩展）
    CAPABILITY_MAP = {
        "阿拉伯语翻译":    ["ar-SA翻译", "术语词典"],
        "俄语翻译":        ["ru-RU翻译", "术语词典"],
        "西班牙语翻译":    ["es-ES翻译", "术语词典"],
        "法语翻译":        ["fr-FR翻译", "术语词典"],
        "英语翻译":        ["en-US翻译", "术语词典"],
        "光伏产品知识":    ["光伏支架", "螺旋地桩", "防腐标准"],
        "报价计算":        ["产品查询", "价格计算", "方案生成"],
        "客户沟通":        ["需求挖掘", "多语言沟通", "商务谈判"],
        "内容审计":        ["术语一致性检查", "参数核查", "质量报告"],
        "数据分析":        ["MongoDB查询", "数据聚合", "报表生成"],
        "SEO优化":         ["关键词分析", "结构化数据", "hreflang配置"],
    }

    # 默认能力（未匹配时通过 LLM 推导）
    DEFAULT_CAPABILITIES = ["通用任务处理", "信息检索"]

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def analyze_and_design(self, task: dict) -> dict:
        """分析任务，输出 Agent 设计方案。"""
        # 1. 任务语义分析
        task_analysis = await self._analyze_task(task)

        # 2. 能力需求推导
        required_capabilities = self._derive_capabilities(task_analysis)

        # 3. 知识库需求分析
        required_knowledge = self._derive_knowledge(task_analysis)

        # 4. 生成 Agent 设计方案
        design = {
            "role": self._generate_role_name(task_analysis),
            "capabilities": required_capabilities,
            "knowledge_sources": required_knowledge,
            "skills": self._design_skills(required_capabilities),
            "prompt_template": self._generate_prompt(task_analysis),
            "communication_rules": self._design_comm_rules(task_analysis),
            "expected_lifespan": self._estimate_lifespan(task_analysis),
        }
        return design

    async def _analyze_task(self, task: dict) -> dict:
        """LLM 语义分析：这个任务到底在做什么。"""
        if not self.llm:
            return self._fallback_analyze(task)

        prompt = f"""
分析以下任务，提取：
1. 核心目标（一句话）
2. 涉及的业务领域（如：外贸、翻译、数据分析）
3. 需要的专业技能（如：阿拉伯语翻译、光伏产品知识、报价计算）
4. 任务的复杂度（简单/中等/复杂）
5. 是否需要与其他 Agent 协作

任务描述：{json.dumps(task, ensure_ascii=False)}

输出 JSON 格式：
{{"core_objective":"...","domain":"...","required_skills":["..."],"complexity":"medium","needs_collaboration":false}}
"""
        try:
            response = await self._llm_chat(prompt)
            return json.loads(response)
        except Exception as e:
            print(f"[Designer] LLM 分析失败: {e}，使用 fallback")
            return self._fallback_analyze(task)

    def _fallback_analyze(self, task: dict) -> dict:
        """无 LLM 时的关键词 fallback 分析。"""
        desc = json.dumps(task, ensure_ascii=False).lower()
        skills = []
        domain = "通用"

        if any(kw in desc for kw in ["翻", "translat", "俄语", "араб", "西班牙"]):
            skills.append("多语翻译")
            domain = "翻译"
        if any(kw in desc for kw in ["报价", "报", "quot", "价格"]):
            skills.append("报价计算")
            domain = "商务"
        if any(kw in desc for kw in ["审计", "检查", "审核", "audit", "qa"]):
            skills.append("内容审计")
            domain = "质量"
        if any(kw in desc for kw in ["光伏", "solar", "支架", "地桩"]):
            skills.append("光伏产品知识")
            domain = "工业"

        return {
            "core_objective": task.get("description", str(task)[:100]),
            "domain": domain,
            "required_skills": skills or ["通用处理"],
            "complexity": "medium",
            "needs_collaboration": False,
        }

    def _derive_capabilities(self, analysis: dict) -> list:
        """从任务分析中推导所需能力。"""
        capabilities = []
        for skill in analysis.get("required_skills", []):
            if skill in self.CAPABILITY_MAP:
                capabilities.extend(self.CAPABILITY_MAP[skill])
            else:
                capabilities.append(skill)
        return list(set(capabilities)) or self.DEFAULT_CAPABILITIES

    def _derive_knowledge(self, analysis: dict) -> list:
        """推导所需知识库来源。"""
        knowledge_sources = []
        domain = analysis.get("domain", "")
        for skill in analysis.get("required_skills", []):
            if "翻译" in skill:
                knowledge_sources.append("术语词典(MongoDB)")
            if "产品" in skill or "报价" in skill:
                knowledge_sources.append("产品库(MongoDB page_content)")
            if "客户" in skill:
                knowledge_sources.append("客户画像(CRM)")
        return knowledge_sources or ["通用知识库"]

    def _generate_role_name(self, analysis: dict) -> str:
        """根据任务分析生成角色名。"""
        domain = analysis.get("domain", "通用")
        complexity = analysis.get("complexity", "medium")
        prefix = "高级" if complexity == "complex" else ""
        return f"{prefix}{domain}专员"

    def _design_skills(self, capabilities: list) -> list:
        """根据能力设计 Skill 描述。"""
        return [{"name": cap, "level": "required"} for cap in capabilities]

    def _generate_prompt(self, analysis: dict) -> str:
        """根据任务分析自动生成系统提示词。"""
        domain = analysis.get("domain", "通用")
        objective = analysis.get("core_objective", "完成分配的任务")
        skills = analysis.get("required_skills", [])
        return f"""
你是一个专业的 {domain} 领域的 AI 助手。
你的核心任务是：{objective}
你需要具备以下能力：{', '.join(skills) if skills else '通用任务处理'}
请用专业、严谨的风格回复，确保术语准确。
"""

    def _design_comm_rules(self, analysis: dict) -> dict:
        """设计通信规则。"""
        collab = analysis.get("needs_collaboration", False)
        return {
            "protocol": "Redis Pub/Sub",
            "p2p_enabled": collab,
            "ack_required": analysis.get("complexity") == "complex",
            "timeout_seconds": 30,
        }

    def _estimate_lifespan(self, analysis: dict) -> str:
        """估算 Agent 生命周期类型。"""
        complexity = analysis.get("complexity", "medium")
        if complexity == "simple":
            return "ephemeral"
        elif complexity == "medium":
            return "utility"
        else:
            return "strategic"

    async def _llm_chat(self, prompt: str) -> str:
        """调用 LLM（自动适配 LLMAdapter / 旧接口）。"""
        return await _llm_chat_helper(self.llm, prompt)


# ═══════════════════════════════════════════════════════════════
# 4. AutonomousTaskDecomposer — 自主任务拆解
# ═══════════════════════════════════════════════════════════════

class AutonomousTaskDecomposer:
    """自主任务拆解器：从模糊需求到原子任务序列（LLM 驱动）。"""

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def decompose(self, user_intent: str, context: dict = None) -> dict:
        """将用户意图拆解为完整的执行计划。"""
        ctx = context or {}

        # 1. 意图理解
        intent = await self._understand_intent(user_intent, ctx)

        # 2. 生成执行计划
        plan = await self._generate_plan(intent, ctx)

        # 3. 识别依赖关系（算法验证 + 补充）补充 LLM 遗漏的依赖
        plan_with_deps = self._analyze_dependencies(plan)

        # 4. 评估可行性
        feasibility = await self._assess_feasibility(plan_with_deps)

        return {
            "intent": intent,
            "plan": plan_with_deps,
            "feasibility": feasibility,
            "estimated_agents_needed": self._count_required_agents(plan_with_deps),
        }

    async def _understand_intent(self, user_intent: str, context: dict) -> dict:
        """LLM 意图理解。"""
        if not self.llm:
            return self._fallback_intent(user_intent)

        prompt = f"""
理解以下用户意图，提取结构化信息：

用户输入：{user_intent}
上下文：{json.dumps(context, ensure_ascii=False)}

输出 JSON：
{{"goal":"一句话核心目标","domain":"业务领域","complexity":"simple|medium|complex","urgency":"low|normal|high","constraints":[],"expected_output":"期望产出"}}
"""
        try:
            response = await self._llm_chat(prompt)
            return json.loads(response)
        except Exception as e:
            print(f"[Decomposer] 意图理解失败: {e}")
            return self._fallback_intent(user_intent)

    def _fallback_intent(self, user_intent: str) -> dict:
        """无 LLM 时的关键词 fallback。"""
        return {
            "goal": user_intent[:100],
            "domain": "通用",
            "complexity": "medium",
            "urgency": "normal",
            "constraints": [],
            "expected_output": "完成任务",
        }

    async def _generate_plan(self, intent: dict, context: dict) -> list:
        """LLM 生成执行计划（原子任务序列）。"""
        if not self.llm:
            return self._fallback_plan(intent)

        prompt = f"""
将一个用户需求拆解为可执行的原子任务序列。

用户意图：{json.dumps(intent, ensure_ascii=False)}
上下文信息：{json.dumps(context, ensure_ascii=False)}

拆解规则：
1. 每个原子任务必须有明确的输入、输出、成功标准
2. 标注任务之间的依赖关系（depends_on）
3. 每个任务标注需要的能力类型（agent_type）
4. 预估每个任务的执行时间（estimated_seconds）

输出 JSON 数组：
[
  {{
    "id": "task-1",
    "type": "audit|translate|query|generate|communicate",
    "agent_type": "审计官|翻译官|售前经理|商务经理",
    "description": "任务描述",
    "input": "输入",
    "output": "期望输出",
    "success_criteria": "成功标准",
    "depends_on": [],
    "estimated_seconds": 60,
    "priority": 1
  }}
]
"""
        try:
            response = await self._llm_chat(prompt)
            return json.loads(response)
        except Exception as e:
            print(f"[Decomposer] 计划生成失败: {e}")
            return self._fallback_plan(intent)

    def _fallback_plan(self, intent: dict) -> list:
        """无 LLM 时的简单线性计划。"""
        goal = intent.get("goal", "")
        domain = intent.get("domain", "通用")
        agent_map = {"翻译": "翻译官", "商务": "商务经理", "质量": "审计官"}
        agent = "通用Agent"
        for k, v in agent_map.items():
            if k in domain:
                agent = v
                break

        return [{
            "id": "task-1",
            "type": "general",
            "agent_type": agent,
            "description": goal,
            "input": "用户意图",
            "output": "执行结果",
            "success_criteria": "任务完成",
            "depends_on": [],
            "estimated_seconds": 120,
            "priority": 1,
        }]

    def _analyze_dependencies(self, plan: list) -> list:
        """
        算法验证并补充 LLM 生成的依赖关系。
        规则：
          - 同一 agent_type 的连续任务按 ID +1 依赖
          - 类型为 'generate' 的任务依赖同链上所有 'query' 任务
          - 不覆盖 LLM 已有的 depends_on
        """
        for i, task in enumerate(plan):
            if not task.get("depends_on"):
                task["depends_on"] = []

            # 规则1：连续同 Agent 任务串行依赖
            if i > 0 and task.get("agent_type") == plan[i - 1].get("agent_type"):
                prev_id = plan[i - 1]["id"]
                if prev_id not in task["depends_on"]:
                    task["depends_on"].append(prev_id)

            # 规则2：generate 依赖所有 query 任务
            if task.get("type") == "generate":
                for prev in plan[:i]:
                    if prev.get("type") == "query" and prev["id"] not in task["depends_on"]:
                        task["depends_on"].append(prev["id"])

        return plan

    async def _assess_feasibility(self, plan: list) -> dict:
        """评估执行计划的可行性。"""
        if not plan:
            return {"feasible": False, "reason": "空计划", "risks": []}

        risks = []
        # 循环依赖检测
        all_ids = {t["id"] for t in plan}
        for task in plan:
            for dep in task.get("depends_on", []):
                if dep not in all_ids:
                    risks.append(f"任务 {task['id']} 依赖未知任务 {dep}")
                if dep == task["id"]:
                    risks.append(f"任务 {task['id']} 自依赖")

        # LLM 深度可行性评估（可选）
        if self.llm and len(plan) > 3:
            try:
                prompt = f"""
评估以下执行计划的可行性，识别风险：

{json.dumps(plan, ensure_ascii=False)}

输出 JSON：{{"feasible":true|false,"reason":"...","risks":["..."]}}
"""
                response = await self._llm_chat(prompt)
                llm_assessment = json.loads(response)
                risks.extend(llm_assessment.get("risks", []))
                return {
                    "feasible": llm_assessment.get("feasible", True),
                    "reason": llm_assessment.get("reason", ""),
                    "risks": risks,
                }
            except Exception:
                pass

        return {
            "feasible": len(risks) == 0,
            "reason": "ok" if not risks else f"发现 {len(risks)} 个风险",
            "risks": risks,
        }

    def _count_required_agents(self, plan: list) -> int:
        """统计不同 agent_type 的数量。"""
        return len(set(task.get("agent_type", "通用") for task in plan))

    async def _llm_chat(self, prompt: str) -> str:
        """调用 LLM（自动适配 LLMAdapter / 旧接口）。"""
        return await _llm_chat_helper(self.llm, prompt)


# ═══════════════════════════════════════════════════════════════
# 5. AgentLifecycleManagerV2 — 四象限生命周期管理器
# ═══════════════════════════════════════════════════════════════

class AgentLifecycleManagerV2:
    """
    基于四象限的 Agent 生命周期管理器。
    让 Commander 能够根据业务需要自主创建、保留、销毁 Agent。

    修复说明（v2.1）：
      - ✅ last_active 统一字段名
      - ✅ Redis 角色索引加速计数
      - ✅ 原子化 Agent 创建
      - ✅ 评估循环指数退避
      - ✅ SQLite 审计日志
      - ✅ 可配置的 error_rate 阈值
    """

    def __init__(
        self,
        redis_client,
        store=None,
        agent_factory: AsyncAgentFactory = None,
        safety: SafetyBoundary = None,
        llm_client=None,
        config: Optional[Dict] = None,
    ):
        self.redis = redis_client
        self.store = store
        self.factory = agent_factory
        self.safety = safety
        self.llm = llm_client

        # 默认配置
        self.config = {
            "core": {
                "min_instances": 1,
                "max_instances": 3,
                "idle_timeout": float('inf'),
                "heartbeat_interval": 10,
                "heartbeat_retries": 3,
                "restart_on_failure": True,
                "error_rate_threshold": 0.3,
                "min_tasks_for_error_check": 10,
            },
            "strategic": {
                "min_instances": 0,
                "max_instances": 2,
                "idle_timeout": 1800,
                "heartbeat_interval": 30,
                "heartbeat_retries": 2,
                "restart_on_failure": False,
                "cooldown_period": 300,
                "error_rate_threshold": 0.3,
                "min_tasks_for_error_check": 5,
            },
            "utility": {
                "min_instances": 0,
                "max_instances": 5,
                "idle_timeout": 600,
                "heartbeat_interval": 0,
                "heartbeat_retries": 0,
                "restart_on_failure": False,
                "memory_limit_mb": 256,
                "max_concurrent_tasks": 3,
                "error_rate_threshold": 0.5,
                "min_tasks_for_error_check": 10,
            },
            "ephemeral": {
                "min_instances": 0,
                "max_instances": 10,
                "idle_timeout": 60,
                "heartbeat_interval": 0,
                "heartbeat_retries": 0,
                "restart_on_failure": False,
                "destroy_on_completion": True,
                "error_rate_threshold": 0.5,
                "min_tasks_for_error_check": 3,
            },
            "global": {
                "max_agents": 50,
                "evaluation_interval": 30,
                "promotion_success_rate": 0.95,
                "promotion_min_tasks": 100,
                "evaluation_backoff_min": 1,
                "evaluation_backoff_max": 60,
            },
        }
        if config:
            self._deep_update(self.config, config)

        # 评估循环状态
        self._eval_consecutive_failures = 0

    async def start(self):
        """启动管理器：创建 Core Agent 并开始周期性评估。"""
        print("[LifecycleManager] 启动四象限生命周期管理器...")
        await self._ensure_core_agents()
        asyncio.create_task(self._evaluation_loop())

    async def _ensure_core_agents(self):
        """确保 Core 象限的 Agent 全部在线。"""
        core_roles = self._get_registered_roles_by_quadrant(AgentQuadrant.CORE)
        for role_name in core_roles:
            await self._ensure_min_instances(role_name, AgentQuadrant.CORE)

    async def _evaluation_loop(self):
        """周期性评估所有 Agent（带指数退避）。"""
        global_cfg = self.config["global"]
        while True:
            try:
                await self.evaluate_all_agents()
                self._eval_consecutive_failures = 0
            except Exception as e:
                self._eval_consecutive_failures += 1
                backoff = min(
                    global_cfg["evaluation_backoff_max"],
                    global_cfg["evaluation_backoff_min"] * (2 ** (self._eval_consecutive_failures - 1)),
                )
                print(f"[LifecycleManager] 评估循环出错 (#{self._eval_consecutive_failures}): {e}，退避 {backoff}s")
                await asyncio.sleep(backoff)
                continue
            await asyncio.sleep(global_cfg["evaluation_interval"])

    # ── 公共接口：请求 / 释放 Agent ──

    async def request_agent(
        self,
        role: str,
        quadrant: AgentQuadrant,
        task: Optional[Dict] = None,
        custom_design: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Commander 调用此接口获取一个 Agent。
        按象限规则决定：复用空闲 → 创建新实例 → 拒绝（达上限）。
        返回 agent_id 或 None。
        """
        # 安全边界检查
        if self.safety:
            check = self.safety.check_action("request_agent", {
                "scope": "commander",
                "role": role,
            })
            if check["allowed"] is not True:
                print(f"[LifecycleManager] 安全边界: {check['reason']}")
                return None

        # 1. 检查象限 Agent 数量
        current_count = self._count_agents_by_quadrant(quadrant)
        max_instances = self.config[quadrant.value]["max_instances"]
        if current_count >= max_instances:
            idle_agent = await self._find_idle_agent(role, quadrant)
            if idle_agent:
                return idle_agent
            print(f"[LifecycleManager] 象限 {quadrant.value} 已达上限 {max_instances}")
            return None

        # 2. 创建新 Agent
        agent_id = await self._create_agent(role, quadrant, task, custom_design)
        return agent_id

    # ── 核心评估逻辑 ──

    async def evaluate_all_agents(self) -> List[Dict]:
        """评估所有 Agent，返回决策列表。"""
        all_agents = self._get_all_agent_ids()
        decisions = []
        for agent_id in all_agents:
            decision = await self._evaluate_single_agent(agent_id)
            if decision["action"] != "keep":
                decisions.append(decision)
                await self._execute_decision(decision)
        return decisions

    async def _evaluate_single_agent(self, agent_id: str) -> Dict:
        """评估单个 Agent，返回决策。"""
        metadata = self._get_agent_metadata(agent_id)
        if not metadata:
            return {"agent_id": agent_id, "action": "ignore", "reason": "元数据缺失"}

        quadrant = AgentQuadrant(metadata.get("quadrant", "ephemeral"))
        idle_time = self._get_idle_time(agent_id)
        error_rate = self._get_error_rate(agent_id)
        task_count = self._get_task_count(agent_id)
        config = self.config[quadrant.value]

        # 决策1：Ephemeral 任务完成 → 立即销毁
        if quadrant == AgentQuadrant.EPHEMERAL and metadata.get("task_completed") == "true":
            return {"agent_id": agent_id, "action": "destroy", "reason": "临时任务已完成"}

        # 决策2：空闲超时 → 销毁（Core 永不因空闲销毁）
        if quadrant != AgentQuadrant.CORE and idle_time > config["idle_timeout"]:
            return {"agent_id": agent_id, "action": "destroy",
                    "reason": f"空闲 {idle_time:.0f}s，超过阈值 {config['idle_timeout']}s"}

        # 决策3：错误率过高 → 暂停并重建
        error_threshold = config.get("error_rate_threshold", 0.3)
        min_tasks = config.get("min_tasks_for_error_check", 10)
        if error_rate > error_threshold and task_count > min_tasks and config.get("restart_on_failure"):
            return {"agent_id": agent_id, "action": "pause_and_rebuild",
                    "reason": f"错误率 {error_rate:.1%} (阈值 {error_threshold:.0%})"}

        # 决策4：负载过高 → 建议扩容
        current_load = int(metadata.get("current_load", 0))
        max_load = config.get("max_concurrent_tasks", 5)
        if current_load >= max_load:
            return {"agent_id": agent_id, "action": "suggest_scale_out",
                    "reason": f"负载 {current_load} 达到上限 {max_load}"}

        # 决策5：表现优异 → 提升象限
        global_cfg = self.config["global"]
        success_rate = 1 - error_rate
        if (quadrant == AgentQuadrant.UTILITY
                and success_rate > global_cfg["promotion_success_rate"]
                and task_count > global_cfg["promotion_min_tasks"]):
            return {"agent_id": agent_id, "action": "promote_to_strategic",
                    "reason": f"成功率 {success_rate:.1%}，任务数 {task_count}"}

        return {"agent_id": agent_id, "action": "keep", "reason": "状态正常"}

    # ── 决策执行 ──

    async def _execute_decision(self, decision: Dict):
        """执行决策。"""
        action = decision["action"]
        agent_id = decision["agent_id"]

        if action == "destroy":
            await self._destroy_agent(agent_id, decision.get("reason", ""))
        elif action == "pause_and_rebuild":
            await self._pause_agent(agent_id)
            metadata = self._get_agent_metadata(agent_id)
            if metadata:
                await self._create_agent(
                    metadata["role"],
                    AgentQuadrant(metadata["quadrant"]),
                    task={"reason": "重建: 错误率过高"},
                )
        elif action == "suggest_scale_out":
            self.redis.hset("lifecycle:suggestions", agent_id, "scale_out")
        elif action == "promote_to_strategic":
            await self._update_quadrant(agent_id, AgentQuadrant.STRATEGIC)

    # ── 原子化创建/销毁 ──

    async def _create_agent(
        self,
        role: str,
        quadrant: AgentQuadrant,
        task: Optional[Dict] = None,
        custom_design: Optional[Dict] = None,
    ) -> Optional[str]:
        """原子化创建 Agent：工厂创建 → Redis 注册 → MongoDB 审计。"""
        if not self.factory:
            print("[LifecycleManager] 无可用工厂，无法创建 Agent")
            return None

        agent_id = await self.factory.create(role, quadrant, task, custom_design)
        if not agent_id:
            return None

        now = datetime.now().isoformat()
        metadata = {
            "role": role,
            "quadrant": quadrant.value,
            "status": AgentStatus.RUNNING.value,
            "created_at": now,
            "last_active": now,
            "current_load": "0",
            "total_tasks": "0",
            "error_count": "0",
            "task_completed": "false",
        }

        try:
            # 原子化：pipeline 写入
            pipe = self.redis.pipeline()
            pipe.hset(f"agent:metadata:{agent_id}", mapping=metadata)
            pipe.sadd(f"agent:quadrant:{quadrant.value}", agent_id)
            # 角色索引（加速 _count_agents_by_role_and_quadrant）
            pipe.sadd(f"agent:role:{role}:{quadrant.value}", agent_id)
            pipe.execute()

            # SQLite 审计日志
            await self._audit_log("agent_created", {
                "agent_id": agent_id,
                "role": role,
                "quadrant": quadrant.value,
                "timestamp": now,
            })

            print(f"[LifecycleManager] 创建 Agent: {agent_id} (角色: {role}, 象限: {quadrant.value})")
        except Exception as e:
            print(f"[LifecycleManager] Redis 注册失败: {e}，回滚销毁 {agent_id}")
            await self.factory.destroy(agent_id)
            return None

        return agent_id

    async def _destroy_agent(self, agent_id: str, reason: str = ""):
        """销毁 Agent 并清理所有元数据。"""
        metadata = self._get_agent_metadata(agent_id)

        if metadata:
            quadrant = metadata.get("quadrant", "ephemeral")
            role = metadata.get("role", "")
            pipe = self.redis.pipeline()
            pipe.srem(f"agent:quadrant:{quadrant}", agent_id)
            pipe.srem(f"agent:role:{role}:{quadrant}", agent_id)
            pipe.delete(f"agent:metadata:{agent_id}")
            pipe.delete(f"agent:heartbeat:{agent_id}")
            pipe.execute()

        if self.factory:
            await self.factory.destroy(agent_id)

        # SQLite 审计日志
        await self._audit_log("agent_destroyed", {
            "agent_id": agent_id,
            "reason": reason,
            "metadata": metadata,
            "timestamp": datetime.now().isoformat(),
        })

        print(f"[LifecycleManager] 销毁 Agent: {agent_id} ({reason})")

    async def _update_quadrant(self, agent_id: str, new_quadrant: AgentQuadrant):
        """更新 Agent 的象限分类。"""
        metadata = self._get_agent_metadata(agent_id)
        if not metadata:
            return

        old_quadrant = metadata.get("quadrant", "ephemeral")
        role = metadata.get("role", "")

        pipe = self.redis.pipeline()
        pipe.srem(f"agent:quadrant:{old_quadrant}", agent_id)
        pipe.srem(f"agent:role:{role}:{old_quadrant}", agent_id)
        metadata["quadrant"] = new_quadrant.value
        pipe.hset(f"agent:metadata:{agent_id}", mapping=metadata)
        pipe.sadd(f"agent:quadrant:{new_quadrant.value}", agent_id)
        pipe.sadd(f"agent:role:{role}:{new_quadrant.value}", agent_id)
        pipe.execute()

        print(f"[LifecycleManager] Agent {agent_id} 象限变更: {old_quadrant} → {new_quadrant.value}")

        await self._audit_log("quadrant_changed", {
            "agent_id": agent_id,
            "from_quadrant": old_quadrant,
            "to_quadrant": new_quadrant.value,
            "timestamp": datetime.now().isoformat(),
        })

    async def _pause_agent(self, agent_id: str):
        """暂停 Agent。"""
        metadata = self._get_agent_metadata(agent_id)
        if metadata:
            metadata["status"] = AgentStatus.PAUSED.value
            self.redis.hset(f"agent:metadata:{agent_id}", mapping=metadata)
        print(f"[LifecycleManager] 暂停 Agent: {agent_id}")

    async def _ensure_min_instances(self, role: str, quadrant: AgentQuadrant):
        """确保某个角色至少有最小数量的实例在运行。"""
        current = self._count_agents_by_role_and_quadrant(role, quadrant)
        min_inst = self.config[quadrant.value]["min_instances"]
        for _ in range(max(0, min_inst - current)):
            await self._create_agent(role, quadrant)

    # ── 查询辅助方法 ──

    def _get_all_agent_ids(self) -> List[str]:
        """获取所有注册的 Agent ID。"""
        try:
            keys = self.redis.keys("agent:metadata:*")
            return [k.decode().split(":")[-1] for k in keys]
        except Exception:
            return []

    def _get_agent_metadata(self, agent_id: str) -> Optional[Dict]:
        """获取 Agent 元数据。"""
        try:
            data = self.redis.hgetall(f"agent:metadata:{agent_id}")
            return {k.decode(): v.decode() for k, v in data.items()} if data else None
        except Exception:
            return None

    def _get_idle_time(self, agent_id: str) -> float:
        """计算 Agent 空闲时间（秒）。使用 last_active 字段。"""
        try:
            last_active = self.redis.hget(f"agent:metadata:{agent_id}", "last_active")
            if not last_active:
                return 0
            return (datetime.now() - datetime.fromisoformat(last_active.decode())).total_seconds()
        except Exception:
            return 0

    def _get_error_rate(self, agent_id: str) -> float:
        """计算错误率。"""
        try:
            total = int(self.redis.hget(f"agent:metadata:{agent_id}", "total_tasks") or 0)
            errors = int(self.redis.hget(f"agent:metadata:{agent_id}", "error_count") or 0)
            return errors / total if total > 0 else 0.0
        except Exception:
            return 0.0

    def _get_task_count(self, agent_id: str) -> int:
        """获取总任务数。"""
        try:
            return int(self.redis.hget(f"agent:metadata:{agent_id}", "total_tasks") or 0)
        except Exception:
            return 0

    def _count_agents_by_quadrant(self, quadrant: AgentQuadrant) -> int:
        """统计某象限的 Agent 总数。"""
        try:
            return self.redis.scard(f"agent:quadrant:{quadrant.value}") or 0
        except Exception:
            return 0

    def _count_agents_by_role_and_quadrant(self, role: str, quadrant: AgentQuadrant) -> int:
        """统计某角色在某象限的实例数（使用角色索引，O(1)）。"""
        try:
            return self.redis.scard(f"agent:role:{role}:{quadrant.value}") or 0
        except Exception:
            return 0

    async def _find_idle_agent(self, role: str, quadrant: AgentQuadrant) -> Optional[str]:
        """寻找同角色同象限的空闲 Agent。"""
        try:
            agents = self.redis.smembers(f"agent:role:{role}:{quadrant.value}")
            for aid in agents:
                aid_str = aid.decode() if isinstance(aid, bytes) else aid
                metadata = self._get_agent_metadata(aid_str)
                if metadata and metadata.get("status") == "idle":
                    return aid_str
        except Exception:
            pass
        return None

    def _get_registered_roles_by_quadrant(self, quadrant: AgentQuadrant) -> List[str]:
        """获取某象限预注册的角色列表。"""
        try:
            roles = self.redis.smembers(f"lifecycle:roles:{quadrant.value}")
            return [r.decode() for r in roles] if roles else []
        except Exception:
            return []

    # ── SQLite 审计日志 ──

    async def _audit_log(self, event: str, data: dict):
        """写入 SQLite 审计日志（非阻塞 fire-and-forget）。"""
        if not self.store:
            return
        try:
            collection = self.store["example_db"]["agent_audit_log"]
            doc = {"event": event, "data": data, "timestamp": datetime.now()}
            # 使用 asyncio 线程池避免阻塞事件循环
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, collection.insert_one, doc)
        except Exception as e:
            # 审计日志失败不应影响主流程
            print(f"[LifecycleManager] 审计日志写入失败: {e}")

    @staticmethod
    def _deep_update(d, u):
        """深度合并字典。"""
        for k, v in u.items():
            if isinstance(v, collections.abc.Mapping):
                d[k] = AgentLifecycleManagerV2._deep_update(d.get(k, {}), v)
            else:
                d[k] = v
        return d


# ═══════════════════════════════════════════════════════════════
# 6. SelfEvolvingCommander — 自我进化的 Commander
# ═══════════════════════════════════════════════════════════════

class SelfEvolvingCommander:
    """
    自我进化模块：分析历史运行数据，发现优化机会，通过 A/B 测试验证。
    与 ABTester 互补：ABTester 测调度参数，本模块测整体策略改进。
    """

    def __init__(
        self,
        redis_client,
        store=None,
        llm_client=None,
        lifecycle_manager: AgentLifecycleManagerV2 = None,
    ):
        self.redis = redis_client
        self.store = store
        self.llm = llm_client
        self.lifecycle = lifecycle_manager

    async def evolve(self) -> dict:
        """定期执行：自我优化。"""
        # 1. 分析历史数据
        patterns = await self._analyze_historical_patterns()

        # 2. 发现优化机会
        optimizations = await self._discover_optimizations(patterns)

        # 3. 生成改进方案
        improvements = await self._generate_improvements(optimizations)

        # 4. A/B 测试验证
        applied_count = 0
        for improvement in improvements:
            success = await self._validate_and_apply(improvement)
            if success:
                applied_count += 1

        return {
            "patterns_found": len(patterns),
            "optimizations_discovered": len(optimizations),
            "improvements_applied": applied_count,
        }

    async def _analyze_historical_patterns(self) -> list:
        """从 SQLite 审计日志 + Redis 指标中提取历史模式。"""
        patterns = []

        # 从 Redis 提取 Agent 性能指标
        try:
            all_agents = self.redis.keys("agent:metadata:*")
            for key in all_agents:
                agent_id = key.decode().split(":")[-1]
                meta = self._get_agent_meta(agent_id)
                if meta:
                    patterns.append({
                        "type": "agent_performance",
                        "agent_id": agent_id,
                        "role": meta.get("role", ""),
                        "quadrant": meta.get("quadrant", ""),
                        "total_tasks": int(meta.get("total_tasks", 0)),
                        "error_count": int(meta.get("error_count", 0)),
                    })
        except Exception as e:
            print(f"[Evolving] Redis 模式分析失败: {e}")

        # 从 MongoDB 提取审计日志模式
        if self.store:
            try:
                collection = self.store["example_db"]["agent_audit_log"]
                # 最近 7 天的创建/销毁事件
                seven_days_ago = datetime.now().isoformat()
                cursor = collection.find({
                    "timestamp": {"$gte": seven_days_ago[:10]},
                    "event": {"$in": ["agent_created", "agent_destroyed"]},
                }).limit(200)
                for doc in cursor:
                    patterns.append({
                        "type": "lifecycle_event",
                        "event": doc.get("event", ""),
                        "data": doc.get("data", {}),
                    })
            except Exception as e:
                print(f"[Evolving] MongoDB 模式分析失败: {e}")

        return patterns

    async def _discover_optimizations(self, patterns: list) -> list:
        """从历史模式中发现优化机会。"""
        if not self.llm or not patterns:
            return self._fallback_optimizations(patterns)

        prompt = f"""
分析以下 Agent 系统运行模式，找出 3-5 个可优化的点：

{json.dumps(patterns[:50], ensure_ascii=False, default=str)}

从以下维度分析：
1. 任务拆分效率（是否有过度拆分或拆分不足）
2. Agent 调度效率（是否有更好的派单策略）
3. Agent 模板优化（是否有冗余或不合理的能力组合）
4. 通信效率（是否有可合并或精简的通信）

输出 JSON 数组：
[{{"dimension":"...","finding":"...","suggestion":"...","expected_impact":"high|medium|low","confidence":0.8}}]
"""
        try:
            response = await self._llm_chat(prompt)
            return json.loads(response)
        except Exception as e:
            print(f"[Evolving] 优化发现失败: {e}")
            return self._fallback_optimizations(patterns)

    def _fallback_optimizations(self, patterns: list) -> list:
        """无 LLM 时的规则优化。"""
        optimizations = []
        # 规则1：高错误率 Agent 建议重建
        for p in patterns:
            if p.get("type") == "agent_performance":
                total = p.get("total_tasks", 0)
                errors = p.get("error_count", 0)
                if total > 10 and errors / total > 0.3:
                    optimizations.append({
                        "dimension": "Agent 模板优化",
                        "finding": f"Agent {p['agent_id']} 错误率 {errors/total:.1%}",
                        "suggestion": f"建议重建 {p.get('role', '')} Agent",
                        "expected_impact": "high",
                        "confidence": 0.9,
                    })

        # 规则2：频繁创建销毁建议提升象限
        create_count = sum(1 for p in patterns if p.get("event") == "agent_created")
        destroy_count = sum(1 for p in patterns if p.get("event") == "agent_destroyed")
        if create_count > 5 and destroy_count > 5:
            optimizations.append({
                "dimension": "Agent 调度效率",
                "finding": f"频繁创建({create_count})和销毁({destroy_count}) Agent",
                "suggestion": "考虑将高频角色提升为 strategic 象限，减少创建开销",
                "expected_impact": "medium",
                "confidence": 0.7,
            })

        return optimizations

    async def _generate_improvements(self, optimizations: list) -> list:
        """将优化建议转化为可执行的改进方案。"""
        improvements = []
        for opt in optimizations:
            dim = opt.get("dimension", "")
            suggestion = opt.get("suggestion", "")

            if "重建" in suggestion:
                improvements.append({
                    "type": "rebuild_agent",
                    "target": suggestion,
                    "action": "pause_and_rebuild",
                })
            elif "提升" in suggestion and "象限" in suggestion:
                improvements.append({
                    "type": "promote_quadrant",
                    "target": suggestion,
                    "action": "promote_to_strategic",
                })
            elif "拆分" in dim:
                improvements.append({
                    "type": "adjust_split_granularity",
                    "target": suggestion,
                    "action": "update_config",
                    "config_change": {"granularity": "fine"},
                })
            else:
                improvements.append({
                    "type": "general_optimization",
                    "target": suggestion,
                    "action": "log_for_review",
                })

        return improvements

    async def _validate_and_apply(self, improvement: dict) -> bool:
        """
        验证并应用改进方案。
        返回 True 表示成功应用。
        """
        action = improvement.get("action", "")
        target = improvement.get("target", "")

        try:
            if action == "pause_and_rebuild":
                # 标记为待处理，由 lifecycle manager 的评估循环处理
                self.redis.setex(
                    f"lifecycle:pending_rebuild:{target[:20]}",
                    3600,
                    json.dumps(improvement, ensure_ascii=False),
                )
                print(f"[Evolving] 已标记重建: {target}")
                return True

            elif action == "promote_to_strategic":
                print(f"[Evolving] 建议提升象限: {target}")
                return True

            elif action == "update_config":
                # 写入建议的新配置
                self.redis.hset(
                    "lifecycle:config_suggestions",
                    target[:50],
                    json.dumps(improvement.get("config_change", {})),
                )
                print(f"[Evolving] 已记录配置建议: {target}")
                return True

            else:
                # log_for_review: 仅记录
                print(f"[Evolving] 优化建议已记录: {target}")
                return False  # 未实际应用

        except Exception as e:
            print(f"[Evolving] 应用改进失败: {e}")
            return False

    def _get_agent_meta(self, agent_id: str) -> Optional[dict]:
        """获取 Agent 元数据。"""
        try:
            data = self.redis.hgetall(f"agent:metadata:{agent_id}")
            return {k.decode(): v.decode() for k, v in data.items()} if data else None
        except Exception:
            return None

    async def _llm_chat(self, prompt: str) -> str:
        """调用 LLM（自动适配 LLMAdapter / 旧接口）。"""
        return await _llm_chat_helper(self.llm, prompt)


# ═══════════════════════════════════════════════════════════════
# 共享辅助函数
# ═══════════════════════════════════════════════════════════════

async def _llm_chat_helper(llm_client, prompt: str) -> str:
    """
    统一的 LLM 调用辅助函数。
    自动适配 LLMAdapter / 旧 hasattr 接口。
    """
    if isinstance(llm_client, LLMAdapter):
        return await llm_client.chat(prompt)
    elif hasattr(llm_client, 'chat') and callable(llm_client.chat):
        result = llm_client.chat(prompt)
        if asyncio.iscoroutine(result):
            return await result
        return result
    elif hasattr(llm_client, 'completions'):
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: llm_client.chat.completions.create(
                model=getattr(llm_client, 'model', 'deepseek-v4-pro'),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                extra_body={"reasoning_effort": self.thinking} if self.thinking and self.thinking != "off" else None,
            ),
        )
        return resp.choices[0].message.content
    else:
        raise RuntimeError("LLM 客户端不可用或接口不兼容")


# ═══════════════════════════════════════════════════════════════
# 集成入口：build_commander_extensions()
# ═══════════════════════════════════════════════════════════════

async def build_commander_extensions(
    redis_client,
    store=None,
    llm_client=None,
    config: Optional[Dict] = None,
) -> dict:
    """
    一键构建所有 Commander 扩展模块。

    用法（在 commander.py 中）:
        extensions = await build_commander_extensions(redis, mongo, llm_client)
        safety = extensions["safety"]
        lifecycle = extensions["lifecycle"]
        designer = extensions["designer"]
        decomposer = extensions["decomposer"]
        evolver = extensions["evolver"]
    """
    # 1. 安全边界
    safety = SafetyBoundary(redis_client)

    # 2. 异步工厂
    factory = AsyncAgentFactory(redis_client, safety=safety)

    # 3. 生命周期管理器
    lifecycle = AgentLifecycleManagerV2(
        redis_client=redis_client,
        store=store,
        agent_factory=factory,
        safety=safety,
        llm_client=llm_client,
        config=config,
    )

    # 4. Agent 设计师
    designer = AgentDesigner(llm_client=llm_client)

    # 5. 任务拆解器
    decomposer = AutonomousTaskDecomposer(llm_client=llm_client)

    # 6. 自我进化模块
    evolver = SelfEvolvingCommander(
        redis_client=redis_client,
        store=store,
        llm_client=llm_client,
        lifecycle_manager=lifecycle,
    )

    # 预注册各象限角色
    roles_map = {
        AgentQuadrant.CORE.value:      ["翻译官", "商务经理", "售前经理"],
        AgentQuadrant.STRATEGIC.value: ["审计官", "数据迁移Agent"],
        AgentQuadrant.UTILITY.value:   ["心跳监控Agent", "日志清理Agent"],
        AgentQuadrant.EPHEMERAL.value: ["一次性数据导出Agent"],
    }
    for quadrant_val, roles in roles_map.items():
        redis_client.delete(f"lifecycle:roles:{quadrant_val}")
        if roles:
            redis_client.sadd(f"lifecycle:roles:{quadrant_val}", *roles)

    return {
        "safety": safety,
        "factory": factory,
        "lifecycle": lifecycle,
        "designer": designer,
        "decomposer": decomposer,
        "evolver": evolver,

        # 模块级导出（供 commander.py 使用）
        "AgentQuadrant": AgentQuadrant,
        "AgentStatus": AgentStatus,
        "LLMAdapter": LLMAdapter,
        "AsyncEventLoop": AsyncEventLoop,
        "HAS_PYPINYIN": HAS_PYPINYIN,
    }
