#!/usr/bin/env python3
"""Yaxiio AGPLv3 Release Sanitizer — 数据脱敏脚本"""
import os, sys, re, shutil
from pathlib import Path

ROOT = Path("/app/.pi/skills/commander")

RULES = [
    # (pattern, replacement, description)
    (r'sk-[a-zA-Z0-9]{20,}', '$DEEPSEEK_API_KEY', 'API Key'),
    (r'"Yaxiio2026"', '"$REDIS_PASSWORD"', 'Redis密码'),
    (r"'Yaxiio2026'", "'$REDIS_PASSWORD'", 'Redis密码'),
    (r'password="Yaxiio2026"', 'password=os.environ.get("REDIS_PASSWORD","")', 'Redis密码(关键字)'),
    (r'Zhangliang@520', '$SSH_PASSWORD', 'SSH密码'),
    (r'Lt@114514!', '$DB_PASSWORD', '数据库密码'),
    (r'172\.17\.0\.1', '$MONGO_HOST', 'MongoDB内网IP'),
    (r'47\.79\.20\.2', '$DEPLOY_HOST', '部署服务器IP'),
    (r'lightingmetal\.com', 'example.com', '域名'),
    (r'LightingMetal', 'ExampleCorp', '公司名'),
    (r'Lighting Metal', 'Example Corp', '品牌名'),
    (r'"lightingmetal"', '"example_db"', '数据库名'),
    (r"mongodb://172\.17\.0\.1:27017", 'os.environ.get("MONGO_URI","mongodb://localhost:27017")', 'MongoDB URI'),
    (r'sk-4cd70b4d104f404e977ca9b33d93d8b1', '$DEEPSEEK_API_KEY', '硬编码API Key'),
    (r'sk-7fc8fef282194bcd996c4bac97315067', '$DEEPSEEK_API_KEY', '硬编码API Key'),
    (r'sk-22BhHx41WDRZfujO9d14Dc28C7F2404b8773F9056b734358', '$LLM_API_KEY', '硬编码LLM Key'),
    (r'47\.79\.20\.2', '$DEPLOY_HOST', '服务器IP'),
]

LICENSE_HEADER = """# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
"""

def scan_files():
    """Scan for sensitive data"""
    found = []
    for py_file in ROOT.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            for pattern, _, desc in RULES:
                for match in re.finditer(pattern, content):
                    ctx = content[max(0,match.start()-20):match.end()+20].replace('\n',' ')
                    found.append((str(py_file.relative_to(ROOT)), match.start()+1, desc, match.group()[:40], ctx))
        except:
            pass
    return found

def sanitize(dry_run=True):
    """Apply sanitization rules"""
    changes = []
    for py_file in ROOT.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            original = content
            for pattern, replacement, desc in RULES:
                content = re.sub(pattern, replacement, content)
            if content != original:
                changes.append(str(py_file.relative_to(ROOT)))
                if not dry_run:
                    py_file.write_text(content)
        except Exception as e:
            print(f"  ERROR {py_file}: {e}")
    return changes

if __name__ == "__main__":
    dry_run = "--apply" not in sys.argv
    
    if dry_run:
        print("=== DRY RUN — 预览脱敏变更 ===\n")
    
    found = scan_files()
    if found:
        print(f"发现 {len(found)} 处敏感数据:\n")
        for fpath, line, desc, match, ctx in sorted(found):
            print(f"  {fpath}:{line} [{desc}] {match}")
            print(f"    → {ctx}")
            print()
    
    changes = sanitize(dry_run=dry_run)
    
    if dry_run:
        print(f"\n将修改 {len(changes)} 个文件:")
        for f in changes:
            print(f"  {f}")
        print(f"\n执行: python3 {__file__} --apply  来实际应用变更")
    else:
        print(f"\n已脱敏 {len(changes)} 个文件")
