#!/usr/bin/env python3
"""
Prompt Optimizer — 基于 GEPA 算法的 Agent 提示词自动优化器
=============================================================
GEPA = Generate → Evaluate → Pick → Apply

触发条件:
  1. Agent 错误率 > 10% (最近50次任务)
  2. Agent 累计运行 ≥ 50 次任务
  3. 手动触发

Constitution R1: commander:* 前缀
            R4: 原始 prompt 永远保留在 commander:prompt:original:{agent}
"""

import copy
import json
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# dataclasses 兼容（Python 3.6 backport）
try:
    from dataclasses import dataclass, field
except ImportError:
    def dataclass(cls=None, **kwargs):
        def wrap(c):
            orig_init = c.__init__
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
                if orig_init is not object.__init__:
                    orig_init(self, **kw)
            c.__init__ = __init__
            return c
        return wrap(cls) if cls else wrap
    def field(**kwargs):
        return kwargs

# ── 可选依赖 ──
HAS_REDIS = False
try:
    import redis  # type: ignore
    HAS_REDIS = True
except ImportError:
    pass

HAS_OPENAI = False
try:
    from openai import OpenAI as OpenAIClient
    HAS_OPENAI = True
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════
# LLM 客户端封装
# ═══════════════════════════════════════════════════════════════

def get_llm_client(api_key: str = "", base_url: str = "") -> Optional[Any]:
    """获取 LLM 客户端。"""
    if not HAS_OPENAI:
        return None
    key = api_key or os.environ.get("LLM_API_KEY", "")
    url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    if not key:
        return None
    return OpenAIClient(api_key=key, base_url=url)


# ═══════════════════════════════════════════════════════════════
# GEPA 提示词分析模板
# ═══════════════════════════════════════════════════════════════

GEPA_ANALYSIS_PROMPT = """你是 Prompt 优化专家。分析以下 Agent 的执行轨迹，找出当前 prompt 的弱点并生成优化版本。

## Agent 信息
- 名称: {agent_name}
- 角色: {agent_role}
- 目标: {agent_goal}

## 当前 Prompt
```
{current_prompt}
```

## 最近 {trace_count} 次任务执行轨迹
{execution_traces}

## 分析要求
1. 找出当前 prompt 的 3-5 个具体弱点（歧义、缺少约束、格式不一致、信息冗余等）
2. 为每个弱点生成针对性改进
3. 生成 {candidate_count} 个优化后的 prompt 候选版本

## 输出格式（仅输出 JSON，不要其他内容）
{{
  "analysis": {{
    "weaknesses": [
      {{"issue": "问题描述", "severity": "high|medium|low", "evidence": "来自轨迹的证据"}}
    ],
    "root_cause": "根因总结"
  }},
  "candidates": [
    {{
      "version": 1,
      "prompt": "优化后的完整 prompt 文本",
      "improvements": ["改进点1", "改进点2"],
      "expected_impact": {{"success_rate": 0.0, "format_compliance": 0.0}},
      "risk": "low|medium|high"
    }}
  ]
}}
"""


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class ExecutionTrace:
    """单次任务执行轨迹。"""
    task_id: str
    agent_id: str
    task_description: str
    status: str         # "success" | "fail"
    score: float = 0.0  # 0-100
    format_compliant: bool = True
    duration_ms: int = 0
    error_message: str = ""
    timestamp: str = ""

    @classmethod
    def from_dict(cls, d: Dict) -> "ExecutionTrace":
        return cls(
            task_id=d.get("task_id", ""),
            agent_id=d.get("agent_id", ""),
            task_description=d.get("task_description", d.get("task", "")),
            status=d.get("status", "unknown"),
            score=float(d.get("score", 0)),
            format_compliant=d.get("format_compliant", True),
            duration_ms=int(d.get("duration_ms", d.get("duration", 0))),
            error_message=d.get("error_message", d.get("error", "")),
            timestamp=str(d.get("timestamp", "")),
        )


