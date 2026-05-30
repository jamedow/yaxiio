"""
DSPyOptimizer — DSPy 驱动的 Prompt 自动优化
============================================
替代 PromptOptimizer 的词频相似度 A/B 选择。
使用 DSPy 编译器自动 find 最优 few-shot 示例和指令结构。
"""

import os, json
from typing import List, Dict, Optional


class DSPyOptimizer:
    """DSPy Prompt 编译器 — 渐进替代 PromptOptimizer"""

    def __init__(self):
        self._dspy = None
        self._lm = None
        self._configured = False

    def _ensure_configured(self):
        if self._configured:
            return
        try:
            import dspy
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key:
                try:
                    import redis as _r
                    r = _r.Redis(host="127.0.0.1", port=6379,
                                password=os.environ.get("REDIS_PASSWORD",""),
                                decode_responses=True, socket_connect_timeout=3)
                    api_key = r.get("yaxiio:config:llm_api_key") or ""
                except Exception:
                    pass

            if api_key:
                lm = dspy.LM(
                    "openai/deepseek-chat",
                    api_key=api_key,
                    api_base="https://api.deepseek.com/v1",
                )
                dspy.configure(lm=lm)
                self._dspy = dspy
                self._lm = lm
                self._configured = True
        except ImportError:
            pass
        except Exception:
            pass

    def optimize_prompt(self, base_prompt: str, examples: List[Dict],
                        task_description: str = "") -> Optional[str]:
        """
        使用 DSPy 编译优化 Prompt。

        Args:
            base_prompt: 当前 prompt
            examples: 训练示例 [{"input": ..., "output": ...}, ...]
            task_description: 任务描述

        Returns:
            优化后的 prompt，或 None（降级到 PromptOptimizer）
        """
        self._ensure_configured()
        if not self._configured or len(examples) < 3:
            return None

        try:
            import dspy

            # 定义 DSPy Signature
            class OptimizableTask(dspy.Signature):
                __doc__ = task_description or "Execute the task based on input"
                input_text: str = dspy.InputField()
                output_text: str = dspy.OutputField()

            # 构建训练集
            trainset = []
            for ex in examples[:10]:
                trainset.append(dspy.Example(
                    input_text=str(ex.get("input", ""))[:500],
                    output_text=str(ex.get("output", ""))[:500],
                ).with_inputs("input_text"))

            # 编译优化
            program = dspy.ChainOfThought(OptimizableTask)
            optimizer = dspy.BootstrapFewShot(
                metric=self._simple_metric,
                max_bootstrapped_demos=3,
                max_rounds=2,
            )
            optimized = optimizer.compile(program, trainset=trainset)

            # 提取优化后的 prompt
            if hasattr(optimized, 'demos') and optimized.demos:
                demo_texts = []
                for demo in optimized.demos[:3]:
                    demo_texts.append(
                        f"Example:\nInput: {demo.input_text}\nOutput: {demo.output_text}"
                    )
                few_shot = "\n\n".join(demo_texts)
                return f"{base_prompt}\n\n## Reference Examples\n{few_shot}"

            return None
        except Exception:
            return None

    def _simple_metric(self, example, pred, trace=None):
        """简单评分: 输出非空 + 长度合理"""
        if not pred.output_text:
            return 0.0
        if len(pred.output_text) < 10:
            return 0.3
        return 0.8  # 基础分 (实际应用中可用 LLM-as-Judge)

    def is_available(self) -> bool:
        self._ensure_configured()
        return self._configured


# 全局单例
_dspy_instance = None

def get_dspy_optimizer() -> DSPyOptimizer:
    global _dspy_instance
    if _dspy_instance is None:
        _dspy_instance = DSPyOptimizer()
    return _dspy_instance
