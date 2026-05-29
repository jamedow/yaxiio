#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
⚠️  DEPRECATED: 此文件已重命名为 gateway.py

请改用:
  from gateway import CommanderV3

此包装器仅为向后兼容保留，将在 v0.3 移除。
"""
import warnings
warnings.warn(
    "commander_v3.py 已废弃，请改用 gateway.py。此包装器将在 v0.3 移除。",
    DeprecationWarning,
    stacklevel=2,
)

from gateway import *  # noqa: E402, F403
