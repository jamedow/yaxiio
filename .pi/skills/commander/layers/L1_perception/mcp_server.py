
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_L1 = 0x26352dc3

"""
L1 Perception Server — 感知层 MCP Server
=========================================
工具:
  - analyze_intent(text) → {intents, confidence, language}
  - extract_keywords(text) → [keywords]
  - check_duplicate(task) → {is_duplicate, original_task_id, summary}
"""

import sys, os
sys.path.insert(0, '/opt/commander')

from mcp.protocol import MCPServer, run_mcp_server
from config import L1_PERCEPTION_PORT


class PerceptionServer(MCPServer):
    """感知层: 输入解析 + 意图识别。"""

    def __init__(self):
        super().__init__("L1_perception", "Perception Layer — Intent Recognition & Input Analysis")

        self.register_tool("analyze_intent", self.analyze_intent)
        self.register_tool("extract_keywords", self.extract_keywords)
        self.register_tool("check_duplicate", self.check_duplicate)

    def analyze_intent(self, text: str) -> dict:
        """分析任务意图。"""
        text_lower = text.lower()
        intents = []
        confidence = 0.5

        patterns = {
            "translate": (["翻译", "translate", "перевод", "ترجمة"], 0.95),
            "quote": (["报价", "quote", "询价", "inquiry"], 0.9),
            "deploy": (["部署", "deploy", "发布", "release"], 0.9),
            "audit": (["审计", "audit", "检查", "review"], 0.85),
            "search": (["搜索", "search", "find", "查询"], 0.9),
            "generate": (["生成", "generate", "create", "build"], 0.8),
        }

        for intent, (keywords, conf) in patterns.items():
            if any(kw in text_lower for kw in keywords):
                intents.append(intent)
                confidence = max(confidence, conf)

        if not intents:
            intents.append("general")
            confidence = 0.5

        return {
            "intents": intents,
            "primary_intent": intents[0],
            "confidence": confidence,
            "language": "zh" if any('\u4e00' <= c <= '\u9fff' for c in text) else "en",
            "text_length": len(text),
        }

    def extract_keywords(self, text: str) -> list:
        """提取关键词。"""
        import re
        stopwords = {"the","a","an","is","to","of","in","for","and","的","了","是","在"}
        words = re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', text.lower())
        return [w for w in words if w not in stopwords][:10]

    def check_duplicate(self, task: str) -> dict:
        """检查重复任务（简化版）。"""
        return {
            "is_duplicate": False,
            "original_task_id": None,
            "summary": None,
            "fingerprint": str(hash(task[:100])),
        }


if __name__ == "__main__":
    run_mcp_server("L1_perception", PerceptionServer(), L1_PERCEPTION_PORT)
