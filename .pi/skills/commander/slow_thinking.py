"""
雅西慢思考策略 (Yaxiio Slow Thinking)
=====================================
基于 CTM (Continuous Thought Machine) 思想的策略锦标赛机制。

核心流程:
  1. 任务到达 → 复杂度判定
  2. 复杂任务 → 拆小样本 → 并行试跑多策略
  3. L5 评分择最优 → 规模化执行
  4. 简单任务 → 快思考直通

触发条件 (任一满足即开启):
  - payload 中包含 _slow_thinking: true
  - 任务标记为 "audit"/"analyze"/"evolve" 等复杂类型
  - 任务描述包含 "大规模"/"全量"/"全部" 关键词
  - codebase 文件数 > 100

配置:
  SAMPLE_SIZE: 小样本数量 (默认 3)
  PASS_SCORE: 通过评分阈值 (默认 6.0)
  MAX_STRATEGIES: 最多并行策略数 (默认 3)
  TIMEOUT_PER_SAMPLE: 单样本超时秒数 (默认 120)
"""

import json, time, os

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

SLOW_THINKING_CONFIG = {
    "enabled": os.environ.get("YAXIIO_SLOW_THINKING", "true").lower() == "true",
    "sample_size": int(os.environ.get("YAXIIO_ST_SAMPLE_SIZE", "3")),
    "pass_score": float(os.environ.get("YAXIIO_ST_PASS_SCORE", "6.0")),
    "max_strategies": int(os.environ.get("YAXIIO_ST_MAX_STRATEGIES", "3")),
    "timeout_per_sample": int(os.environ.get("YAXIIO_ST_TIMEOUT", "120")),
}

# 默认触发慢思考的任务类型
SLOW_THINKING_ACTIONS = {"site_audit", "site_evolve", "site_drill", "analyze", "diagnose"}

# 触发慢思考的关键词
SLOW_THINKING_KEYWORDS = ["大规模", "全量", "全部", "批量", "审计", "全面检查", "深度分析"]


class SlowThinkingOrchestrator:
    """慢思考编排器 — 小样本试跑 + 评分门控"""

    def __init__(self, workflow_engine):
        self.wf = workflow_engine
        self.config = SLOW_THINKING_CONFIG

    def should_slow_think(self, task_id: str, payload: dict) -> bool:
        """判断是否应该启用慢思考"""
        if not self.config["enabled"]:
            return False

        # 显式标记
        if payload.get("_slow_thinking"):
            return True

        action = str(payload.get("action", "")).lower()
        # 复杂任务类型
        if action in SLOW_THINKING_ACTIONS:
            return True

        # 关键词匹配
        task_text = str(payload.get("task", "")) + str(payload.get("description", ""))
        for kw in SLOW_THINKING_KEYWORDS:
            if kw in task_text:
                return True

        # 大规模 codebase
        codebase = payload.get("codebase", "")
        if codebase and os.path.isdir(codebase):
            try:
                file_count = sum(1 for _ in os.listdir(codebase))
                if file_count > 100:
                    return True
            except Exception:
                pass

        return False

    def extract_sample(self, payload: dict) -> dict:
        """从任务中提取小样本"""
        sample = dict(payload)
        task = str(payload.get("task", ""))

        # 缩小任务范围
        sample["_slow_thinking"] = True
        sample["_is_sample"] = True

        # 如果指定了 codebase，缩小扫描范围
        codebase = payload.get("codebase", "")
        if codebase and os.path.isdir(codebase):
            # 只取前 N 个文件作为样本
            try:
                all_files = []
                for root, dirs, files in os.walk(codebase):
                    # 跳过 node_modules 等
                    dirs[:] = [d for d in dirs if d not in ("node_modules", ".git", ".nuxt", ".output")]
                    for f in files:
                        if f.endswith((".vue", ".ts", ".js", ".py")):
                            all_files.append(os.path.join(root, f))
                        if len(all_files) >= self.config["sample_size"]:
                            break
                    if len(all_files) >= self.config["sample_size"]:
                        break
                sample["_sample_files"] = all_files[: self.config["sample_size"]]
                sample["task"] = f"[小样本预热] {task} (抽样 {len(sample['_sample_files'])} 个文件)"
            except Exception:
                pass
        else:
            sample["task"] = f"[小样本预热] {task}"

        return sample

    def run_slow_think(self, task_id: str, payload: dict) -> dict:
        """
        执行慢思考流程:
          1. 提取小样本
          2. 跑一次完整 L1→L5
          3. 检查 L5 评分是否达标
          4. 达标 → 标记可全量执行
          5. 不达标 → 返回诊断信息
        """
        print(f"[慢思考] {task_id} 开启慢思考策略...", flush=True)

        sample_payload = self.extract_sample(payload)
        sample_files = sample_payload.pop("_sample_files", [])
        sample_task_id = f"{task_id}-sample"

        print(f"[慢思考] {task_id} 小样本: {len(sample_files)} 个文件, task={sample_task_id}", flush=True)

        # 执行小样本流水线
        start = time.time()
        try:
            result = self.wf.process(sample_task_id, sample_payload)
        except Exception as e:
            return {
                "slow_thinking": True,
                "phase": "sample_failed",
                "error": f"小样本执行异常: {e}",
                "elapsed_ms": int((time.time() - start) * 1000),
            }

        elapsed_ms = int((time.time() - start) * 1000)
        l5 = result.get("l5_result", {})
        score = l5.get("overall", 0)

        print(f"[慢思考] {task_id} 小样本完成: score={score}/10, elapsed={elapsed_ms}ms", flush=True)

        # 评分门控
        if score >= self.config["pass_score"]:
            print(f"[慢思考] {task_id} ✅ 评分达标 ({score}>={self.config['pass_score']}), 可全量执行", flush=True)
            return {
                "slow_thinking": True,
                "phase": "passed",
                "sample_score": score,
                "sample_elapsed_ms": elapsed_ms,
                "sample_files": sample_files,
                "can_proceed": True,
                "diagnosis": result.get("gap_analysis", {}),
            }
        else:
            print(f"[慢思考] {task_id} ❌ 评分不达标 ({score}<{self.config['pass_score']})", flush=True)
            return {
                "slow_thinking": True,
                "phase": "failed",
                "sample_score": score,
                "sample_elapsed_ms": elapsed_ms,
                "can_proceed": False,
                "reason": f"小样本评分 {score} 低于阈值 {self.config['pass_score']}",
                "diagnosis": result.get("gap_analysis", {}),
                "suggestion": "建议: 调整任务描述、拆分任务或检查 Agent 配置",
            }


# 全局实例
slow_thinking = SlowThinkingOrchestrator(None)
