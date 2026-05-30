"""
雅西预热门控 (Warmup Gate)
============================
在侦察之后、全量执行之前，通过小样本试跑找到最优执行策略。

流程:
  1. 侦察 → 知道体量
  2. 提取小样本 → 跑 L1→L5 (含 round 重试)
  3. L5 评分 + gap 分析 → 自动调整策略
  4. 达标 → 锁定策略 → 全量执行
  5. 不达标 → 调整参数重试 (最多 N 轮)

策略维度 (预热阶段可调整):
  - thinking: medium / high (via Gap 建议 "需要更深推理")
  - agent: 是否更换 Agent (via Gap 建议 "Agent 不匹配")
  - chunk: 拆分粒度 (via 侦察报告)
  - flow: 简单流程 vs LLM 拆解复杂流程

输出:
  WarmupResult {
    passed: bool,
    best_strategy: {...},   # 锁定给全量用的策略
    sample_score: float,
    rounds_used: int,
    diagnosis: {...},       # 为什么通过/失败
  }
"""

import json, os, time, uuid

MAX_WARMUP_ROUNDS = 3       # 预热阶段最多重试几轮
PASS_SCORE = 6.0            # 通过分 (低于全量执行的 7.0，因为样本小)
SAMPLE_SIZE = 5             # 默认小样本文件数


class WarmupStrategy:
    """执行策略 — 预热阶段可以调整的参数"""

    def __init__(self, thinking="medium", agent=None, chunk=10, use_complex=False):
        self.thinking = thinking
        self.agent = agent          # None = 使用默认 Agent
        self.chunk = chunk
        self.use_complex = use_complex  # 是否用 LLM 拆解流程

    def to_dict(self):
        return {
            "thinking": self.thinking,
            "agent": self.agent,
            "chunk": self.chunk,
            "use_complex_flow": self.use_complex,
        }

    @classmethod
    def from_recon(cls, recon: dict) -> "WarmupStrategy":
        """从侦察报告生成初始策略"""
        recs = recon.get("recommendations", {})
        return cls(
            thinking="medium",
            chunk=recs.get("chunk_size", 10),
            use_complex=recon.get("volume", {}).get("total_files", 0) > 200,
        )

    def apply_gap(self, gap: dict, round_num: int) -> "WarmupStrategy":
        """根据 L5 gap 分析调整策略"""
        s = WarmupStrategy(
            thinking=self.thinking,
            agent=self.agent,
            chunk=self.chunk,
            use_complex=self.use_complex,
        )
        # Gap 建议 → 策略调整
        signals = gap.get("evolution_signals", {})
        if signals.get("prompt_needs_optimization"):
            s.thinking = "high" if s.thinking == "medium" else "max"
        if signals.get("agent_mismatch"):
            s.agent = None  # 重置，让 Commander 重新选
        if signals.get("knowledge_gap") and round_num >= 2:
            s.use_complex = True  # 尝试 LLM 拆解
        return s

    def apply_to_payload(self, payload: dict) -> dict:
        """将策略注入 payload"""
        p = dict(payload)
        if self.thinking:
            p["_thinking"] = self.thinking
        if self.agent:
            p["_agent_override"] = self.agent
        if self.use_complex:
            p["_force_complex"] = True
        p["_chunk_size"] = self.chunk
        return p


class WarmupResult:
    """预热结果"""

    def __init__(self):
        self.passed = False
        self.best_strategy = None
        self.sample_score = 0.0
        self.rounds_used = 0
        self.l5_verdict = "unknown"
        self.diagnosis = {}
        self.sample_task_id = ""
        self.elapsed_ms = 0

    def to_dict(self):
        return {
            "passed": self.passed,
            "best_strategy": self.best_strategy.to_dict() if self.best_strategy else None,
            "sample_score": self.sample_score,
            "rounds_used": self.rounds_used,
            "l5_verdict": self.l5_verdict,
            "diagnosis": self.diagnosis,
            "elapsed_ms": self.elapsed_ms,
        }


