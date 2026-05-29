#!/usr/bin/env python3
# Yaxiio v0.2.6 — AGPLv3
"""
⚠️  DEPRECATED: 此文件已重命名为 commander.py

请改用:
  from commander import CommanderV2

此包装器仅为向后兼容保留，将在 v0.3 移除。
"""
import warnings
warnings.warn(
    "commander_v2.py 已废弃，请改用 commander.py。此包装器将在 v0.3 移除。",
    DeprecationWarning,
    stacklevel=2,
)

from commander import *  # noqa: E402, F403
