#!/usr/bin/env python3
"""Patch workflow_engine.py: _do_L5 uses UnifiedScorer, _analyze_gap uses UniversalGapAnalyzer"""

path = "/opt/yaxiio/.pi/skills/commander/workflow_engine.py"
with open(path, "r") as f:
    content = f.read()

changes = 0

# ── Fix: Replace _do_L5 with UnifiedScorer-based implementation ──
old_start = "    def _do_L5(self, task_id: str, action: str, plan: dict, l4: dict, state: dict) -> dict:"
old_end = "    def _build_plan(self, primary_intent: str, action: str,"

idx_start = content.find(old_start)
idx_end = content.find(old_end)

if idx_start >= 0 and idx_end > idx_start:
    # Build new method as a single string
    new_lines = []
    new_lines.append("    def _do_L5(self, task_id: str, action: str, plan: dict, l4: dict, state: dict) -> dict:")
    new_lines.append('        """L5 scoring — UnifiedScorer primary path + legacy fallback"""')
    new_lines.append('        if MCP_LAYERS_ENABLED.get("L5"):')
    new_lines.append('            return {"mcp_routed": True, "layer": "L5", "phase": "not_implemented"}')
    new_lines.append("")
    new_lines.append("        # Extract output text")
    new_lines.append("        output_text = self._extract_output_text(l4)")
    new_lines.append("")
    new_lines.append("        # Resolve agent name")
    new_lines.append('        if isinstance(plan, dict) and "agent" in plan:')
    new_lines.append('            agent_name = plan["agent"]')
    new_lines.append("        else:")
    new_lines.append('            agent_name = "unknown"')
    new_lines.append('        if agent_name == "unknown" and isinstance(plan, dict):')
    new_lines.append('            subtasks = plan.get("subtasks", [])')
    new_lines.append('            agents = list(set(s.get("agent", "") for s in subtasks))')
    new_lines.append('            agent_name = ", ".join(agents[:3]) if agents else "unknown"')
    new_lines.append("")
    new_lines.append("        # Load agent capability card")
    new_lines.append("        agent_card = None")
    new_lines.append("        try:")
    new_lines.append("            if self.commander and self.commander.redis:")
    new_lines.append('                primary = agent_name.split(",")[0].strip()')
    new_lines.append('                card_raw = self.commander.redis.get(f"agent:card:{primary}")')
    new_lines.append("                if card_raw:")
    new_lines.append("                    agent_card = json.loads(card_raw)")
    new_lines.append("        except Exception:")
    new_lines.append("            pass")
    new_lines.append("")
    new_lines.append("        # Determine scoring strategy")
    new_lines.append('        if isinstance(plan, dict):')
    new_lines.append('            subtask_count = len(plan.get("subtasks", []))')
    new_lines.append("        else:")
    new_lines.append("            subtask_count = 1")
    new_lines.append("        if subtask_count <= 1 and len(output_text) < 500:")
    new_lines.append('            strategy = "fast"')
    new_lines.append("        elif subtask_count >= 5:")
    new_lines.append('            strategy = "deep"')
    new_lines.append("        else:")
    new_lines.append('            strategy = "standard"')
    new_lines.append("")
    new_lines.append("        # PRIMARY PATH: UnifiedScorer")
    new_lines.append("        try:")
    new_lines.append("            from modules.layer5.unified_scorer import UnifiedScorer")
    new_lines.append("            scorer = UnifiedScorer(redis_client=self.commander.redis if self.commander else None)")
    new_lines.append("            task_info = {")
    new_lines.append('                "task_id": task_id, "action": action,')
    new_lines.append('                "description": str(state.get("summary", ""))[:500],')
    new_lines.append('                "type": action,')
    new_lines.append("            }")
    new_lines.append("            result_info = {")
    new_lines.append('                "output": output_text[:3000],')
    new_lines.append('                "subtasks": plan.get("subtasks", []) if isinstance(plan, dict) else [],')
    new_lines.append('                "status": "success" if l4.get("results") else "partial",')
    new_lines.append("            }")
    new_lines.append("            result = scorer.score(")
    new_lines.append("                task=task_info, result=result_info,")
    new_lines.append("                strategy=strategy, agent_card=agent_card")
    new_lines.append("            )")
    new_lines.append('            label = f"overall={result.get(\'overall\',\'?\')} verdict={result.get(\'verdict\',\'?\')} sources={result.get(\'sources_used\',[])}"')
    new_lines.append('            print(f"[WF] {task_id} L5 UnifiedScorer: {label}", flush=True)')
    new_lines.append("            self.score_history.append({")
    new_lines.append('                "task_id": task_id,')
    new_lines.append('                "score": result["overall"],')
    new_lines.append('                "ts": time.time()')
    new_lines.append("            })")
    new_lines.append("            return result")
    new_lines.append("        except Exception as e:")
    new_lines.append('            print(f"[WF] {task_id} UnifiedScorer failed ({e}), fallback to legacy L5", flush=True)')
    new_lines.append("")
    new_lines.append("        # FALLBACK: legacy scoring")
    new_lines.append("        return self._legacy_l5_score(task_id, action, plan, l4, state, output_text, agent_name)")
    new_lines.append("")

    new_method = "\n".join(new_lines)
    old_method = content[idx_start:idx_end]
    content = content.replace(old_method, new_method)
    changes += 1
    print(f"OK Fix 1: _do_L5 -> UnifiedScorer")