@dataclass
class OptimizerConfig:
    """优化器配置。"""
    error_rate_threshold: float = 0.10      # 10% 错误率触发
    min_tasks_for_check: int = 50           # 累计50次触发
    check_interval: int = 10                # 每10次任务检查一次
    candidate_count: int = 3                # 生成 2-5 个候选
    ab_test_traffic_control: float = 0.80   # 对照组流量
    ab_test_duration_hours: int = 24        # A/B测试时长
    ab_test_variant_traffic: float = 0.10   # 每组变体流量


@dataclass
class OptimizeResult:
    """单次优化结果。"""
    agent_id: str
    triggered_by: str       # "error_rate" | "task_count" | "manual"
    generated_at: str
    candidates: List[Dict]
    winner_version: Optional[int] = None
    winner_prompt: str = ""
    applied: bool = False
    test_id: str = ""
    metrics_before: Dict = field(default_factory=dict)
    metrics_after: Dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# Prompt 优化器核心
# ═══════════════════════════════════════════════════════════════

class PromptOptimizer:
    """
    GEPA Prompt 优化器。

    用法:
        opt = PromptOptimizer()
        # 检查是否需要优化
        if opt.should_optimize("商务经理"):
            result = opt.optimize("商务经理")
            if result.applied:
                print(f"Prompt 已更新 → version {result.winner_version}")
    """

    KEY_STATS = "commander:prompt:stats:{agent}"
    KEY_BACKUP = "commander:prompt:backup:{agent}:{version}"
    KEY_ORIGINAL = "commander:prompt:original:{agent}"
    KEY_OPT_LOG = "commander:prompt:opt_log"

    def __init__(
        self,
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_password: str = "",
        config: OptimizerConfig = None,
    ):
        self.config = config or OptimizerConfig()
        self._redis = None
        if HAS_REDIS:
            try:
                self._redis = redis.Redis(
                    host=redis_host, port=redis_port,
                    password=redis_password, decode_responses=True,
                )
                self._redis.ping()
            except Exception:
                self._redis = None

        self._llm = get_llm_client()
        # 内存 fallback（无 Redis 时用）
        self._mem_stats: Dict[str, Dict] = {}
        self._mem_traces: Dict[str, List[Dict]] = {}

    # ── Public API ──

    def should_optimize(self, agent_id: str, force: bool = False) -> bool:
        """判断 Agent 是否需要 prompt 优化。

        Args:
            agent_id: Agent 名称
            force: 强制执行（手动触发）

        Returns:
            是否需要优化。
        """
        if force:
            return True

        traces = self._get_recent_traces(agent_id, self.config.min_tasks_for_check)
        if len(traces) < self.config.check_interval:
            return False

        # 检查累计任务数
        stats = self._get_agent_stats(agent_id)
        total_tasks = stats.get("total_tasks", 0)
        last_check = stats.get("last_optimize_check", 0)

        # 距离上次检查不够间隔 → 跳过
        if total_tasks - last_check < self.config.check_interval:
            return False

        # 距离上次优化足够远（≥ min_tasks_for_check）才触发
        if total_tasks - last_check >= self.config.min_tasks_for_check:
            return True

        # 错误率 > threshold (只看上次优化之后的新任务)
        new_task_count = total_tasks - last_check
        sample = traces[-max(new_task_count, self.config.check_interval):]
        if len(sample) >= self.config.min_tasks_for_check:
            failures = sum(1 for t in sample if t.status == "fail")
            error_rate = failures / len(sample)
            if error_rate > self.config.error_rate_threshold:
                return True

        return False

    def optimize(self, agent_id: str) -> OptimizeResult:
        """执行 GEPA 优化流程。

        Args:
            agent_id: Agent 名称

        Returns:
            OptimizeResult。
        """
        # 获取当前 prompt
        current_prompt = self._get_current_prompt(agent_id)
        agent_meta = self._get_agent_meta(agent_id)

        # ── 判断触发原因 ──
        traces = self._get_recent_traces(agent_id, self.config.min_tasks_for_check)
        stats = self._get_agent_stats(agent_id)

        if stats.get("total_tasks", 0) >= self.config.min_tasks_for_check:
            trigger = "task_count"
        elif traces:
            failures = sum(1 for t in traces[-50:] if t.status == "fail")
            if failures / max(len(traces[-50:]), 1) > self.config.error_rate_threshold:
                trigger = "error_rate"
            else:
                trigger = "manual"
        else:
            trigger = "manual"

        result = OptimizeResult(
            agent_id=agent_id,
            triggered_by=trigger,
            generated_at=datetime.now().isoformat(),
            candidates=[],
        )

        # ── [G] Generate: LLM 生成候选 ──
        candidates = self._generate_candidates(
            agent_name=agent_id,
            agent_role=agent_meta.get("role", agent_id),
            agent_goal=agent_meta.get("prompt", ""),
            current_prompt=current_prompt,
            traces=traces[-50:],
        )
        result.candidates = candidates

        if not candidates:
            return result

        # ── [E] Evaluate: 离线评估 ──
        evaluated = self._evaluate_candidates(candidates, traces[-20:])
        # 按评分排序
        evaluated.sort(key=lambda c: c.get("eval_score", 0), reverse=True)
        top2 = evaluated[:2]

        # ── [P] Pick: 创建 A/B 测试 ──
        test_id = f"prompt-opt-{agent_id}-{int(time.time())}"
        ab_config = {
            "test_id": test_id,
            "agent_id": agent_id,
            "control": {"prompt": current_prompt, "traffic_share": self.config.ab_test_traffic_control},
            "variants": [
                {"prompt": c["prompt"], "traffic_share": self.config.ab_test_variant_traffic}
                for c in top2
            ],
            "duration_hours": self.config.ab_test_duration_hours,
            "created_at": datetime.now().isoformat(),
            "status": "running",
        }

        self._save_ab_test(test_id, ab_config)

        # ── [A] Apply: 备份原始 → 发布到 A/B ──
        self._backup_prompt(agent_id, current_prompt, "original")

        # 更新 Agent 元数据（prompt 保持不变，等 A/B 测试结束后自动选优）
        # A/B 测试结果由 Commander.ABTester 监控
        self._update_stats(agent_id, "last_optimize_check", stats.get("total_tasks", 0))

        # 记录优化日志
        self._log_optimization(result, ab_config)

        result.test_id = test_id
        result.metrics_before = self._calc_metrics(traces[-50:])

        return result

    def apply_winner(self, test_id: str) -> bool:
        """A/B 测试结束后应用获胜 prompt。

        Args:
            test_id: A/B 测试 ID

        Returns:
            是否成功应用。
        """
        test = self._get_ab_test(test_id)
        if not test or test.get("status") != "completed":
            return False

        winner = test.get("winner")
        if not winner:
            return False

        agent_id = test["agent_id"]
        version = int(time.time())

        # 备份当前 prompt
        current = self._get_current_prompt(agent_id)
        self._backup_prompt(agent_id, current, str(version))

        # 应用获胜 prompt
        self._set_prompt(agent_id, winner["prompt"])
        test["status"] = "applied"
        test["applied_at"] = datetime.now().isoformat()
        self._save_ab_test(test_id, test)

        # 更新日志
        log_entry = {
            "agent_id": agent_id,
            "action": "apply_winner",
            "version": version,
            "test_id": test_id,
            "timestamp": datetime.now().isoformat(),
        }
        self._append_opt_log(log_entry)

        return True

    def get_optimization_history(self, agent_id: str, limit: int = 10) -> List[Dict]:
        """获取 Agent 的优化历史。"""
        logs = self._get_opt_logs()
        agent_logs = [l for l in logs if l.get("agent_id") == agent_id]
        return agent_logs[-limit:]

    def rollback(self, agent_id: str, version: str = "original") -> bool:
        """回滚到指定版本的 prompt。

        Args:
            agent_id: Agent 名称
            version: 版本号（"original" 为原始版本）

        Returns:
            是否成功回滚。
        """
        key = self.KEY_BACKUP.format(agent=agent_id, version=version)
        backup = self._redis_get(key) if self._redis else None
        if not backup:
            # 尝试从原始备份恢复
            orig_key = self.KEY_ORIGINAL.format(agent=agent_id)
            backup = self._redis_get(orig_key) if self._redis else None

        if not backup:
            return False

        if isinstance(backup, str):
            backup = json.loads(backup)

        prompt_text = backup.get("prompt", "") if isinstance(backup, dict) else str(backup)
        self._set_prompt(agent_id, prompt_text)

        self._append_opt_log({
            "agent_id": agent_id,
            "action": "rollback",
            "version": version,
            "timestamp": datetime.now().isoformat(),
        })

        return True

    # ── [G] Generate ──

    def _generate_candidates(
        self, agent_name: str, agent_role: str, agent_goal: str,
        current_prompt: str, traces: List[ExecutionTrace],
    ) -> List[Dict]:
        """LLM 生成优化候选。"""
        if not self._llm:
            return self._mock_candidates(agent_name, current_prompt)

        trace_text = "\n".join([
            f"{i+1}. [{t.status.upper()}] {t.task_description[:120]} "
            f"(score={t.score}, compliant={t.format_compliant})"
            + (f", error={t.error_message[:80]}" if t.error_message else "")
            for i, t in enumerate(traces)
        ])

        prompt = GEPA_ANALYSIS_PROMPT.format(
            agent_name=agent_name,
            agent_role=agent_role,
            agent_goal=agent_goal,
            current_prompt=current_prompt,
            trace_count=len(traces),
            execution_traces=trace_text,
            candidate_count=self.config.candidate_count,
        )

        try:
            response = self._llm.chat.completions.create(
                model=os.environ.get("LLM_MODEL", "deepseek-chat"),
                messages=[
                    {"role": "system", "content": "你是 Prompt 优化专家。仅输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=4000,
            )
            raw = response.choices[0].message.content

            # 提取 JSON
            json_start = raw.find("{")
            json_end = raw.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(raw[json_start:json_end])
                return data.get("candidates", [])

        except Exception as e:
            print(f"[PromptOpt] LLM 生成候选失败: {e}")

        return self._mock_candidates(agent_name, current_prompt)

    # ── [E] Evaluate ──

    def _evaluate_candidates(
        self, candidates: List[Dict], traces: List[ExecutionTrace],
    ) -> List[Dict]:
        """离线评估候选 prompt（基于历史数据模拟）。"""
        for c in candidates:
            score = 0.0
            prompt = c.get("prompt", "")
            # 评分因子
            # 1. 清晰度: prompt 长度在合理范围 (200-2000 chars)
            p_len = len(prompt)
            if 200 <= p_len <= 2000:
                score += 30
            elif p_len < 200:
                score += 15
            else:
                score += 20

            # 2. 结构: 包含明确的分段/指令/约束
            sections = sum(1 for line in prompt.split("\n") if line.startswith("#") or line.startswith("- "))
            score += min(sections * 5, 20)

            # 3. 具体性: 包含具体的关键词（如"JSON格式""不要省略""逐步"等）
            specificity_keywords = [
                "JSON", "格式", "不要", "禁止", "必须", "请确保",
                "步骤", "检查", "确认", "列出", "must", "ensure",
                "format", "validate", "verify",
            ]
            specificity = sum(1 for kw in specificity_keywords if kw.lower() in prompt.lower())
            score += min(specificity * 5, 25)

            # 4. 改进点数量
            improvements = c.get("improvements", [])
            score += min(len(improvements) * 5, 15)

            # 5. 风险惩罚
            risk = c.get("risk", "medium")
            if risk == "low":
                score += 10
            elif risk == "high":
                score -= 10

            c["eval_score"] = round(min(score, 100), 1)

        return candidates

    # ── Helpers ──

    def _get_recent_traces(self, agent_id: str, limit: int = 50) -> List[ExecutionTrace]:
        """从 Redis 或内存获取最近的任务执行轨迹。"""
        traces = []
        if self._redis:
            try:
                raw = self._redis.lrange(
                    f"commander:task:traces:{agent_id}", -limit, -1
                )
                for r in raw:
                    try:
                        traces.append(ExecutionTrace.from_dict(json.loads(r)))
                    except Exception:
                        pass
            except Exception:
                pass
        elif hasattr(self, '_mem_traces') and agent_id in self._mem_traces:
            for d in self._mem_traces[agent_id][-limit:]:
                traces.append(ExecutionTrace.from_dict(d))
        return traces

    def _get_agent_stats(self, agent_id: str) -> Dict:
        """获取 Agent 统计（Redis 或内存）。"""
        if self._redis:
            try:
                raw = self._redis.get(self.KEY_STATS.format(agent=agent_id))
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        if hasattr(self, '_mem_stats') and agent_id in self._mem_stats:
            return self._mem_stats[agent_id]
        return {"total_tasks": 0, "success": 0, "fail": 0, "last_optimize_check": 0}

    def _get_current_prompt(self, agent_id: str) -> str:
        """获取 Agent 当前的 prompt。"""
        if self._redis:
            try:
                raw = self._redis.hget("commander:agents:registry", agent_id)
                if raw:
                    meta = json.loads(raw)
                    return meta.get("prompt", "")
            except Exception:
                pass
        return ""

    def _get_agent_meta(self, agent_id: str) -> Dict:
        """获取 Agent 元数据。"""
        if self._redis:
            try:
                raw = self._redis.hget("commander:agents:registry", agent_id)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        return {}

    def _backup_prompt(self, agent_id: str, prompt: str, version: str):
        """备份 prompt。"""
        if not self._redis:
            return
        key = self.KEY_BACKUP.format(agent=agent_id, version=version)
        backup = {"prompt": prompt, "version": version, "timestamp": datetime.now().isoformat()}
        self._redis.set(key, json.dumps(backup, ensure_ascii=False))

        # 原始版本 → 单独 key
        if version == "original":
            self._redis.set(
                self.KEY_ORIGINAL.format(agent=agent_id),
                json.dumps(backup, ensure_ascii=False),
            )

    def _set_prompt(self, agent_id: str, prompt: str):
        """更新 Agent registry 中的 prompt。"""
        if not self._redis:
            return
        try:
            raw = self._redis.hget("commander:agents:registry", agent_id)
            if raw:
                meta = json.loads(raw)
                meta["prompt"] = prompt
                meta["updated"] = datetime.now().isoformat()
                self._redis.hset("commander:agents:registry", agent_id, json.dumps(meta, ensure_ascii=False))
        except Exception:
            pass

    def _save_ab_test(self, test_id: str, config: Dict):
        """保存 A/B 测试配置。"""
        if self._redis:
            self._redis.set(f"commander:prompt:ab:{test_id}", json.dumps(config, ensure_ascii=False))

    def _get_ab_test(self, test_id: str) -> Optional[Dict]:
        """获取 A/B 测试配置。"""
        if self._redis:
            raw = self._redis.get(f"commander:prompt:ab:{test_id}")
            if raw:
                return json.loads(raw)
        return None

    def _update_stats(self, agent_id: str, key: str, value: Any):
        """更新 Agent 统计（Redis 或内存）。"""
        stats = self._get_agent_stats(agent_id)
        stats[key] = value
        if self._redis:
            self._redis.set(
                self.KEY_STATS.format(agent=agent_id),
                json.dumps(stats, ensure_ascii=False),
            )
        else:
            if not hasattr(self, '_mem_stats'):
                self._mem_stats = {}
            self._mem_stats[agent_id] = stats

    def _log_optimization(self, result: OptimizeResult, ab_config: Dict):
        """记录优化日志。"""
        entry = {
            "agent_id": result.agent_id,
            "triggered_by": result.triggered_by,
            "candidates_count": len(result.candidates),
            "test_id": result.test_id,
            "ab_config": ab_config,
            "timestamp": result.generated_at,
            "metrics_before": result.metrics_before,
        }
        self._append_opt_log(entry)

    def _append_opt_log(self, entry: Dict):
        """追加优化日志到 Redis。"""
        if self._redis:
            self._redis.rpush(
                self.KEY_OPT_LOG,
                json.dumps(entry, ensure_ascii=False),
            )

    def _get_opt_logs(self) -> List[Dict]:
        """获取所有优化日志。"""
        if self._redis:
            try:
                raw = self._redis.lrange(self.KEY_OPT_LOG, 0, -1)
                return [json.loads(r) for r in raw]
            except Exception:
                pass
        return []

    def _calc_metrics(self, traces: List[ExecutionTrace]) -> Dict:
        """从轨迹计算 Agent 指标。"""
        if not traces:
            return {}
        total = len(traces)
        successes = sum(1 for t in traces if t.status == "success")
        compliant = sum(1 for t in traces if t.format_compliant)
        scores = [t.score for t in traces if t.score > 0]
        durations = [t.duration_ms for t in traces if t.duration_ms > 0]
        return {
            "total": total,
            "success_rate": round(successes / total, 3),
            "format_compliance": round(compliant / total, 3) if total else 0,
            "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
            "avg_duration_ms": round(sum(durations) / len(durations), 0) if durations else 0,
        }

    def _redis_get(self, key: str) -> Optional[Any]:
        if self._redis:
            try:
                return self._redis.get(key)
            except Exception:
                pass
        return None

    def _mock_candidates(self, agent_name: str, current_prompt: str) -> List[Dict]:
        """LLM 不可用时生成模拟候选。"""
        base_prompt = current_prompt or f"你是LightingMetal的{agent_name}，负责完成指定的任务。"
        return [
            {
                "version": 1,
                "prompt": base_prompt + "\n\n## 补充: 请以 JSON 格式输出，不要包含额外解释。",
                "improvements": ["增加 JSON 格式强制约束"],
                "expected_impact": {"success_rate": 0.85, "format_compliance": 0.95},
                "risk": "low",
                "eval_score": 85.0,
            },
            {
                "version": 2,
                "prompt": base_prompt + "\n\n## 输出规范\n1. 先给出分析，再给出结论\n2. 所有数据标注来源\n3. 不确定时标注 [待确认]",
                "improvements": ["增加输出规范章节", "增加不确定性标注要求"],
                "expected_impact": {"success_rate": 0.82, "format_compliance": 0.90},
                "risk": "low",
                "eval_score": 78.0,
            },
            {
                "version": 3,
                "prompt": "作为一名专业专家，" + (base_prompt[0].lower() + base_prompt[1:] if base_prompt else base_prompt) + "\n\n请严格按照以下步骤执行：\n1. 分析输入\n2. 列出可选方案\n3. 选择最优方案\n4. 输出结果",
                "improvements": ["增加专业角色身份", "增加步骤化指令"],
                "expected_impact": {"success_rate": 0.80, "format_compliance": 0.88},
                "risk": "medium",
                "eval_score": 72.0,
            },
        ]


# ═══════════════════════════════════════════════════════════════
# Commander 集成入口
# ═══════════════════════════════════════════════════════════════

class PromptOptimizerGuard:
    """
    Prompt Optimizer Guard — Commander 集成层。

    在每次任务完成后自动检查是否需要优化。

    用法:
        guard = PromptOptimizerGuard()
        # 在 Commander 的任务完成回调中:
        guard.after_task("商务经理", success=True)
    """

    def __init__(self, optimizer: PromptOptimizer = None):
        self.optimizer = optimizer or PromptOptimizer()

    def after_task(self, agent_id: str, success: bool, score: float = 0,
                   format_compliant: bool = True, task_description: str = "",
                   duration_ms: int = 0, error_message: str = ""):
        """任务完成后调用，记录轨迹并检查优化。

        Args:
            agent_id: Agent 名称
            success: 是否成功
            score: 质量评分 (0-100)
            format_compliant: 格式是否合规
            task_description: 任务描述
            duration_ms: 耗时
            error_message: 错误信息
        """
        # 原子更新统计（读→改→写在同一方法内完成）
        stats = self.optimizer._get_agent_stats(agent_id)
        stats["total_tasks"] = stats.get("total_tasks", 0) + 1
        if success:
            stats["success"] = stats.get("success", 0) + 1
        else:
            stats["fail"] = stats.get("fail", 0) + 1
        # 一次性全量写回，保证原子性
        if self.optimizer._redis:
            self.optimizer._redis.set(
                self.optimizer.KEY_STATS.format(agent=agent_id),
                json.dumps(stats, ensure_ascii=False),
            )
        else:
            self.optimizer._mem_stats[agent_id] = stats

        # 记录轨迹
        trace = ExecutionTrace(
            task_id=f"trace-{int(time.time() * 1000)}",
            agent_id=agent_id,
            task_description=task_description,
            status="success" if success else "fail",
            score=score,
            format_compliant=format_compliant,
            duration_ms=duration_ms,
            error_message=error_message,
            timestamp=datetime.now().isoformat(),
        )

        if self.optimizer._redis:
            self.optimizer._redis.rpush(
                f"commander:task:traces:{agent_id}",
                json.dumps(trace.__dict__, ensure_ascii=False),
            )
            # 限制 500 条
            self.optimizer._redis.ltrim(f"commander:task:traces:{agent_id}", -500, -1)
        else:
            # 内存 fallback
            if not hasattr(self.optimizer, '_mem_traces'):
                self.optimizer._mem_traces = {}
            if agent_id not in self.optimizer._mem_traces:
                self.optimizer._mem_traces[agent_id] = []
            self.optimizer._mem_traces[agent_id].append(trace.__dict__)
            if len(self.optimizer._mem_traces[agent_id]) > 500:
                self.optimizer._mem_traces[agent_id] = self.optimizer._mem_traces[agent_id][-500:]

        # 检查是否需要优化
        if self.optimizer.should_optimize(agent_id):
            result = self.optimizer.optimize(agent_id)
            print(f"[PromptOpt] {agent_id}: GEPA优化已触发 ({result.triggered_by}), "
                  f"生成 {len(result.candidates)} 个候选, A/B测试: {result.test_id}")

    def manual_optimize(self, agent_id: str) -> OptimizeResult:
        """手动触发优化。"""
        return self.optimizer.optimize(agent_id)

    def get_status(self, agent_id: str) -> Dict:
        """获取 Agent 的优化状态。"""
        stats = self.optimizer._get_agent_stats(agent_id)
        trails = self.optimizer._get_recent_traces(agent_id, 50)
        metrics = self.optimizer._calc_metrics(trails)
        history = self.optimizer.get_optimization_history(agent_id, 3)
        return {
            "agent_id": agent_id,
            "stats": stats,
            "metrics": metrics,
            "recent_opt_count": len(history),
            "last_opt": history[-1] if history else None,
            "needs_optimize": self.optimizer.should_optimize(agent_id),
        }


# ═══════════════════════════════════════════════════════════════
# 测试入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    opt = PromptOptimizer()

    # 模拟注入轨迹
    guard = PromptOptimizerGuard(opt)

    sample_tasks = [
        ("生成沙特光伏项目报价方案", True, 85, True),
        ("分析客户需求并推荐产品", True, 78, True),
        ("翻译俄语产品规格书", False, 45, True, "格式错误: 缺少 JSON 输出"),
        ("对比三种地桩防腐工艺", True, 90, True),
        ("回复客户技术咨询邮件", False, 30, False, "内容不完整"),
    ]

    for i in range(10):
        for task_desc, success, score, compliant, *err in sample_tasks:
            err_msg = err[0] if err else ""
            guard.after_task(
                "商务经理",
                success=success,
                score=score,
                format_compliant=compliant,
                task_description=task_desc,
                error_message=err_msg,
            )

    status = guard.get_status("商务经理")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    print(f"\n需要优化: {status['needs_optimize']}")
