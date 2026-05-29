
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
MCP Protocol v1.0 — JSON-RPC 2.0 标准实现
==========================================
Commander 五层架构的通信基础。每层作为 MCP Server 暴露工具接口，
层间通过 JSON-RPC 2.0 协议调用。

标准方法:
  initialize     — 握手，交换能力信息
  tools/list     — 列出本层提供的工具
  tools/call     — 调用工具
  health/ping    — 健康检查

格式:
  请求: {"jsonrpc":"2.0","method":"tools/call","id":1,"params":{"name":"analyze","arguments":{...}}}
  响应: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"..."}]}}
  错误: {"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"..."}}
"""

import json
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════
# MCP Server 基类
# ═══════════════════════════════════════════════════════════════

class MCPServer:
    """MCP JSON-RPC 2.0 Server 基类。

    每层继承此类，注册自己的工具，即可作为 MCP Server 运行。

    使用:
        class PerceptionServer(MCPServer):
            def __init__(self):
                super().__init__("perception", "L1 Perception Layer")
                self.register_tool("analyze_intent", self.analyze_intent)

            def analyze_intent(self, text: str) -> dict:
                return {"intents": ["translate"], "confidence": 0.9}
    """

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self._tools: Dict[str, Callable] = {}
        self._started_at = time.time()

        # 注册内置工具
        self.register_tool("ping", self._ping)

    def register_tool(self, tool_name: str, handler: Callable):
        """注册一个工具。"""
        self._tools[tool_name] = handler

    def list_tools(self) -> List[dict]:
        """列出所有工具。"""
        return [
            {"name": name, "description": handler.__doc__ or ""}
            for name, handler in self._tools.items()
        ]

    def handle_request(self, request: dict) -> dict:
        """处理 JSON-RPC 2.0 请求。"""
        rpc_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})

        try:
            if method == "initialize":
                return self._make_response(rpc_id, {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": self.name, "description": self.description},
                    "capabilities": {"tools": {}},
                })

            elif method == "tools/list":
                return self._make_response(rpc_id, {"tools": self.list_tools()})

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                return self._call_tool(rpc_id, tool_name, arguments)

            elif method == "health/ping":
                return self._make_response(rpc_id, {"status": "ok", "uptime": time.time() - self._started_at})

            else:
                return self._make_error(rpc_id, -32601, f"Unknown method: {method}")

        except Exception as e:
            return self._make_error(rpc_id, -32603, str(e))

    def _call_tool(self, rpc_id, tool_name: str, arguments: dict) -> dict:
        """调用注册的工具。"""
        if tool_name not in self._tools:
            return self._make_error(rpc_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = self._tools[tool_name](**arguments)
            return self._make_response(rpc_id, {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}]
            })
        except Exception as e:
            return self._make_error(rpc_id, -32000, f"Tool error: {e}")

    def _ping(self) -> dict:
        """Health check."""
        return {"status": "ok", "layer": self.name, "uptime_s": int(time.time() - self._started_at)}

    @staticmethod
    def _make_response(rpc_id, result) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    @staticmethod
    def _make_error(rpc_id, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


# ═══════════════════════════════════════════════════════════════
# MCP HTTP Handler
# ═══════════════════════════════════════════════════════════════

class MCPRequestHandler(BaseHTTPRequestHandler):
    """MCP JSON-RPC over HTTP 请求处理器。"""

    server_instance: MCPServer = None

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            request = json.loads(body)
        except json.JSONDecodeError:
            self._send(400, {"error": "Invalid JSON"})
            return

        response = self.server_instance.handle_request(request)

        # 通知（无 id）不需要响应
        if request.get("id") is None and "result" not in response:
            self._send(202, {})
            return

        self._send(200, response)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"status": "ok", "layer": self.server_instance.name})
        else:
            self._send(200, {
                "layer": self.server_instance.name,
                "description": self.server_instance.description,
                "tools": self.server_instance.list_tools(),
            })

    def _send(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())


def create_mcp_http_server(layer_server: MCPServer, port: int) -> HTTPServer:
    """创建 MCP HTTP 服务器。"""
    handler = type("Handler", (MCPRequestHandler,), {"server_instance": layer_server})
    server = HTTPServer(("127.0.0.1", port), handler)
    return server


# ═══════════════════════════════════════════════════════════════
# MCP Client
# ═══════════════════════════════════════════════════════════════

class MCPClient:
    """MCP JSON-RPC 2.0 客户端 — 调用其他层的工具。"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._rpc_id = 0

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def call(self, method: str, params: dict = None) -> dict:
        """调用 MCP 方法。"""
        import urllib.request

        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._next_id(),
            "params": params or {},
        }

        req = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def call_tool(self, tool_name: str, arguments: dict = None) -> dict:
        """快捷方法：调用工具。"""
        result = self.call("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })
        if "result" in result:
            content = result["result"].get("content", [])
            if content and isinstance(content, list):
                try:
                    return json.loads(content[0].get("text", "{}"))
                except (json.JSONDecodeError, IndexError):
                    return content[0]
        return result

    def health(self) -> dict:
        """健康检查。"""
        return self.call("health/ping")


# ═══════════════════════════════════════════════════════════════
# MCP Hub — 服务注册与发现
# ═══════════════════════════════════════════════════════════════

class MCPHub:
    """MCP Hub: 管理五层服务的注册、发现和路由。"""

    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._status: Dict[str, dict] = {}

    def register_layer(self, name: str, url: str):
        """注册一层服务。"""
        self._clients[name] = MCPClient(url)

    def get_client(self, layer: str) -> Optional[MCPClient]:
        """获取指定层的客户端。"""
        return self._clients.get(layer)

    def health_check_all(self) -> dict:
        """全层健康检查。"""
        result = {}
        for name, client in self._clients.items():
            try:
                h = client.health()
                result[name] = "ok" if "result" in h else "error"
            except Exception:
                result[name] = "unreachable"
        return result

    def list_all_tools(self) -> dict:
        """列出所有层的工具。"""
        tools = {}
        for name, client in self._clients.items():
            try:
                result = client.call("tools/list")
                tools[name] = result.get("result", {}).get("tools", [])
            except Exception:
                tools[name] = []
        return tools


# ═══════════════════════════════════════════════════════════════
# 启动辅助
# ═══════════════════════════════════════════════════════════════

def run_mcp_server(layer_name: str, layer_server: MCPServer, port: int):
    """启动 MCP Server 并阻塞。"""
    server = create_mcp_http_server(layer_server, port)
    print(f"[MCP] {layer_name} Server running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
