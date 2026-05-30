"""
ExperienceFlywheel — 经验飞轮
================================
闭合"存经验 → 用经验 → 改善模板"的完整数据飞轮。

三阶段:
  1. 高分 (≥8): 向量化索引 + 模板回写提升基线
  2. 中分 (5-7): 创建 A/B 变体进行测试
  3. 低分 (<5): 记录失败模式，避免重复错误

同时还维护:
  - Agent 信用分 (EMA, α=0.2)
  - 能力卡片模板版本追踪
"""
import json
import time
from typing import Dict, List, Set


class ExperienceFlywheel:
    """经验飞轮 — 让 Agent 越用越聪明"""

    def __init__(self, redis_client, vector_store):
        """
        Args:
            redis_client: RedisClient 实例
            vector_store: ChromaVectorStore 或 MemVectorStore 实例
        """
        self.redis = redis_client
        self.vs = vector_store

    # ═══════════════════════════════════════════════
    # Phase 1: 存 — 任务完成后提取经验
    # ═══════════════════════════════════════════════

    def save_experience(self,
                        task_id: str,
                        task_description: str,
                        subtasks: List[dict],
                        final_score: float,
                        l5_signals: dict,
                        agents_used: Set[str],
                        intent: str = "general"):
        """
        任务完成后保存经验。

        改进点（相比 l0_memory._save_experience 的简单 LPUSH）:
          - 高分经验向量化索引到 Chroma（语义可检索）
          - 模板自动回写（高分 → 提升基线）
          - A/B 变体创建（中分 → 测试优化）
          - 失败模式记录（低分 → 避免重复）
        """
        # 1. 向量化索引（所有任务，方便语义搜索）
        experience_text = self._format_experience(
            task_id, task_description, subtasks, final_score, agents_used
        )
        if self.vs:
            try:
                self.vs.add(
                    f"exp:{intent}:{task_id}",
                    experience_text,
                    {
                        "type": "experience",
                        "intent": intent,
                        "score": final_score,
                        "agents": list(agents_used),
                        "subtask_count": len(subtasks),
                        "timestamp": time.time(),
                        "success": final_score >= 7,
                    },
                )
            except Exception:
                pass

        # 2. Redis List（快速 FIFO 查询兜底）
        for agent in agents_used:
            if agent.startswith("_"):
                continue
            exp = {
                "task_id": task_id,
                "agent": agent,
                "intent": intent,
                "score": final_score,
                "subtask_count": len(subtasks),
                "success": final_score >= 7,
                "agents_involved": list(agents_used),
                "subtask_actions": [
                    s.get("action", "")[:60] for s in subtasks[:5]
                ],
                "ts": time.time(),
            }
            try:
                key = f"exp:{intent}:{agent}"
                self.redis.client.lpush(key, json.dumps(exp, ensure_ascii=False))
                self.redis.client.ltrim(key, 0, 49)
            except Exception:
                pass

        # 也存 agent-agnostic 索引
        try:
            intent_exp = {
                "task_id": task_id,
                "score": final_score,
                "agents": list(agents_used),
                "actions": [s.get("action", "")[:60] for s in subtasks[:5]],
                "ts": time.time(),
                "success": final_score >= 7,
            }
            all_key = f"exp:{intent}:all"
            self.redis.client.lpush(all_key, json.dumps(intent_exp, ensure_ascii=False))
            self.redis.client.ltrim(all_key, 0, 49)
        except Exception:
            pass

        # 3. Agent 信用分更新（EMA）
        for agent in agents_used:
            self._update_agent_credit(agent, final_score)

        # 4. 按分数执行后续动作
        if final_score >= 8.0:
            self._on_high_score(task_id, agents_used, l5_signals)
        elif final_score >= 5.0:
            self._on_medium_score(task_id, agents_used, l5_signals)
        else:
            self._on_low_score(task_id, agents_used, l5_signals, subtasks)

        print(
            f"[Flywheel] {task_id} saved: score={final_score} "
            f"agents={list(agents_used)[:3]} intent={intent}",
            flush=True,
        )

    # ═══════════════════════════════════════════════
    # Phase 2: 取 — 为新任务检索经验
    # ═══════════════════════════════════════════════

    def retrieve_experiences(self,
                             task_description: str,
                             intent: str = "general",
                             top_k: int = 5) -> List[dict]:
        """
        为新任务检索相关经验

        优先使用 Chroma 语义搜索，降级到 Redis List
        只返回高分经验（score ≥ 7）
        """
        results = []

        # 1. Chroma 语义搜索
        if self.vs:
            try:
                semantic_results = self.vs.search(
                    f"task:{intent}:{task_description[:300]}", top_k=top_k
                )
                for r in semantic_results:
                    meta = r.get("meta", r.get("metadata", {}))
                    if meta.get("type") == "experience":
                        if meta.get("score", 0) >= 7:
                            results.append({
                                "source": "chroma",
                                "score": meta.get("score", 0),
                                "agents": meta.get("agents", []),
                                "text": r.get("text", "")[:500],
                                "task_id": meta.get("task_id", r.get("key", "")),
                            })
            except Exception:
                pass

        # 2. Redis List 兜底
        if len(results) < 3:
            try:
                for key in [f"exp:{intent}:all"]:
                    raw_list = self.redis.client.lrange(key, 0, 4)
                    for item in raw_list:
                        try:
                            exp = json.loads(item)
                            if exp.get("score", 0) >= 7:
                                results.append({
                                    "source": "redis",
                                    "score": exp.get("score", 0),
                                    "agents": exp.get("agents_involved",
                                                      exp.get("agents", [])),
                                    "actions": exp.get("subtask_actions", []),
                                    "task_id": exp.get("task_id", ""),
                                })
                        except json.JSONDecodeError:
                            pass
            except Exception:
                pass

        # 去重
        seen = set()
        unique = []
        for r in results:
            tid = r.get("task_id", id(r))
            if tid not in seen:
                seen.add(tid)
                unique.append(r)

        return unique[:top_k]

    # ═══════════════════════════════════════════════
    # Phase 3: 改善 — 按分数采取行动
    # ═══════════════════════════════════════════════

    def _on_high_score(self, task_id: str, agents_used: Set[str],
                       l5_signals: dict):
        """高分 (≥8) → 模板回写提升基线"""
        if l5_signals.get("template_promotable"):
            for agent_name in agents_used:
                self._promote_template(agent_name, task_id, l5_signals)

    def _on_medium_score(self, task_id: str, agents_used: Set[str],
                         l5_signals: dict):
        """中分 (5-7) → 创建 A/B 变体测试"""
        if l5_signals.get("prompt_needs_optimization"):
            for agent_name in agents_used:
                self._create_ab_variant(agent_name, task_id)

    def _on_low_score(self, task_id: str, agents_used: Set[str],
                      l5_signals: dict, subtasks: List[dict]):
        """低分 (<5) → 记录失败模式供分析"""
        failure_pattern = {
            "task_id": task_id,
            "score": l5_signals.get("overall", 0),
            "issues": l5_signals.get("key_issues", []),
            "agents": list(agents_used),
            "subtask_actions": [
                s.get("action", "")[:80] for s in subtasks[:5]
            ],
            "ts": time.time(),
        }
        try:
            self.redis.client.lpush(
                "yaxiio:failure_patterns",
                json.dumps(failure_pattern, ensure_ascii=False),
            )
            self.redis.client.ltrim("yaxiio:failure_patterns", 0, 99)
        except Exception:
            pass

    # ═══════════════════════════════════════════════
    # 模板管理
    # ═══════════════════════════════════════════════

    def _promote_template(self, agent_name: str, task_id: str,
                          l5_signals: dict):
        """高分模板提升：更新能力卡片模板版本"""
        card_key = f"agent:card:{agent_name}"
        try:
            card_raw = self.redis.get(card_key)
            if not card_raw:
                return
            card = json.loads(card_raw)
            version = card.get("_template_version", 1) + 1
            card["_template_version"] = version
            card["_last_promoted_score"] = l5_signals.get("overall", 0)
            card["_last_promoted_task"] = task_id
            card["_promoted_at"] = time.time()
            self.redis.set(card_key, json.dumps(card, ensure_ascii=False))
            print(f"[Flywheel] 📈 {agent_name} 模板 v{version} (score={l5_signals.get('overall',0)})", flush=True)
        except Exception as e:
            print(f"[Flywheel] 模板提升失败 ({agent_name}): {e}", flush=True)

    def _create_ab_variant(self, agent_name: str, task_id: str):
        """为中分配置创建 A/B 测试变体"""
        try:
            from modules.layer5.ab_tester import ABTester

            tester = ABTester()
            card_key = f"agent:card:{agent_name}"
            card_raw = self.redis.get(card_key)
            if not card_raw:
                return

            card = json.loads(card_raw)
            variant_a = card.get("system_prompt", "")
            # 变体 B: 增加结构化输出指令
            variant_b = variant_a + (
                "\n\n## 输出要求（重要）\n"
                "1. 以 JSON 格式返回结果\n"
                "2. 包含 reasoning 字段说明推理过程\n"
                "3. 包含 confidence 字段说明置信度 (0-1)\n"
            )

            test_name = f"prompt:{agent_name}:{task_id[:12]}"
            tester.start(
                test_name,
                {"prompt": variant_a},
                {"prompt": variant_b},
                traffic_split=0.3,
            )
            print(f"[Flywheel] 🧪 A/B 测试创建: {test_name}", flush=True)
        except Exception as e:
            print(f"[Flywheel] A/B 创建失败: {e}", flush=True)

    # ═══════════════════════════════════════════════
    # Agent 信用分
    # ═══════════════════════════════════════════════

    def _update_agent_credit(self, agent_name: str, score: float):
        """EMA 更新 Agent 信用分（指数移动平均 α=0.2）"""
        try:
            credit_key = f"agent:credit:{agent_name}"
            current = float(self.redis.get(credit_key) or "7.0")
            new_credit = current * 0.8 + score * 0.2
            self.redis.set(credit_key, str(round(new_credit, 2)))
        except Exception:
            pass

    def get_agent_credit(self, agent_name: str) -> float:
        """获取 Agent 信用分"""
        try:
            return float(self.redis.get(f"agent:credit:{agent_name}") or "7.0")
        except Exception:
            return 7.0

    def get_top_agents(self, n: int = 5) -> List[tuple]:
        """返回信用分最高的 Agent"""
        try:
            keys = self.redis.keys("agent:credit:*")
            scored = []
            for key in keys:
                name = key.replace("agent:credit:", "")
                credit = float(self.redis.get(key) or "7.0")
                scored.append((name, credit))
            scored.sort(key=lambda x: -x[1])
            return scored[:n]
        except Exception:
            return []

    # ═══════════════════════════════════════════════
    # 辅助
    # ═══════════════════════════════════════════════

    def _format_experience(self, task_id: str, task_description: str,
                           subtasks: List[dict], score: float,
                           agents: Set[str]) -> str:
        """格式化经验文本用于向量化"""
        parts = [
            f"Task: {task_description[:200]}",
            f"Score: {score}/10",
            f"Agents: {', '.join(agents)}",
            "Subtasks:",
        ]
        for st in subtasks[:8]:
            parts.append(f"  - {st.get('action', '')[:80]} → {st.get('agent', '')}")
        return "\n".join(parts)
