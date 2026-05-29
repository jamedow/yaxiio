#!/usr/bin/env python3
"""
GEPA (Genetic-Pareto Evolution Algorithm) — 进化引擎核心
==========================================================
让 LLM 像严谨的科学家一样，通过四步循环自动优化文本参数:

  1. 变异 (Mutate)   — 基于当前最佳方案，生成多个新候选
  2. 评估 (Evaluate) — 在验证集上测试，用具体指标打分
  3. 反思 (Reflect)   — LLM 分析失败轨迹，提出改进建议
  4. 选择 (Select)    — 根据 Pareto 前沿，保留最优解

核心类:
  - GEPAEngine       : 进化引擎主控
  - Candidate        : 候选方案（基因+得分+轨迹）
  - Population       : 种群管理
  - ParetoSelector   : 多目标 Pareto 选择器

Constitution:
  R1 — 不删 page:* / lightingmetal:* 前缀 key
  R2 — 进化日志写入 commander:evolution:*
"""

import copy
import json
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class Candidate:
    """进化种群中的一个候选个体。"""
    id: str
    genes: Dict[str, Any]          # 可优化的参数（如 {"system_prompt": "...", "temperature": 0.7}）
    score: float = 0.0             # 综合得分
    metrics: Dict[str, float] = field(default_factory=dict)  # 多维度指标
    generation: int = 0
    parent_id: Optional[str] = None
    mutation_strategy: Optional[str] = None
    trajectory: List[Dict] = field(default_factory=list)  # 执行轨迹
    reflection: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "genes": self.genes,
            "score": self.score,
            "metrics": self.metrics,
            "generation": self.generation,
            "parent_id": self.parent_id,
            "mutation_strategy": self.mutation_strategy,
            "trajectory_count": len(self.trajectory),
            "reflection": self.reflection[:200] if self.reflection else None,
        }


@dataclass 
class EvolutionReport:
    """进化过程报告。"""
    evolution_id: str
    seed: Candidate
    best_candidate: Candidate
    all_candidates: List[Candidate] = field(default_factory=list)
    generations: int = 0
    total_evaluations: int = 0
    improvement: float = 0.0
    pareto_frontier: List[Candidate] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> dict:
        return {
            "evolution_id": self.evolution_id,
            "best_score": self.best_candidate.score,
            "best_genes": self.best_candidate.genes,
            "improvement": self.improvement,
            "generations": self.generations,
            "total_evaluations": self.total_evaluations,
            "pareto_size": len(self.pareto_frontier),
            "candidates": [c.to_dict() for c in self.all_candidates],
        }


# ═══════════════════════════════════════════════════════════════
# 变异策略库
# ═══════════════════════════════════════════════════════════════

MUTATION_STRATEGIES = [
    "简化措辞，使其更直接、更少冗余",
    "增加具体示例和边界条件说明",
    "调整语气，使其更专业/更友好",
    "重新组织逻辑结构，先总后分",
    "添加反例和常见错误提示",
    "压缩长度，只保留核心指令",
    "增加约束条件和验收标准",
    "引入思维链步骤（step-by-step）",
    "强调角色定位和输出格式",
    "添加领域术语和上下文背景",
]


# ═══════════════════════════════════════════════════════════════
# Pareto 选择器
# ═══════════════════════════════════════════════════════════════

