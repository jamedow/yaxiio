"""MCP Server 注册与发现"""
class MCPRegistry:
    def __init__(self): self.servers = {}
    def register(self, name: str, command: str, args: list = None, env: dict = None):
        self.servers[name] = {"name":name,"command":command,"args":args or [],"env":env or {},"status":"registered"}
        return {"status":"success","server":name}
    def list_all(self): return list(self.servers.values())
    def get(self, name: str): return self.servers.get(name)
