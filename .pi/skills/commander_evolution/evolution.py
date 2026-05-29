#!/usr/bin/env python3
"""
CommanderEvolution — GEPA 进化引擎的 Commander 集成
======================================================
将 GEPA 封装为 Commander 的技能，支持:
  - Agent 提示词自动进化
  - 调度策略参数优化
  - 翻译模板迭代改进
  - 审计规则自动调整

集成方式:
  1. 作为 Commander V3 的挂载模块
  2. 通过 Redis Pub/Sub 接收进化指令
  3. 结果写入 MongoDB + Redis

Constitution:
  R1 — 使用 commander:evolution:* 前缀
  R2 — 进化不覆盖当前运行配置，而是 A/B 对比后升级
"""

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .gepa_engine import GEPAEngine, Candidate, EvolutionReport, ParetoSelector

try:
    import redis as redis_lib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

try:
    from pymongo import MongoClient
    HAS_MONGO = True
except ImportError:
    HAS_MONGO = False


# ═══════════════════════════════════════════════════════════════
# EvolutionManager — Commander 进化管理器
# ═══════════════════════════════════════════════════════════════

class EvolutionManager:
    """
    Commander 的 GEPA 进化管理器。

    功能:
      - evolve_agent_prompt:    优化 Agent 提示词
      - evolve_routing_policy:  优化调度策略
      - evolve_translation:     优化翻译模板
      - evolve_audit_rules:     优化审计规则
      - get_evolution_history:  查询进化历史
    """

    def __init__(self,
                 redis_client=None,
                 mongo_client=None,
                 llm_client=None,
                 llm_model: str = "deepseek-chat",
                 redis_host: str = "127.0.0.1",
                 redis_port: int = 6379,
                 redis_password: str = None):
        self.redis = redis_client
        self.mongo = mongo_client
        self.llm = llm_client
        self.llm_model = llm_model

        # 如果没有传入 Redis 客户端，自动创建
        if self.redis is None and HAS_REDIS:
            try:
                self.redis = redis_lib.Redis(
                    host=redis_host, port=redis_port,
                    password=redis_password, decode_responses=True)
            except Exception:
                pass

        # 运行中的进化任务
        self.active_evolutions: Dict[str, EvolutionReport] = {}

    # ── 公共 API ──────────────────────────────────────────────

    def evolve_agent_prompt(self,
                            agent_id: str,
                            current_prompt: str,
                            trainset: List[Dict] = None,
                            valset: List[Dict] = None,
                            task_description: str = None,
                            max_generations: int = 5,
                            population_size: int = 4,
                            async_mode: bool = False) -> Dict[str, Any]:
        """
        进化指定 Agent 的 system_prompt。

        Args:
            agent_id: Agent 标识（如 "翻译官"）
            current_prompt: 当前提示词
            trainset: 训练集 [{input, expected_output, context}, ...]
            valset: 验证集（格式同训练集）
            task_description: 任务描述
            max_generations: 最大代数
            population_size: 种群大小
            async_mode: True=异步执行，返回 task_id 轮询

        Returns:
            {"evolution_id": "...", "best_prompt": "...", "score": ..., ...}
        """
        evolution_id = f"evo-{agent_id}-{uuid.uuid4().hex[:6]}"

        if not task_description:
            task_description = f"优化 {agent_id} 的系统提示词，提高任务完成质量和效率"

        # 构建评估器
        def evaluator(genes):
            """在验证集上评估新提示词。"""
            new_prompt = genes.get("system_prompt", current_prompt)
            results = []
            success = 0
            total_latency = 0

            for task in (valset or []):
                start = time.time()
                # 这里是模拟评估，实际应该调用 Agent 执行
                task_result = self._evaluate_on_task(new_prompt, task)
                latency = time.time() - start
                total_latency += latency
                if task_result.get("success"):
                    success += 1
                results.append(task_result)

            n = len(valset) if valset else 1
            accuracy = success / n if n > 0 else 0.5
            avg_latency = total_latency / n if n > 0 else 1.0

            return {
                "score": accuracy * 0.7 + max(0, (2.0 - avg_latency) / 2.0) * 0.3,
                "metrics": {
                    "accuracy": accuracy,
                    "avg_latency": avg_latency,
                    "success_count": success,
                },
                "trajectory": results,
            }

        if async_mode:
            # TODO: 异步执行
            self.active_evolutions[evolution_id] = {"status": "queued"}
            return {"evolution_id": evolution_id, "status": "queued"}

        # 同步执行
        engine = GEPAEngine(
            llm_client=self.llm,
            llm_model=self.llm_model,
            verbose=True,
        )

        report = engine.evolve(
            seed={"system_prompt": current_prompt},
            evaluator=evaluator,
            task_description=task_description,
            trainset=trainset or [],
            valset=valset or [],
            gene_keys=["system_prompt"],
            max_generations=max_generations,
            population_size=population_size,
            elite_size=max(1, population_size // 4),
        )

        # 记录到数据库
        self._save_evolution_log(agent_id, "prompt", report)

        return {
            "evolution_id": evolution_id,
            "agent_id": agent_id,
            "original_prompt": current_prompt,
            "best_prompt": report.best_candidate.genes.get("system_prompt", ""),
            "best_score": report.best_candidate.score,
            "improvement": report.improvement,
            "generations": report.generations,
            "total_evaluations": report.total_evaluations,
            "all_candidates": [c.to_dict() for c in report.all_candidates],
        }

    def evolve_routing_policy(self,
                              current_policy: Dict[str, Any],
                              performance_data: List[Dict],
                              max_generations: int = 8,
                              population_size: int = 6) -> Dict[str, Any]:
        """
        进化 Commander 的任务调度策略。

        Args:
            current_policy: 当前调度规则 {"翻译任务": "翻译官", "询价": "商务经理", ...}
            performance_data: 历史任务表现数据
        """
        evolution_id = f"evo-routing-{uuid.uuid4().hex[:6]}"

        def evaluator(genes):
            policy = genes.get("routing_policy", {})
            # 模拟：用新策略在历史数据上的效果
            correct = 0
            total = len(performance_data)
            for task in performance_data:
                task_type = task.get("type", "")
                assigned = policy.get(task_type)
                expected = task.get("expected_agent", "")
                if assigned == expected:
                    correct += 1

            accuracy = correct / total if total > 0 else 0.5

            return {
                "score": accuracy,
                "metrics": {"routing_accuracy": accuracy, "total_tasks": total},
                "trajectory": [],
            }

        engine = GEPAEngine(llm_client=self.llm, llm_model=self.llm_model, verbose=True)
        report = engine.evolve(
            seed={"routing_policy": current_policy},
            evaluator=evaluator,
            task_description="优化 Commander 的任务调度策略，最大化路由准确率",
            gene_keys=["routing_policy"],
            max_generations=max_generations,
            population_size=population_size,
            elite_size=2,
        )

        self._save_evolution_log("commander", "routing_policy", report)

        return {
            "evolution_id": evolution_id,
            "evolved_policy": report.best_candidate.genes.get("routing_policy", {}),
            "improvement": report.improvement,
            "generations": report.generations,
        }

    def evolve_translation_template(self,
                                    template_name: str,
                                    current_template: str,
                                    sample_pairs: List[Dict],
                                    source_lang: str = "zh-CN",
                                    target_lang: str = "en",
                                    max_generations: int = 4) -> Dict[str, Any]:
        """
        进化翻译模板。

        Args:
            template_name: 模板名称
            current_template: 当前翻译模板
            sample_pairs: 样本对 [{source, reference, context}, ...]
            source_lang: 源语言
            target_lang: 目标语言
        """
        evolution_id = f"evo-trans-{template_name}-{uuid.uuid4().hex[:4]}"

        def evaluator(genes):
            template = genes.get("template", current_template)
            scores = []
            for pair in sample_pairs:
                # 这里应调用翻译 API 评估，目前用模拟
                score = self._simulate_translation_score(template, pair)
                scores.append(score)
            avg = sum(scores) / len(scores) if scores else 0.5

            return {
                "score": avg,
                "metrics": {"avg_translation_quality": avg},
                "trajectory": [],
            }

        engine = GEPAEngine(llm_client=self.llm, llm_model=self.llm_model, verbose=True)
        report = engine.evolve(
            seed={"template": current_template},
            evaluator=evaluator,
            task_description=f"优化 {source_lang}→{target_lang} 翻译模板 '{template_name}'",
            gene_keys=["template"],
            max_generations=max_generations,
            population_size=4,
            elite_size=1,
        )

        self._save_evolution_log("translate-engine", f"template:{template_name}", report)

        return {
            "evolution_id": evolution_id,
            "template_name": template_name,
            "original": current_template,
            "evolved": report.best_candidate.genes.get("template", ""),
            "improvement": report.improvement,
        }

    def get_evolution_history(self, target: str = None,
                               limit: int = 20) -> List[Dict]:
        """查询进化历史记录。"""
        if not self.mongo or not HAS_MONGO:
            # 从 Redis 读取
            history = []
            if self.redis:
                keys = self.redis.keys("commander:evolution:log:*")
                for key in sorted(keys, reverse=True)[:limit]:
                    val = self.redis.get(key)
                    if val:
                        try:
                            history.append(json.loads(val))
                        except Exception:
                            pass
            return history

        try:
            collection = self.mongo["lightingmetal"]["agent_evolution_log"]
            query = {"target": target} if target else {}
            cursor = collection.find(query).sort("timestamp", -1).limit(limit)
            return list(cursor)
        except Exception:
            return []

    def get_best_prompts(self) -> Dict[str, str]:
        """获取所有 Agent 当前最优提示词。"""
        best_prompts = {}
        if self.redis:
            keys = self.redis.keys("commander:evolution:best:*")
            for key in keys:
                agent = key.rsplit(":", 1)[-1]
                val = self.redis.get(key)
                if val:
                    best_prompts[agent] = val
        return best_prompts

    # ── 内部方法 ──────────────────────────────────────────────

    def _evaluate_on_task(self, prompt: str, task: Dict) -> Dict:
        """在单个任务上评估（可替换为实际 Agent 调用）。"""
        # 模拟评估逻辑
        return {
            "task_id": task.get("id", uuid.uuid4().hex[:6]),
            "success": True,
            "latency": 0.5,
        }

    def _simulate_translation_score(self, template: str, pair: Dict) -> float:
        """模拟翻译质量评分。"""
        return 0.7 + 0.3 * (len(template) / max(len(template) + 100, 1))

    def _save_evolution_log(self, target: str, evo_type: str, report: EvolutionReport):
        """保存进化记录到 Redis + MongoDB。"""
        log_entry = {
            "target": target,
            "type": evo_type,
            "evolution_id": report.evolution_id,
            "best_score": report.best_candidate.score,
            "improvement": report.improvement,
            "generations": report.generations,
            "candidates": [c.to_dict() for c in report.all_candidates],
            "timestamp": datetime.now().isoformat(),
        }

        # Redis
        if self.redis:
            try:
                key = f"commander:evolution:log:{report.evolution_id}"
                self.redis.setex(key, 86400 * 30, json.dumps(log_entry, ensure_ascii=False))

                # 保存最优提示词
                best_prompt = report.best_candidate.genes.get("system_prompt", "")
                if best_prompt:
                    self.redis.set(
                        f"commander:evolution:best:{target}",
                        best_prompt,
                    )
            except Exception:
                pass

        # MongoDB
        if self.mongo and HAS_MONGO:
            try:
                collection = self.mongo["lightingmetal"]["agent_evolution_log"]
                collection.insert_one(log_entry)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════
# 独立运行入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("CommanderEvolution — GEPA 进化引擎 v1.0")
    print()

    # 测试：无 LLM 的简单进化
    mgr = EvolutionManager()

    result = mgr.evolve_agent_prompt(
        agent_id="翻译官",
        current_prompt="你是一个专业的五金外贸翻译助手，请将中文翻译成英文，保持术语一致。",
        task_description="优化翻译提示词，提高翻译准确率和专业度",
        max_generations=2,
        population_size=3,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