class ParetoSelector:
    """多目标 Pareto 前沿选择器。
    
    当有多个评估维度时（如 质量+速度+成本），使用 Pareto 支配关系
    选出非支配解作为精英保留。
    """

    @staticmethod
    def dominates(a: Candidate, b: Candidate, metric_keys: List[str]) -> bool:
        """检查 a 是否 Pareto 支配 b（所有维度不差，至少一个维度更好）。"""
        at_least_one_better = False
        for key in metric_keys:
            va = a.metrics.get(key, 0)
            vb = b.metrics.get(key, 0)
            if va < vb:
                return False
            if va > vb:
                at_least_one_better = True
        return at_least_one_better

    @classmethod
    def pareto_frontier(cls, candidates: List[Candidate], metric_keys: List[str]) -> List[Candidate]:
        """计算 Pareto 前沿（非支配解集合）。"""
        frontier = []
        for c in candidates:
            dominated = False
            for other in candidates:
                if other is c:
                    continue
                if cls.dominates(other, c, metric_keys):
                    dominated = True
                    break
            if not dominated:
                frontier.append(c)
        return frontier

    @classmethod
    def select(cls, population: List[Candidate], elite_size: int,
               metric_keys: List[str], diversity_weight: float = 0.1) -> List[Candidate]:
        """选出下一代种群：精英（Pareto前沿）+ 多样性补充。"""
        if len(population) <= elite_size:
            return list(population)

        # 1. 按综合得分排序
        sorted_pop = sorted(population, key=lambda c: c.score, reverse=True)
        selected = sorted_pop[:elite_size]

        # 2. 从剩余中按多样性补充
        remaining = sorted_pop[elite_size:]
        selection_pool = []

        for c in remaining:
            # 计算与已选候选的基因相似度
            max_similarity = 0.0
            for sel in selected:
                sim = cls._gene_similarity(c.genes, sel.genes)
                max_similarity = max(max_similarity, sim)

            # 多样性得分：得分高 + 基因不同
            diversity_score = c.score * (1 - diversity_weight * max_similarity)
            selection_pool.append((diversity_score, c))

        # 选多样性得分最高的
        selection_pool.sort(key=lambda x: x[0], reverse=True)
        for _, c in selection_pool:
            if len(selected) >= len(population):
                break
            selected.append(c)

        return selected

    @staticmethod
    def _gene_similarity(genes_a: dict, genes_b: dict) -> float:
        """计算两组基因的相似度（基于文本字段的 Jaccard 近似）。"""
        if not genes_a or not genes_b:
            return 0.0

        common = 0
        total = 0
        for key in set(list(genes_a.keys()) + list(genes_b.keys())):
            va = str(genes_a.get(key, ""))
            vb = str(genes_b.get(key, ""))
            if not va and not vb:
                continue
            total += 1
            # 简单字符集重叠
            set_a = set(va.split())
            set_b = set(vb.split())
            if set_a and set_b:
                overlap = len(set_a & set_b) / len(set_a | set_b)
                if overlap > 0.3:
                    common += 1
            elif va == vb:
                common += 1

        return common / total if total > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# GEPA 进化引擎
# ═══════════════════════════════════════════════════════════════