else:
    print(f"FAIL Fix 1: start={idx_start}, end={idx_end}")

# ── Fix: Add _legacy_l5_score and _extract_output_text before _build_plan ──
marker = "    def _build_plan(self, primary_intent: str, action: str,"
if marker in content:
    helpers = '''\
    def _legacy_l5_score(self, task_id, action, plan, l4, state, output_text, agent_name):
        """Legacy L5 scoring — fallback when UnifiedScorer is unavailable"""
        context = json.dumps({"action": action, "intent": state.get("primary_intent", ""),
                              "total_rounds": state.get("total_rounds", 1)}, ensure_ascii=False)
        # Try LLM deep_score via MCP
        try:
            l5 = call_layer(5, "deep_score",
                           task_id=task_id, action=action,
                           agent_name=agent_name,
                           output=output_text[:3000], context=context)
            if l5.get("method") == "llm":
                result = {
                    "overall": l5.get("overall", 5),
                    "method": "llm_deep_score",
                    "dimensions": {k: l5.get(k, 0) for k in
                                   ["accuracy","completeness","professionalism","actionability","consistency"]},
                    "key_issues": l5.get("key_issues", []),
                    "suggestions": l5.get("suggestions", []),
                    "verdict": l5.get("verdict", "pass"),
                    "needs_review": l5.get("verdict") in ("retry", "reject"),
                    "needs_evolution": l5.get("overall", 5) < 5,
                }
                self.score_history.append({"task_id": task_id, "score": result["overall"], "ts": time.time()})
                return result
        except Exception:
            pass

        # Rule-based fallback
        has_result = bool(output_text and len(output_text) > 50)
        subtask_count = len(l4.get("results", {}))
        completeness = 8 if has_result else (5 if subtask_count > 0 else 3)
        quality = min(9, 4 + len(output_text) // 500) if has_result else 3
        base = {
            "accuracy": 5 + (2 if subtask_count >= 3 else 0),
            "completeness": completeness,
            "professionalism": 6 + (1 if len(output_text) > 1000 else 0),
            "actionability": 6 + (2 if "```" in output_text or "1." in output_text else 0),
            "consistency": 7,
        }
        base_overall = round(sum(base.values()) / len(base))
        result = {"overall": base_overall, "method": "rule_fallback", "dimensions": base,
                  "needs_review": base_overall < 7, "needs_evolution": base_overall < 5,
                  "verdict": "pass" if base_overall >= 7 else ("retry" if base_overall >= 4 else "reject")}
        self.score_history.append({"task_id": task_id, "score": base_overall, "ts": time.time()})
        return result

    def _extract_output_text(self, l4: dict) -> str:
        """Extract output text from various L4 result formats"""
        output_text = ""
        if l4.get("results") and isinstance(l4["results"], dict):
            parts = []
            for sid, r in sorted(l4["results"].items()):
                out = str(r.get("output", r.get("summary", "")))[:300]
                if out:
                    parts.append(out)
            output_text = "\\n---\\n".join(parts)
        if not output_text and l4.get("summary"):
            output_text = str(l4["summary"])[:3000]
        if not output_text:
            if isinstance(l4.get("result"), dict):
                output_text = str(l4["result"].get("output", l4["result"].get("summary", "")))
        if not output_text:
            output_text = str(l4.get("stdout", l4.get("output", "")))
        return output_text

'''
    content = content.replace(marker, helpers + marker)
    changes += 1
    print("OK Fix 2: _legacy_l5_score + _extract_output_text helpers added")
else:
    print("FAIL Fix 2: marker not found")

# ── Fix: _analyze_gap uses UniversalGapAnalyzer ──
old_gap = "    def _analyze_gap(self, *args, **kwargs):\n        return self.gap.analyze_gap(*args, **kwargs)"
new_gap = '''\
    def _analyze_gap(self, task_id: str, payload: dict, results: dict, l5_scores: dict) -> dict:
        """Gap analysis using UniversalGapAnalyzer — zero industry hardcoding"""
        try:
            from modules.layer5.gap_analyzer_v2 import UniversalGapAnalyzer
            analyzer = UniversalGapAnalyzer()

            # Load agent card
            agent_card = None
            try:
                if self.commander and self.commander.redis:
                    primary_agent = l5_scores.get("primary_agent", "\\u5ba1\\u8ba1\\u5b98")
                    card_raw = self.commander.redis.get(f"agent:card:{primary_agent}")
                    if card_raw:
                        agent_card = json.loads(card_raw)
            except Exception:
                pass

            return analyzer.analyze(
                task={"action": payload.get("action", ""),
                      "description": str(payload.get("task", ""))[:300]},
                results=results,
                l5_scores=l5_scores,
                agent_card=agent_card,
            )
        except Exception as e:
            print(f"[WF] UniversalGapAnalyzer failed ({e}), fallback to legacy", flush=True)
            return self.gap.analyze(task_id, payload, results, l5_scores)'''

if old_gap in content:
    content = content.replace(old_gap, new_gap)
    changes += 1
    print("OK Fix 3: _analyze_gap -> UniversalGapAnalyzer")
else:
    print("FAIL Fix 3: old _analyze_gap pattern not found")
    # Show what's there
    idx = content.find("def _analyze_gap")
    if idx >= 0:
        print("  Found at", idx, ":", repr(content[idx:idx+200]))

with open(path, "w") as f:
    f.write(content)

print(f"\n{changes}/3 changes applied")
