# Yaxiio v1.1 - AGPLv3
"""L0 Memory Layer — experience storage, retrieval, web knowledge"""
import json, time

class L0Memory:
    """L0: Experience + Web Knowledge storage (Redis-based)"""
    def __init__(self, redis_client):
        self.r = redis_client

    def _retrieve_experiences(self, intent: str, agents: list) -> list:
        """L0: Retrieve past experiences for given intent and agents"""
        exps = []
        try:
            r = self.r
            # Query by intent+agent and intent:all
            keys_to_check = [f"exp:{intent}:all"]
            for agent in agents[:3]:
                keys_to_check.append(f"exp:{intent}:{agent}")
            for key in keys_to_check:
                raw = r.lrange(key, 0, 4)
                for item in raw:
                    try:
                        exp = json.loads(item)
                        if exp not in exps:
                            exps.append(exp)
                    except:
                        pass
            # Also check web cache
            web_raw = r.lrange(f"web:{intent}:*", 0, 2)
            for item in web_raw:
                try:
                    exp = json.loads(item)
                    exps.append(exp)
                except:
                    pass
        except Exception as e:
            print(f"[L0] retrieve error: {e}", flush=True)

        # Phase 3: Chroma 语义搜索 — 补充关键词匹配未命中的经验
        if len(exps) < 3:
            try:
                from modules.layer1.vector_store_chroma import ChromaVectorStore
                vs = ChromaVectorStore()
                semantic = vs.search_experiences(intent, top_k=5)
                for item in semantic:
                    meta = item.get("metadata", {})
                    if meta and meta not in exps:
                        exps.append(meta)
            except Exception:
                pass  # Chroma 未安装时静默降级

        return exps[:5]

    def _save_web_knowledge(self, intent: str, concept: str, facts: list, domain: str = "general"):
        """L0: Save web research results as cached knowledge"""
        try:
            r = self.r
            ttl = {"standard": 86400*180, "price": 86400*7, "tech_spec": 86400*90,
                   "regulation": 86400*365}.get(domain, 86400*30)
            entry = {"intent": intent, "concept": concept, "facts": facts,
                     "source": "web", "domain": domain, "ts": time.time()}
            r.setex(f"web:{intent}:{concept[:40]}", ttl, json.dumps(entry, ensure_ascii=False, default=str))
            r.lpush(f"web:{intent}:all", json.dumps(entry, ensure_ascii=False, default=str))
            r.ltrim(f"web:{intent}:all", 0, 49)
            print(f"[L0] web knowledge saved: {intent}/{concept[:30]} ({domain}, ttl={ttl}s)", flush=True)
        except Exception as e:
            print(f"[L0] web save error: {e}", flush=True)

    def _should_search_web(self, l5_result: dict, intent: str) -> dict:
        """L0: Determine if web search is needed based on L5 gap analysis"""
        score = l5_result.get("overall", 10)
        verdict = l5_result.get("verdict", "pass")
        issues = l5_result.get("key_issues", [])
        issues_str = " ".join(str(i) for i in issues).lower()
        gaps = l5_result.get("gap_summary", "")
        # Check internal experience count
        try:
            r = self.r
            internal_count = r.llen(f"exp:{intent}:all")
        except:
            internal_count = 0
        knowledge_gap = any(kw in issues_str for kw in
            ["knowledge", "data", "unknown", "not found", "unable to", "no info", "missing",
             "缺少", "未知", "没有", "不确定", "无法确认", "数据不足"])
        no_internal = internal_count == 0
        can_improve = score < 5 and verdict in ("retry", "reject")
        if (knowledge_gap or no_internal) and can_improve:
            queries = [str(i)[:100] for i in issues[:3] if str(i).strip()]
            return {"should_search": True, "reason": gaps[:200] if gaps else str(issues[:2])[:200],
                    "queries": queries if queries else [str(issues[0])[:100]],
                    "internal_count": internal_count}
        return {"should_search": False, "reason": "no gap or score sufficient",
                "internal_count": internal_count}