class GEPAEngine:
    """GEPA (Genetic-Pareto) 进化引擎。
    
    使用示例:
        engine = GEPAEngine(llm_client=openai_client, llm_model="deepseek-chat")
        
        result = engine.evolve(
            seed={"system_prompt": "你是一个翻译助手..."},
            evaluator=my_evaluate_prompt,
            task_description="优化翻译提示词，提高翻译准确率",
            trainset=[...],      # 训练数据
            valset=[...],        # 验证数据
            gene_keys=["system_prompt"],
            mutation_strategies=MUTATION_STRATEGIES,
            max_generations=5,
            population_size=4,
            elite_size=1,
        )
    """

    def __init__(self,
                 llm_client=None,
                 llm_model: str = "deepseek-chat",
                 on_generation=None,  # callback(gen_num, candidates)
                 verbose: bool = True):
        self.llm = llm_client
        self.llm_model = llm_model
        self.on_generation = on_generation
        self.verbose = verbose
        self.evolution_id = uuid.uuid4().hex[:12]

    def evolve(self,
               seed: Dict[str, Any],
               evaluator: Callable[[Dict[str, Any]], Dict[str, Any]],
               task_description: str,
               trainset: List[Dict] = None,
               valset: List[Dict] = None,
               gene_keys: List[str] = None,
               mutation_strategies: List[str] = None,
               max_generations: int = 5,
               population_size: int = 4,
               elite_size: int = 1,
               early_stop_patience: int = 3) -> EvolutionReport:
        """
        执行一轮 GEPA 进化。

        Args:
            seed: 初始基因（种子方案）
            evaluator: 评估函数，输入 genes，返回 {"score": float, "metrics": {...}, "trajectory": [...]}
            task_description: 任务描述，用于 LLM 变异和反思
            trainset: 训练数据集（可选，用于上下文增强）
            valset: 验证数据集
            gene_keys: 要优化的基因字段（默认所有 seed 字段）
            mutation_strategies: 变异策略列表
            max_generations: 最大迭代代数
            population_size: 每代种群大小
            elite_size: 每代保留的精英数量
            early_stop_patience: 连续无改善则提前停止 (0=不停止)

        Returns:
            EvolutionReport
        """
        if gene_keys is None:
            gene_keys = list(seed.keys())
        if mutation_strategies is None:
            mutation_strategies = MUTATION_STRATEGIES
        trainset = trainset or []
        valset = valset or []

        started_at = datetime.now().isoformat()

        # 1. 创建种子候选
        seed_candidate = Candidate(
            id=f"seed-{self.evolution_id[:6]}",
            genes=copy.deepcopy(seed),
            generation=0,
            parent_id="root",
            mutation_strategy="seed",
        )
        self._log(f"🧬 种子候选: {seed_candidate.id}")

        # 2. 评估种子
        seed_candidate = self._evaluate(seed_candidate, evaluator, valset)
        population = [seed_candidate]
        all_candidates = [seed_candidate]

        best_overall = seed_candidate
        no_improve_count = 0
        total_evals = 1

        self._log(f"   初始得分: {seed_candidate.score:.4f}")

        # 3. 世代循环
        for gen in range(1, max_generations + 1):
            self._log(f"\n🔬 第 {gen}/{max_generations} 代 ———————")

            # 3a. 变异：从当前种群生成新候选
            new_candidates = self._mutate(
                population=population,
                task_description=task_description,
                gene_keys=gene_keys,
                strategies=mutation_strategies,
                target_count=population_size - elite_size,
                generation=gen,
            )

            # 3b. 评估所有新候选
            for candidate in new_candidates:
                candidate = self._evaluate(candidate, evaluator, valset)
                all_candidates.append(candidate)
                population.append(candidate)
                total_evals += 1

            # 3c. 反思：分析低分候选的失败原因
            self._reflect(population, task_description, gene_keys)

            # 3d. 选择：保留最优 + 多样性
            metric_keys = list(population[-1].metrics.keys()) if population[-1].metrics else ["score"]
            selected = ParetoSelector.select(
                population, elite_size, metric_keys,
                diversity_weight=0.15
            )
            population = selected

            # 当前最佳
            current_best = max(population, key=lambda c: c.score)
            improvement = current_best.score - best_overall.score
            self._log(f"   最佳: {current_best.id} 得分={current_best.score:.4f} "
                      f"(策略: {current_best.mutation_strategy})")

            if improvement > 0.001:
                best_overall = current_best
                no_improve_count = 0
                self._log(f"   📈 改善 +{improvement:.4f}")
            else:
                no_improve_count += 1
                self._log(f"   ⏸  无改善 (连续{no_improve_count}代)")

            # 回调
            if self.on_generation:
                self.on_generation(gen, population)

            # 提前停止
            if early_stop_patience and no_improve_count >= early_stop_patience:
                self._log(f"   ⏹ 提前停止 (连续{no_improve_count}代无改善)")
                break

        # 4. 计算最终的 Pareto 前沿
        metric_keys = list(best_overall.metrics.keys()) if best_overall.metrics else ["score"]
        pareto = ParetoSelector.pareto_frontier(all_candidates, metric_keys)

        return EvolutionReport(
            evolution_id=self.evolution_id,
            seed=seed_candidate,
            best_candidate=best_overall,
            all_candidates=all_candidates,
            generations=gen,
            total_evaluations=total_evals,
            improvement=best_overall.score - seed_candidate.score,
            pareto_frontier=pareto,
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
        )

    # ── 私有方法 ──────────────────────────────────────────────

    def _mutate(self, population: List[Candidate], task_description: str,
                gene_keys: List[str], strategies: List[str],
                target_count: int, generation: int) -> List[Candidate]:
        """使用 LLM 生成变异候选。"""
        candidates = []
        strategies_used = []

        for i in range(target_count):
            # 选一个父代（轮盘赌，得分越高概率越大）
            parent = self._select_parent(population)

            # 选一个未用过（或较少用）的策略
            strategy = self._pick_strategy(strategies, strategies_used)
            strategies_used.append(strategy)

            # LLM 变异
            mutated_genes = self._llm_mutate(
                parent.genes, task_description, gene_keys, strategy
            )

            candidate = Candidate(
                id=f"gen{generation}-{uuid.uuid4().hex[:6]}",
                genes=mutated_genes,
                generation=generation,
                parent_id=parent.id,
                mutation_strategy=strategy,
            )
            candidates.append(candidate)

        return candidates

    def _select_parent(self, population: List[Candidate]) -> Candidate:
        """轮盘赌选择（得分越高越容易被选中）。"""
        total = sum(c.score + 0.01 for c in population)
        r = random.random() * total
        cum = 0
        for c in population:
            cum += c.score + 0.01
            if cum >= r:
                return c
        return population[-1]

    def _pick_strategy(self, strategies: List[str], used: List[str]) -> str:
        """选择变异策略（优先选使用次数少的）。"""
        if not strategies:
            return "随机微调"
        counts = {s: used.count(s) for s in strategies}
        min_count = min(counts.values()) if counts else 0
        candidates = [s for s, c in counts.items() if c == min_count]
        return random.choice(candidates)

    def _llm_mutate(self, genes: Dict[str, Any], task_description: str,
                    gene_keys: List[str], strategy: str) -> Dict[str, Any]:
        """调用 LLM 生成变异版本。"""
        if not self.llm:
            return self._simple_mutate(genes, gene_keys, strategy)

        genes_text = json.dumps(genes, ensure_ascii=False, indent=2)
        prompt = f"""你是提示词优化专家。基于以下原始方案，应用变异策略生成一个新版本。

## 任务描述
{task_description}

## 变异策略
{strategy}

## 原始方案
{genes_text}

## 要求
1. 只修改 {', '.join(gene_keys)} 这些字段
2. 其他字段保持不变
3. 输出纯 JSON，不要包含 markdown 标记

输出:
{{"""

        try:
            response = self._llm_chat(prompt)
            # 清理可能的 markdown
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            response = response.strip()
            mutated = json.loads(response)

            # 只保留 gene_keys 中的字段，其他从原 genes 继承
            result = copy.deepcopy(genes)
            for key in gene_keys:
                if key in mutated:
                    result[key] = mutated[key]
            return result
        except Exception as e:
            self._log(f"   ⚠️ LLM 变异失败: {e}，使用简单变异")
            return self._simple_mutate(genes, gene_keys, strategy)

    def _simple_mutate(self, genes: Dict[str, Any], gene_keys: List[str],
                       strategy: str) -> Dict[str, Any]:
        """无 LLM 时的简单变异（随机调整）。"""
        result = copy.deepcopy(genes)
        for key in gene_keys:
            if isinstance(result[key], str):
                # 随机添加/删除修饰词
                if random.random() > 0.5:
                    result[key] = result[key].replace("请", "请务必")
                else:
                    result[key] = result[key].replace("请", "")
        return result

    def _evaluate(self, candidate: Candidate,
                  evaluator: Callable, valset: List[Dict]) -> Candidate:
        """评估候选方案。"""
        try:
            eval_result = evaluator(candidate.genes)
            candidate.score = float(eval_result.get("score", 0))
            candidate.metrics = eval_result.get("metrics", {})
            candidate.trajectory = eval_result.get("trajectory", [])
        except Exception as e:
            self._log(f"   ❌ 评估失败 ({candidate.id}): {e}")
            candidate.score = 0.0
            candidate.metrics = {"error": 1.0}
        return candidate

    def _reflect(self, population: List[Candidate],
                 task_description: str, gene_keys: List[str]):
        """让 LLM 分析失败候选的轨迹，产生反思。"""
        if not self.llm:
            return

        # 找出低分候选（得分低于中位数）
        if len(population) < 2:
            return
        median_score = sorted([c.score for c in population])[len(population) // 2]
        low_performers = [c for c in population if c.score < median_score]
        best = max(population, key=lambda c: c.score)

        if not low_performers:
            return

        # 构建反思 prompt
        low_texts = []
        for c in low_performers[:3]:  # 最多分析3个
            low_texts.append({
                "id": c.id,
                "strategy": c.mutation_strategy,
                "score": c.score,
                "genes_diff": {k: c.genes.get(k, "") for k in gene_keys},
            })

        best_text = {k: best.genes.get(k, "") for k in gene_keys}

        prompt = f"""你是方案优化分析专家。以下是本轮进化中表现较差和最好的候选方案。

## 任务
{task_description}

## 最佳方案 (得分: {best.score:.4f})
{json.dumps(best_text, ensure_ascii=False, indent=2)}

## 表现较差的方案
{json.dumps(low_texts, ensure_ascii=False, indent=2)}

请分析：
1. 低分方案共同的问题是什么？
2. 与最佳方案的核心差异在哪里？
3. 给下一代变异提供1-2条具体建议

输出简短分析（100字以内）:"""

        try:
            reflection = self._llm_chat(prompt)
            # 将反思记录到低分候选上
            for c in low_performers:
                c.reflection = reflection
            self._log(f"   💭 反思: {reflection[:100]}...")
        except Exception as e:
            self._log(f"   ⚠️ 反思生成失败: {e}")

    def _llm_chat(self, prompt: str) -> str:
        """调用 LLM 对话。"""
        try:
            response = self.llm.chat.completions.create(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=2048,
            )
            return response.choices[0].message.content
        except Exception as e:
            raise RuntimeError(f"LLM 调用失败: {e}")

    def _log(self, msg: str):
        if self.verbose:
            print(f"[GEPA] {msg}")


# ═══════════════════════════════════════════════════════════════
# 独立测试
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("GEPA 进化引擎 v1.0")
    print("使用示例: gepa_engine.py")
    print()
    print("快速测试（无 LLM）:")

    # 模拟评估器
    def dummy_evaluator(genes):
        prompt = genes.get("system_prompt", "")
        # 模拟：越长的提示词得分越高（实际中当然不对，这只是测试）
        score = min(len(prompt) / 500, 1.0) + random.uniform(-0.1, 0.1)
        return {
            "score": max(0, score),
            "metrics": {"length": len(prompt), "complexity": len(set(prompt))},
            "trajectory": [],
        }

    engine = GEPAEngine(llm_client=None, verbose=True)
    report = engine.evolve(
        seed={"system_prompt": "你是一个翻译助手，请将以下中文翻译成英文。"},
        evaluator=dummy_evaluator,
        task_description="优化翻译提示词",
        max_generations=2,
        population_size=3,
        elite_size=1,
        early_stop_patience=0,
    )

    print(f"\n=== 进化报告 ===")
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