class WarmupGate:
    """预热门控 — 小样本试跑 + 策略寻优"""

    def __init__(self, workflow_engine):
        self.wf = workflow_engine
        self.max_rounds = MAX_WARMUP_ROUNDS
        self.pass_score = PASS_SCORE

    def should_warmup(self, recon: dict) -> bool:
        """判断是否需要预热"""
        vol = recon.get("volume", {})
        total = vol.get("total_files", 0)
        # 文件超过 chunk_size 才需要预热
        chunk = recon.get("recommendations", {}).get("chunk_size", 50)
        return total > chunk

    def extract_sample_payload(self, payload: dict, recon: dict, strategy: WarmupStrategy) -> dict:
        """从全量任务中提取小样本 payload"""
        sample = dict(payload)
        sample["_is_sample"] = True
        sample["_skip_recon"] = True

        codebase = payload.get("codebase", "")
        task_desc = str(payload.get("task", ""))

        # 缩小文件范围
        if codebase and os.path.isdir(codebase):
            files = []
            for root, dirs, fs in os.walk(codebase):
                dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".nuxt", ".output")]
                for f in fs:
                    if f.endswith((".vue", ".ts", ".js", ".py", ".json", ".md")):
                        files.append(os.path.join(root, f))
                    if len(files) >= strategy.chunk:
                        break
                if len(files) >= strategy.chunk:
                    break
            sample["_sample_files"] = files[: strategy.chunk]
            sample["task"] = f"[预热] {task_desc} (抽样 {len(sample['_sample_files'])} 文件)"

        # 注入策略
        sample = strategy.apply_to_payload(sample)
        return sample

    def warmup(self, task_id: str, payload: dict, recon: dict) -> WarmupResult:
        """
        执行预热流程:
          1. 生成初始策略
          2. 提取小样本
          3. 跑 L1→L5 (含 round 重试)
          4. 检查评分
          5. 不达标 → gap 调整策略 → 重试
          6. 达标 → 锁定策略
        """
        result = WarmupResult()
        start = time.time()

        strategy = WarmupStrategy.from_recon(recon)
        print(f"[预热] {task_id} 开始, 初始策略: {strategy.to_dict()}", flush=True)

        for round_num in range(1, self.max_rounds + 1):
            sample_id = f"{task_id}-warmup-r{round_num}"
            sample_payload = self.extract_sample_payload(payload, recon, strategy)

            print(f"[预热] {task_id} Round {round_num}/{self.max_rounds}: "
                  f"thinking={strategy.thinking}, chunk={strategy.chunk}, "
                  f"complex={strategy.use_complex}", flush=True)

            # 跑 L1→L5
            wf_result = self.wf.process(sample_id, sample_payload)

            l5 = wf_result.get("l5_result", {})
            score = l5.get("overall", 5)
            verdict = l5.get("verdict", "retry")
            gap = wf_result.get("gap_analysis", {})

            print(f"[预热] {task_id} Round {round_num} L5={score} verdict={verdict}", flush=True)

            result.rounds_used = round_num
            result.sample_score = score
            result.l5_verdict = verdict
            result.sample_task_id = sample_id

            # 检查是否达标
            if score >= self.pass_score and verdict in ("pass", "retry"):
                result.passed = True
                result.best_strategy = strategy
                result.diagnosis = gap
                print(f"[预热] {task_id} ✅ 达标 (score={score}), 策略锁定: {strategy.to_dict()}", flush=True)
                break

            # 不达标 → 根据 gap 调整策略
            if round_num < self.max_rounds:
                new_strategy = strategy.apply_gap(gap, round_num)
                if new_strategy.to_dict() == strategy.to_dict():
                    print(f"[预热] {task_id} 策略无变化，放弃重试", flush=True)
                    break
                strategy = new_strategy
                print(f"[预热] {task_id} 调整策略 → {strategy.to_dict()}", flush=True)
            else:
                result.diagnosis = gap
                print(f"[预热] {task_id} ❌ {self.max_rounds} 轮未达标 (score={score})", flush=True)

        result.elapsed_ms = int((time.time() - start) * 1000)
        return result

    def tournament(self, task_id: str, payload: dict, recon: dict) -> WarmupResult:
        """
        策略锦标赛: 并行试跑多个策略变体, L5评分择最优。

        候选策略:
          A: 默认 (thinking=medium, 简单流程)
          B: 深度 (thinking=high, 简单流程)
          C: LLM拆解 (thinking=medium, 复杂流程)
        """
        print(f"[锦标赛] {task_id} 开始策略锦标赛...", flush=True)
        start = time.time()
        candidates = [
            WarmupStrategy(thinking="medium", chunk=10, use_complex=False),
            WarmupStrategy(thinking="high", chunk=10, use_complex=False),
            WarmupStrategy(thinking="medium", chunk=10, use_complex=True),
        ]
        best_score, best_strategy = 0, None
        for i, st in enumerate(candidates):
            sid = f"{task_id}-t{i}"
            sp = self.extract_sample_payload(payload, recon, st)
            print(f"[锦标赛] {task_id} 策略{i}: thinking={st.thinking} complex={st.use_complex}", flush=True)
            try:
                r = self.wf.process(sid, sp)
                l5 = r.get("l5_result", {})
                s = l5.get("overall", 5)
                print(f"[锦标赛] {task_id} 策略{i}: L5={s}", flush=True)
                if s > best_score:
                    best_score, best_strategy = s, st
            except Exception as e:
                print(f"[锦标赛] {task_id} 策略{i} 异常: {e}", flush=True)
        result = WarmupResult()
        result.rounds_used = len(candidates)
        result.sample_score = best_score
        result.passed = best_score >= self.pass_score
        result.best_strategy = best_strategy
        result.l5_verdict = "pass" if result.passed else "retry"
        result.elapsed_ms = int((time.time() - start) * 1000)
        if best_strategy:
            print(f"[锦标赛] {task_id} 冠军: thinking={best_strategy.thinking} score={best_score}", flush=True)
        return result
