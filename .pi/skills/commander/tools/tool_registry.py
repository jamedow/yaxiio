#!/usr/bin/env python3
"""Agent Tool Registry — 工具描述注入 Agent 上下文"""
import json

TOOLS = {
    "audit_multilang": {
        "name": "audit_multilang",
        "desc": "Scan ALL pages in MongoDB, compare zh vs en/ru/ar/es for: mixed language, truncation, missing pages, empty fields. Outputs structured report.",
        "usage": "python3 /opt/commander/tools/multilang_audit.py",
        "output": "Markdown report at /app/.pi/blackboard/reports/multilang-audit-*.md",
        "category": "audit"
    },
    "content_sync": {
        "name": "content_sync", 
        "desc": "Sync page content from MongoDB to Redis cache. Modes: full (all pages), industry (single industry), page (single page).",
        "usage": "python3 /opt/commander/tools/content_sync.py [mode] [target]",
        "output": "JSON status with sync count",
        "category": "sync"
    },
    "fix_executor": {
        "name": "fix_executor",
        "desc": "Execute a JSON fix specification: mongo_set (update MongoDB field), redis_sync (sync to Redis), deploy (trigger deploy), verify (check result).",
        "usage": "python3 /opt/commander/tools/fix_executor.py",
        "output": "JSON result per step",
        "category": "fix"
    },
    "verify_page": {
        "name": "verify_page",
        "desc": "Verify a specific page URL: check no Chinese in non-Chinese pages, verify SEO meta, check content rendering.",
        "usage": "python3 /opt/commander/tools/verify_page.py [url]",
        "output": "Pass/fail per check",
        "category": "verify"
    },
    "mongo_query": {
        "name": "mongo_query",
        "desc": "Query MongoDB page_content collection. Read-only. Find pages by path, industry, or pageType. Returns JSON.",
        "usage": "python3 /opt/commander/tools/mongo_query.py [--path PATTERN] [--industry NAME] [--lang LANG] [--fields f1,f2]",
        "output": "JSON array of matching documents",
        "category": "query"
    },
    "redis_query": {
        "name": "redis_query", 
        "desc": "Query Redis page cache. Read page content fields from cache. Returns JSON.",
        "usage": "python3 /opt/commander/tools/redis_query.py [--key PATTERN] [--type hash|string]",
        "output": "JSON with matching keys and values",
        "category": "query"
    },
    "terminology_check": {
        "name": "terminology_check",
        "desc": "Check terminology consistency across pages. Compare actual terms against the standard terminology dictionary.",
        "usage": "python3 /opt/commander/tools/terminology_check.py [--industry NAME]",
        "output": "JSON with inconsistent terms, locations, and corrections",
        "category": "audit"
    },
    "deploy_hook": {
        "name": "deploy_hook",
        "desc": "Trigger deployment and verification. Modes: verify (check deployment), deploy (trigger deployment).",
        "usage": "python3 /opt/commander/tools/deploy_hook.py [verify|deploy] [industry]",
        "output": "JSON status",
        "category": "deploy"
    }
}

def get_tools_for_agent(agent_name: str, task_action: str = "") -> list:
    """Return relevant tools for an agent based on its role and task"""
    agent_tool_map = {
        "审计官": ["audit_multilang", "mongo_query", "redis_query", "terminology_check", "verify_page"],
        "翻译官": ["content_sync", "mongo_query", "redis_query", "terminology_check"],
        "LM内容工程师": ["mongo_query", "redis_query", "fix_executor", "deploy_hook", "verify_page"],
        "品牌策略师": ["mongo_query", "redis_query", "terminology_check"],
        "前端工程师": ["deploy_hook", "verify_page"],
        "售前经理": ["mongo_query", "redis_query"],
        "UI/UX设计师": ["verify_page"],
    }
    tool_names = agent_tool_map.get(agent_name, ["mongo_query", "redis_query"])
    return [TOOLS[n] for n in tool_names if n in TOOLS]

def tools_prompt(agent_name: str) -> str:
    """Generate tool description prompt for LLM context"""
    available = get_tools_for_agent(agent_name)
    lines = ["## Available Tools", "", "You can run these tools via bash commands:", ""]
    for t in available:
        lines.append(f"### {t['name']}")
        lines.append(f"- Description: {t['desc']}")
        lines.append(f"- Usage: `{t['usage']}`")
        lines.append(f"- Output: {t['output']}")
        lines.append("")
    return "\n".join(lines)

if __name__ == "__main__":
    import sys
    agent = sys.argv[1] if len(sys.argv) > 1 else "审计官"
    print(tools_prompt(agent))
