"""LLM-as-Judge — 用强模型评估任务输出质量"""
import json, asyncio
from typing import Dict

JUDGE_PROMPT = """你是严格的质量评审专家。评估以下 AI 任务的执行结果。

## 任务描述
{task}

## 执行结果
{result}

## 评分维度 (1-10分)
1. **完成度**: 任务目标是否完全达成？
2. **准确性**: 事实、数据、结论是否正确？
3. **格式规范**: 输出格式是否标准、可解析？
4. **实用性**: 对用户是否有实际价值？
5. **效率**: 是否有冗余或不必要的步骤？

## 输出格式(仅JSON)
{{"completeness":8,"accuracy":7,"format":9,"usefulness":6,"efficiency":7,"overall":7.4,"summary":"一句话总结","issues":["问题1"],"suggestions":["建议1"]}}
"""

class LLMJudge:
    def __init__(self, llm_client=None, redis_client=None):
        self.llm = llm_client
        self.redis = redis_client

    async def evaluate(self, task: dict, result: dict) -> dict:
        """LLM裁判评分"""
        if not self.llm or not self.llm.available:
            return self._fallback_score(task, result)

        prompt = JUDGE_PROMPT.format(
            task=json.dumps(task, ensure_ascii=False)[:1000],
            result=json.dumps(result, ensure_ascii=False)[:2000]
        )
        try:
            response = await self.llm.chat(prompt)
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                scores = json.loads(response[json_start:json_end])
                scores["judge_model"] = self.llm.model
                scores["judged_at"] = __import__("time").strftime("%Y-%m-%dT%H:%M:%S")
                # 记录到 Redis
                if self.redis:
                    self.redis.set(f"judge:{task.get('task_id','')}", json.dumps(scores, ensure_ascii=False))
                return scores
        except Exception as e:
            print(f"[LLMJudge] evaluation failed: {e}")

        return self._fallback_score(task, result)

    def evaluate_sync(self, task: dict, result: dict) -> dict:
        """同步评估（Commander 中调用）"""
        try:
            loop = asyncio.new_event_loop()
            r = loop.run_until_complete(self.evaluate(task, result))
            loop.close()
            return r
        except:
            return self._fallback_score(task, result)

    def _fallback_score(self, task: dict, result: dict) -> dict:
        """LLM不可用时的快速评分"""
        status = result.get("status", "")
        if status == "success":
            subs = result.get("subtasks", [])
            if subs:
                done = sum(1 for s in subs if s.get("status") in ("completed","dispatched"))
                overall = min(10.0, 5.0 + (done / len(subs)) * 5.0)
            else:
                overall = 7.5
        elif status == "failed":
            overall = 3.0
        else:
            overall = 5.0
        return {"completeness": overall, "overall": overall, "method": "fallback"}
