# Yaxiio v1.1 - AGPLv3
"""MCP Bridge — unified MCPClient call interface"""
import json, time, os
from mcp.protocol import MCPClient

MCP_HOST = os.environ.get("MCP_HOST", "localhost")
MCP_CLIENTS = {i: MCPClient(f"http://{MCP_HOST}:{3400+i}") for i in range(1, 6)}

def call_layer(layer: int, method: str, **kwargs) -> dict:
    """Call an MCP tool on a layer via MCPClient (standard protocol)."""
    client = MCP_CLIENTS.get(layer)
    if not client:
        return {"error": f"Invalid layer: {layer}"}
    for attempt in range(3):
        try:
            result = client.call_tool(method, kwargs)
            if isinstance(result, dict) and "error" not in result:
                return result
            if attempt == 2:
                return result if isinstance(result, dict) else {"error": str(result)[:200]}
        except Exception as e:
            if attempt == 2:
                return {"error": str(e)[:200]}
        time.sleep(0.3)
    return {"error": "max retries"}


