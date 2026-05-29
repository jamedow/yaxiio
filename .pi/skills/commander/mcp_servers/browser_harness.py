#!/usr/bin/env python3
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Browser Harness MCP Server — Commander 浏览器自动化工具
==========================================================
通过 MCP 协议 (JSON-RPC 2.0 over stdio) 暴露浏览器自动化能力，
供 Commander 的各种 Subagent 调用。

协议:
  请求:  stdin 读取 JSON-RPC 2.0 请求（每行一个）
  响应:  stdout 输出 JSON-RPC 2.0 响应

工具列表:
  browser_navigate       — 导航到 URL
  browser_click          — 点击元素
  browser_type           — 输入文本
  browser_screenshot     — 截图
  browser_extract_text   — 提取页面文本
  browser_extract_links  — 提取所有链接
  browser_extract_html   — 提取页面 HTML
  browser_evaluate       — 执行 JS 代码
  browser_wait           — 等待元素出现
  browser_scroll         — 滚动页面
  browser_get_url        — 获取当前 URL
  browser_get_title      — 获取页面标题

安全:
  - 仅允许 HTTP/HTTPS 协议
  - 默认超时 30s
  - 单实例浏览器，避免资源泄漏

Constitution:
  R1 — 不碰 page:* / lightingmetal:* 前缀
  R2 — 所有操作记录到 commander:browser:log
"""

import json
import sys
import time
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PlaywrightTimeout
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ═══════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════

SERVER_NAME = "browser-harness"
SERVER_VERSION = "1.0.0"
DEFAULT_TIMEOUT_MS = 30000
VIEWPORT = {"width": 1920, "height": 1080}
ALLOWED_PROTOCOLS = ("http:", "https:")

# ═══════════════════════════════════════════════════════════════
# 浏览器管理器
# ═══════════════════════════════════════════════════════════════

class BrowserManager:
    """管理 Playwright 浏览器实例（单例）。"""

    def __init__(self):
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._page: Optional[Page] = None

    def ensure_browser(self):
        """确保浏览器可用。"""
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("Playwright 未安装")

        if self._browser is None or not self._browser.is_connected():
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ]
            )
            self._page = self._browser.new_page(viewport=VIEWPORT)
            self._page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            log(f"浏览器已启动")

        return self._page

    def close(self):
        """关闭浏览器。"""
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._page = None
            self._browser = None
            self._playwright = None


browser_mgr = BrowserManager()


# ═══════════════════════════════════════════════════════════════
# MCP 协议处理
# ═══════════════════════════════════════════════════════════════

def log(msg: str):
    """输出到 stderr（不影响 MCP 协议）。"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [BrowserHarness] {msg}", file=sys.stderr, flush=True)


def send_response(id, result=None, error=None):
    """发送 JSON-RPC 2.0 响应。"""
    response = {"jsonrpc": "2.0", "id": id}
    if error:
        response["error"] = error
    else:
        response["result"] = result
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def send_notification(method, params=None):
    """发送 JSON-RPC 2.0 通知（无 id）。"""
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ═══════════════════════════════════════════════════════════════
# 工具定义
# ═══════════════════════════════════════════════════════════════

