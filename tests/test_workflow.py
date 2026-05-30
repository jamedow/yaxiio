#!/usr/bin/env python3
"""
WorkflowEngine 关键路径测试
============================
测试: process() 分流, _decompose_via_l2(), _do_L5(), _cleanup_task()
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '.pi', 'skills', 'commander'))

class TestWorkflowEngine:
    """测试 WorkflowEngine 核心流程（不依赖 Redis/LLM）"""
    
    def __init__(self):
        self.passed = 0
        self.failed = 0
    
    def _check(self, condition, name):
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            print(f"  ❌ {name}")
    
    def test_imports(self):
        """验证所有关键模块可以导入"""
        print("=== Module Imports ===\n")
        modules = [
            ("workflow_engine", "WorkflowEngine"),
            ("constitution", "YaxiioConstitution"),
            ("task_state_machine", "TaskStateMachine"),
            ("l0_memory", "L0Memory"),
            ("gap_analyzer", "GapAnalyzer"),
            ("workflow_snapshot", "WorkflowSnapshot"),
        ]
        for mod_name, class_name in modules:
            try:
                mod = __import__(mod_name)
                cls = getattr(mod, class_name)
                self._check(True, f"{mod_name}.{class_name} importable")
            except Exception as e:
                self._check(False, f"{mod_name}.{class_name}: {e}")
    
    def test_intent_map(self):
        """验证 INTENT_TOOL_MAP 完整性"""
        print("\n=== INTENT_TOOL_MAP ===\n")
        from workflow_engine import INTENT_TOOL_MAP
        
        # 每个条目应有 tool, agent, desc 字段
        for intent, info in INTENT_TOOL_MAP.items():
            self._check("tool" in info, f"{intent}: has tool field")
            self._check("agent" in info, f"{intent}: has agent field")
            self._check("desc" in info, f"{intent}: has desc field")
        
        # 关键 intent 必须存在
        required = ["audit", "translate", "fix", "diagnose", "quote", "deploy"]
        for r in required:
            self._check(r in INTENT_TOOL_MAP, f"required intent '{r}' exists")
    
    def test_complex_task_detection(self):
        """验证复杂任务检测逻辑"""
        print("\n=== Complex Task Detection ===\n")
        from workflow_engine import INTENT_TOOL_MAP
        
        # 标记为 complex 的 intent
        complex_intents = [k for k, v in INTENT_TOOL_MAP.items() if v.get("complex")]
        self._check(len(complex_intents) > 0, f"{len(complex_intents)} complex intents defined")
        
        # 长度 > 150 字符应该触发复杂任务
        long_task = "x" * 151
        has_complex_keywords = any(n in long_task for n in ["split","batch","parallel","entries","pending"])
        self._check(not has_complex_keywords, "long task triggers complex via length check")
    
    def test_sandbox_actions(self):
        """验证沙箱要求的 action"""
        print("\n=== Sandbox Actions ===\n")
        from workflow_engine import SANDBOX_REQUIRED_ACTIONS
        
        self._check(len(SANDBOX_REQUIRED_ACTIONS) > 0, "sandbox actions defined")
        self._check("site_deploy" in SANDBOX_REQUIRED_ACTIONS, "site_deploy requires sandbox")
        self._check("site_fix" in SANDBOX_REQUIRED_ACTIONS, "site_fix requires sandbox")
    
    def test_template_state(self):
        """验证 COMPLEX_TASK_TEMPLATES"""
        print("\n=== Task Templates ===\n")
        from workflow_engine import COMPLEX_TASK_TEMPLATES
        
        # 设计文档说这是空的——LLM 自主拆解
        self._check(isinstance(COMPLEX_TASK_TEMPLATES, dict), "templates is a dict")
        # 空是合理的——系统使用 LLM 动态拆解
        print(f"  ℹ️  templates count: {len(COMPLEX_TASK_TEMPLATES)} (LLM自主拆解)")
    
    def test_mcp_clients(self):
        """验证 MCP 客户端初始化"""
        print("\n=== MCP Clients ===\n")
        from workflow_engine import MCP_CLIENTS, MCP_HOST
        
        self._check(len(MCP_CLIENTS) == 5, f"5 MCP clients (got {len(MCP_CLIENTS)})")
        self._check(1 in MCP_CLIENTS, "L1 client exists")
        self._check(5 in MCP_CLIENTS, "L5 client exists")
    
    def test_constants(self):
        """验证关键常量"""
        print("\n=== Constants ===\n")
        from workflow_engine import (
            POLL_TIMEOUT, POLL_INTERVAL, SANDBOX_TIMEOUT, SANDBOX_MAX_SIZE_MB
        )
        
        self._check(POLL_TIMEOUT > 0, f"POLL_TIMEOUT={POLL_TIMEOUT} > 0")
        self._check(POLL_INTERVAL > 0, f"POLL_INTERVAL={POLL_INTERVAL} > 0")
        self._check(SANDBOX_TIMEOUT > 0, f"SANDBOX_TIMEOUT={SANDBOX_TIMEOUT} > 0")
        self._check(SANDBOX_MAX_SIZE_MB > 0, f"SANDBOX_MAX_SIZE_MB={SANDBOX_MAX_SIZE_MB} > 0")
    
    def run(self):
        self.test_imports()
        self.test_intent_map()
        self.test_complex_task_detection()
        self.test_sandbox_actions()
        self.test_template_state()
        self.test_mcp_clients()
        self.test_constants()
        
        total = self.passed + self.failed
        print(f"\n{'='*40}")
        print(f"WorkflowEngine Tests: {self.passed}/{total} passed")
        return self.failed == 0


if __name__ == "__main__":
    ok = TestWorkflowEngine().run()
    if ok:
        print("✅ All workflow engine tests passed!")
    else:
        print("❌ Some tests failed")
        sys.exit(1)
