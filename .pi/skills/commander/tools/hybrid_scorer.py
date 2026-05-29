#!/usr/bin/env python3
"""HybridScorer: AI + Human weighted scoring for Agent System v2"""
import json, time, redis as _r

class HybridScorer:
    """混合评分器：融合 AI 评分和人类评分"""
    
    HUMAN_WEIGHT = 0.7
    AI_WEIGHT = 0.3
    ANOMALY_THRESHOLD = 3  # 人机分差 > 3 触发审查
    
    def __init__(self, redis_host="127.0.0.1", redis_port=6379, redis_pass="$REDIS_PASSWORD"):
        self.r = _r.Redis(host=redis_host, port=redis_port, password=redis_pass, decode_responses=True)
    
    def calculate(self, task_id: str, ai_score: float, human_review: dict = None) -> dict:
        """计算最终评分"""
        if human_review is None:
            human_review = self._get_human_review(task_id)
        
        if human_review is None:
            return {"score": ai_score, "source": "ai_only", "ai": ai_score}
        
        human_overall = human_review.get("overall", human_review.get("scores", {}).get("overall", 5))
        if isinstance(human_overall, str):
            human_overall = float(human_overall)
        
        # 获取评价者权重
        reviewer_weight = self._get_reviewer_weight(human_review.get("reviewer_id", "anonymous"))
        adjusted_human = human_overall * reviewer_weight
        adjusted_ai = ai_score * (2 - reviewer_weight)  # AI权重补偿
        
        final = adjusted_human * self.HUMAN_WEIGHT + adjusted_ai * self.AI_WEIGHT
        
        # 异常检测
        anomaly = None
        if abs(human_overall - ai_score) > self.ANOMALY_THRESHOLD:
            anomaly = self._record_anomaly(task_id, ai_score, human_overall, human_review)
        
        # 更新评价者信用
        self._update_reviewer_credit(human_review.get("reviewer_id", "anonymous"), 
                                      human_overall, ai_score)
        
        return {
            "score": round(final, 1),
            "source": "hybrid",
            "ai": ai_score,
            "human": human_overall,
            "human_weight": round(reviewer_weight, 2),
            "anomaly": anomaly
        }
    
    def submit_review(self, task_id: str, reviewer_id: str, scores: dict, comment: str = ""):
        """提交人类评价"""
        review = {
            "task_id": task_id,
            "reviewer": "human",
            "reviewer_id": reviewer_id,
            "scores": scores,
            "overall": round(sum(scores.values()) / len(scores), 1) if scores else 5,
            "comment": comment,
            "reviewed_at": time.time(),
            "dimensions": list(scores.keys())
        }
        self.r.setex(f"review:{task_id}", 86400 * 30, json.dumps(review, ensure_ascii=False))
        self.r.lpush(f"reviewer:{reviewer_id}:history", json.dumps(review, ensure_ascii=False))
        self.r.ltrim(f"reviewer:{reviewer_id}:history", 0, 99)
        return review
    
    def get_review_dimensions(self, agent_name: str) -> list:
        """获取该 Agent 的评价维度（从能力卡片）"""
        card_raw = self.r.get(f"agent:card:{agent_name}")
        if card_raw:
            card = json.loads(card_raw)
            return card.get("human_review_dimensions", ["accuracy", "completeness", "clarity"])
        return ["accuracy", "completeness", "clarity"]
    
    def _get_human_review(self, task_id: str) -> dict:
        raw = self.r.get(f"review:{task_id}")
        return json.loads(raw) if raw else None
    
    def _get_reviewer_weight(self, reviewer_id: str) -> float:
        profile = self.r.hgetall(f"reviewer:{reviewer_id}:profile")
        credit = float(profile.get("credit", 0.8))
        count = int(profile.get("review_count", 0))
        # 评价越多权重越高，但不超过 1.0
        frequency_bonus = min(0.2, count / 50 * 0.2)
        return min(1.0, credit + frequency_bonus)
    
    def _update_reviewer_credit(self, reviewer_id: str, human_score: float, ai_score: float):
        profile = self.r.hgetall(f"reviewer:{reviewer_id}:profile")
        old_credit = float(profile.get("credit", 0.8))
        old_count = int(profile.get("review_count", 0))
        
        # 一致性得分：人机评分越接近越可信
        consistency = 1.0 - abs(human_score - ai_score) / 10.0
        new_credit = old_credit * 0.9 + consistency * 0.1
        
        self.r.hset(f"reviewer:{reviewer_id}:profile", mapping={
            "credit": str(round(new_credit, 3)),
            "review_count": str(old_count + 1),
            "last_review_at": str(time.time())
        })
    
    def _record_anomaly(self, task_id: str, ai_score: float, human_score: float, review: dict):
        anomaly = {
            "task_id": task_id,
            "ai_score": ai_score,
            "human_score": human_score,
            "gap": abs(ai_score - human_score),
            "reviewer": review.get("reviewer_id", "unknown"),
            "ts": time.time()
        }
        self.r.lpush("review:anomalies", json.dumps(anomaly, ensure_ascii=False))
        self.r.ltrim("review:anomalies", 0, 99)
        return anomaly
    
    def get_anomalies(self, limit: int = 10) -> list:
        raw = self.r.lrange("review:anomalies", 0, limit - 1)
        return [json.loads(r) for r in raw]
    
    def get_reviewer_stats(self, reviewer_id: str) -> dict:
        profile = self.r.hgetall(f"reviewer:{reviewer_id}:profile")
        history = self.r.lrange(f"reviewer:{reviewer_id}:history", 0, 9)
        return {
            "credit": float(profile.get("credit", 0.8)),
            "review_count": int(profile.get("review_count", 0)),
            "recent_reviews": [json.loads(h) for h in history[:3]]
        }

if __name__ == "__main__":
    # Quick test
    scorer = HybridScorer()
    # Submit a test review
    scorer.submit_review("test-task-001", "jamedow", 
                        {"accuracy": 8, "completeness": 7, "professionalism": 9, "timeliness": 6},
                        "Good work but slow response")
    # Calculate hybrid score
    result = scorer.calculate("test-task-001", 5.5)
    print("Hybrid score:", json.dumps(result, ensure_ascii=False, indent=2))
    print("Reviewer stats:", json.dumps(scorer.get_reviewer_stats("jamedow"), ensure_ascii=False))
