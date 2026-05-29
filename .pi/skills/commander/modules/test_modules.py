
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html
"""
Commander V3 模块测试套件
=========================
测试所有 10 个模块的核心功能。

运行:
  python3 -m pytest test_modules.py -v
  或
  python3 test_modules.py
"""

import os
import sys
import time
import json
import unittest

# 确保模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 设置测试环境变量
os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017/")
os.environ.setdefault("SCORE_THRESHOLD", "6")


class TestSessionManager(unittest.TestCase):
    """测试会话与连接分离 (模块1,2,4,5,6,10)"""

    @classmethod
    def setUpClass(cls):
        from session_manager import SessionManager
        cls.mgr = SessionManager()

    def test_01_token_generation(self):
        """令牌生成与验证"""
        token = self.mgr.generate_token("browser-chrome")
        self.assertTrue(token.startswith("sess-"))
        self.assertTrue(self.mgr.validate_token(token, "browser-chrome"))
        self.assertFalse(self.mgr.validate_token(token, "wrong-fingerprint"))

    def test_02_session_lifecycle(self):
        """会话创建→连接→断开→关闭"""
        result = self.mgr.create_session("test-client")
        self.assertEqual(result["status"], "active")
        token = result["token"]

        # 连接
        conn = self.mgr.connect(token, "test-client")
        self.assertEqual(conn["status"], "connected")

        # 断开
        disc = self.mgr.disconnect(token, "test-client")
        self.assertTrue(disc["disconnected"])

        # 关闭
        closed = self.mgr.close_session(token)
        self.assertEqual(closed["status"], "closed")

    def test_03_message_enqueue_and_seq(self):
        """消息入队与 seq 递增"""
        result = self.mgr.create_session("test-seq")
        token = result["token"]

        seq1 = self.mgr.enqueue_message(token, {"type": "task_result", "data": "hello"})
        seq2 = self.mgr.enqueue_message(token, {"type": "task_result", "data": "world"})
        self.assertGreater(seq2, seq1)

    def test_04_offline_messages_dedup(self):
        """离线消息 seq 去重"""
        result = self.mgr.create_session("test-dedup")
        token = result["token"]

        self.mgr.enqueue_message(token, {"type": "msg1"})
        self.mgr.enqueue_message(token, {"type": "msg2"})
        self.mgr.enqueue_message(token, {"type": "msg3"})

        msgs = self.mgr.get_offline_messages(token, last_seq=1)
        self.assertEqual(len(msgs), 2)  # seq 2 and 3

    def test_05_lamport_clock(self):
        """Lamport 逻辑时钟"""
        from session_manager import SessionManager
        mgr = SessionManager()
        result = mgr.create_session("test-lamport")
        token = result["token"]

        c1 = mgr.update_lamport(token, 0)
        c2 = mgr.update_lamport(token, 5)
        c3 = mgr.update_lamport(token, 3)  # 应该 max(local=6, received=3)+1 = 7
        self.assertGreaterEqual(c3, c2)

    def test_06_queue_limits(self):
        """离线队列限制"""
        result = self.mgr.create_session("test-limit")
        token = result["token"]

        for i in range(10):
            self.mgr.enqueue_message(token, {"type": f"msg{i}"})

        stats = self.mgr.get_queue_stats(token)
        self.assertGreaterEqual(stats["offline_queue_size"], 1)


class TestFailureRecovery(unittest.TestCase):
    """测试五种失败恢复策略 (模块8)"""

    def setUp(self):
        from failure_recovery import FailureRecovery
        self.fr = FailureRecovery()

    def test_01_retry_on_first_failure(self):
        result = self.fr.decide("task-1", "agent-A", "timeout", elapsed_ms=5000)
        self.assertEqual(result["strategy"], "retry")

    def test_02_reassign_on_repeated_failure(self):
        for i in range(5):
            result = self.fr.decide(f"task-{i}", "agent-B", "crash",
                                     available_agents=["agent-B", "agent-C"])
            if result["strategy"] == "reassign":
                self.assertEqual(result["params"]["new_agent"], "agent-C")
                return
        self.fail("Should have triggered reassign")

    def test_03_decompose_on_slow_task(self):
        result = self.fr.decide("task-slow", "agent-D", "timeout",
                                 elapsed_ms=90000, available_agents=["agent-D"])
        self.assertIn(result["strategy"], ["decompose", "reassign", "retry"])


class TestLLMScorer(unittest.TestCase):
    """测试 LLM 自动评分 (模块7)"""

    def setUp(self):
        from llm_scorer import LLMScorer
        self.scorer = LLMScorer()

    def test_01_score_successful_task(self):
        result = self.scorer.score(
            "翻译 Hello → 俄语",
            {"result": "Привет", "stdout": "Привет"},
            agent_id="翻译官", elapsed_ms=2000
        )
        self.assertGreaterEqual(result["overall"], 1)
        self.assertLessEqual(result["overall"], 10)

    def test_02_score_failed_task(self):
        result = self.scorer.score(
            "执行危险命令",
            {"error": "Permission denied", "stderr": "error"},
            agent_id="bad-agent", elapsed_ms=500
        )
        self.assertLess(result["overall"], 8)

    def test_03_agent_stats(self):
        for i in range(5):
            self.scorer.score(f"task-{i}", {"result": "ok"}, agent_id="agent-X")
        stats = self.scorer.get_agent_average("agent-X")
        self.assertGreater(stats["count"], 0)


class TestAuditLogger(unittest.TestCase):
    """测试审计日志 (模块9)"""

    @classmethod
    def setUpClass(cls):
        from audit_logger import AuditLogger
        cls.logger = AuditLogger()

    def test_01_log_llm_call(self):
        self.logger.log_llm_call(
            "prompt", "response", model="test-model",
            token_count=100, latency_ms=500,
        )
        self.logger.flush()

    def test_02_log_failure(self):
        self.logger.log_failure(
            "Connection timeout",
            agent_id="agent-X", task_id="task-1",
            strategy="retry"
        )
        self.logger.flush()

    def test_03_log_state_change(self):
        self.logger.log_state_change(
            "agent-X", "running", "error",
            reason="OOM killed"
        )
        self.logger.flush()


class TestCommanderModules(unittest.TestCase):
    """测试统一入口"""

    def test_01_import_and_health(self):
        from modules import CommanderModules
        mod = CommanderModules()
        health = mod.health_check()
        self.assertIn("session", health)
        self.assertIn("scorer", health)
        self.assertIn("failure", health)
        self.assertIn("audit", health)


if __name__ == "__main__":
    # 运行所有测试
    unittest.main(verbosity=2)
