#!/usr/bin/env python3
"""TraceLogger — 单元测试"""
import os, sys
os.environ["LOG_TO_REDIS"] = "0"
from trace_logger import TraceLogger

log = TraceLogger("TestModule")
log.info("test", "测试", trace_id="t1", key="val")
log.warn("test", "警告", trace_id="t1", code=500)
log.error("test", "错误", trace_id="t1", error="boom")

# Verify output was produced (no crash = pass)
print("✅ TraceLogger tests passed")