TOOLS = [
    {
        "name": "browser_navigate",
        "description": "导航到指定 URL",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "目标 URL"},
                "timeout": {"type": "number", "description": "超时毫秒，默认 30000"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": "点击页面上匹配选择器的元素",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器或文本内容"},
                "timeout": {"type": "number", "description": "超时毫秒，默认 5000"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_type",
        "description": "在输入框中输入文本",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "输入框的 CSS 选择器"},
                "text": {"type": "string", "description": "要输入的文本"},
                "delay": {"type": "number", "description": "每个字符间的延迟毫秒，默认 0"},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "截取当前页面或指定元素的截图，返回 base64 编码",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "可选，截取特定元素"},
                "fullPage": {"type": "boolean", "description": "是否截取完整页面，默认 false"},
            },
        },
    },
    {
        "name": "browser_extract_text",
        "description": "提取页面可见文本内容",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "可选，仅提取匹配元素的文本"},
                "maxLength": {"type": "number", "description": "最大字符数，默认 50000"},
            },
        },
    },
    {
        "name": "browser_extract_links",
        "description": "提取页面上所有链接（href + 文本）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "可选，仅提取指定区域的链接"},
                "maxLinks": {"type": "number", "description": "最大链接数，默认 200"},
            },
        },
    },
    {
        "name": "browser_extract_html",
        "description": "提取页面或元素的 HTML 源码",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "可选 CSS 选择器"},
                "maxLength": {"type": "number", "description": "最大字符数，默认 100000"},
            },
        },
    },
    {
        "name": "browser_evaluate",
        "description": "在浏览器中执行 JavaScript 代码",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "JavaScript 代码"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "browser_wait",
        "description": "等待元素出现或指定时间",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "等待此选择器的元素出现"},
                "timeout": {"type": "number", "description": "超时毫秒，默认 10000"},
            },
        },
    },
    {
        "name": "browser_scroll",
        "description": "滚动页面",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "description": "滚动方向: down, up, top, bottom", "enum": ["down", "up", "top", "bottom"]},
                "amount": {"type": "number", "description": "滚动像素（仅 down/up），默认 500"},
            },
        },
    },
    {
        "name": "browser_get_url",
        "description": "获取当前页面 URL",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_get_title",
        "description": "获取当前页面标题",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_close",
        "description": "关闭浏览器实例（释放资源）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


# ═══════════════════════════════════════════════════════════════
# 工具执行
# ═══════════════════════════════════════════════════════════════

def execute_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """执行指定的浏览器工具。"""

    if name == "browser_close":
        browser_mgr.close()
        return {"result": "浏览器已关闭"}

    # 确保浏览器可用
    page = browser_mgr.ensure_browser()

    if name == "browser_navigate":
        url = args["url"]
        if not url.startswith(ALLOWED_PROTOCOLS):
            return {"error": f"不支持的协议: {url}，仅允许 HTTP/HTTPS"}
        timeout = args.get("timeout", DEFAULT_TIMEOUT_MS)
        response = page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        return {
            "url": page.url,
            "title": page.title(),
            "status": response.status if response else None,
            "ok": response.ok if response else None,
        }

    elif name == "browser_click":
        selector = args["selector"]
        timeout = args.get("timeout", 5000)
        # 先尝试标准选择器
        try:
            page.click(selector, timeout=timeout)
        except Exception:
            # 回退：尝试按文本匹配
            try:
                page.get_by_text(selector, exact=False).first.click(timeout=timeout)
            except Exception as e:
                return {"error": f"无法点击 '{selector}': {str(e)}"}
        return {"result": f"已点击 '{selector}'", "url": page.url}

    elif name == "browser_type":
        selector = args["selector"]
        text = args["text"]
        delay = args.get("delay", 0)
        try:
            page.fill(selector, "")
            page.type(selector, text, delay=delay)
        except Exception as e:
            return {"error": f"输入失败: {str(e)}"}
        return {"result": f"已在 '{selector}' 输入 {len(text)} 个字符"}

    elif name == "browser_screenshot":
        selector = args.get("selector")
        full_page = args.get("fullPage", False)
        options = {"full_page": full_page, "type": "png"}
        if selector:
            try:
                el = page.locator(selector).first
                data = el.screenshot(**options)
            except Exception as e:
                return {"error": f"截图失败: {str(e)}"}
        else:
            data = page.screenshot(**options)

        import base64
        return {
            "screenshot": base64.b64encode(data).decode("utf-8"),
            "format": "png",
            "size_bytes": len(data),
        }

    elif name == "browser_extract_text":
        selector = args.get("selector")
        max_len = args.get("maxLength", 50000)
        try:
            if selector:
                el = page.locator(selector).first
                text = el.inner_text()
            else:
                text = page.locator("body").inner_text()
            if len(text) > max_len:
                text = text[:max_len] + f"\n... [截断，共 {len(text)} 字符]"
            return {"text": text, "length": len(text)}
        except Exception as e:
            return {"error": str(e)}

    elif name == "browser_extract_links":
        selector = args.get("selector")
        max_links = args.get("maxLinks", 200)
        try:
            container = page.locator(selector).first if selector else page.locator("body")
            links = container.locator("a[href]").all()
            result = []
            for link in links[:max_links]:
                try:
                    href = link.get_attribute("href")
                    text = link.inner_text().strip()[:200]
                    if href and not href.startswith("javascript:"):
                        result.append({"href": href, "text": text})
                except Exception:
                    continue
            return {"links": result, "count": len(result)}
        except Exception as e:
            return {"error": str(e)}

    elif name == "browser_extract_html":
        selector = args.get("selector")
        max_len = args.get("maxLength", 100000)
        try:
            if selector:
                el = page.locator(selector).first
                html = el.inner_html()
            else:
                html = page.content()
            if len(html) > max_len:
                html = html[:max_len] + f"\n<!-- 截断，共 {len(html)} 字符 -->"
            return {"html": html, "length": len(html)}
        except Exception as e:
            return {"error": str(e)}

    elif name == "browser_evaluate":
        code = args["code"]
        try:
            result = page.evaluate(code)
            return {"result": str(result) if result is not None else "undefined"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "browser_wait":
        selector = args.get("selector")
        timeout = args.get("timeout", 10000)
        if selector:
            try:
                page.wait_for_selector(selector, timeout=timeout)
                return {"result": f"元素 '{selector}' 已出现"}
            except PlaywrightTimeout:
                return {"error": f"等待超时: '{selector}' 未在 {timeout}ms 内出现"}
        else:
            page.wait_for_timeout(timeout)
            return {"result": f"已等待 {timeout}ms"}

    elif name == "browser_scroll":
        direction = args.get("direction", "down")
        amount = args.get("amount", 500)
        if direction == "down":
            page.evaluate(f"window.scrollBy(0, {amount})")
        elif direction == "up":
            page.evaluate(f"window.scrollBy(0, -{amount})")
        elif direction == "top":
            page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return {"result": f"已{direction}滚动"}

    elif name == "browser_get_url":
        return {"url": page.url}

    elif name == "browser_get_title":
        return {"title": page.title()}

    else:
        return {"error": f"未知工具: {name}"}


# ═══════════════════════════════════════════════════════════════
# 主循环: JSON-RPC 2.0 over stdio
# ═══════════════════════════════════════════════════════════════

def handle_request(request: dict):
    """处理单个 JSON-RPC 请求。"""
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    try:
        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "capabilities": {
                    "tools": {},
                },
            }
            send_response(req_id, result)

        elif method == "tools/list":
            send_response(req_id, {"tools": TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            log(f"调用工具: {tool_name}")
            try:
                result = execute_tool(tool_name, tool_args)
                send_response(req_id, {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                    ]
                })
            except Exception as e:
                log(f"工具执行错误: {e}")
                traceback.print_exc(file=sys.stderr)
                send_response(req_id, {
                    "content": [
                        {"type": "text", "text": json.dumps({"error": str(e)}, ensure_ascii=False)}
                    ],
                    "isError": True,
                })

        elif method == "notifications/initialized":
            # 客户端确认初始化完成，无需响应
            pass

        elif method == "ping":
            send_response(req_id, {})

        else:
            send_response(req_id, error={
                "code": -32601,
                "message": f"未知方法: {method}",
            })

    except Exception as e:
        log(f"处理错误: {e}")
        traceback.print_exc(file=sys.stderr)
        send_response(req_id, error={
            "code": -32603,
            "message": str(e),
        })


def main():
    """主循环：从 stdin 逐行读取 JSON-RPC 请求。"""
    log(f"Browser Harness MCP Server v{SERVER_VERSION} 启动")
    log(f"Playwright: {'可用' if HAS_PLAYWRIGHT else '不可用'}")

    if not HAS_PLAYWRIGHT:
        log("错误: Playwright 未安装，无法启动")
        sys.exit(1)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                handle_request(request)
            except json.JSONDecodeError as e:
                log(f"JSON 解析错误: {e}")
                send_response(None, error={
                    "code": -32700,
                    "message": f"Parse error: {str(e)}",
                })
    except KeyboardInterrupt:
        pass
    finally:
        browser_mgr.close()
        log("已关闭")


if __name__ == "__main__":
    main()
