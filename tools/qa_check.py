#!/usr/bin/env python3
"""
Yaxiio Quality Check Tool
==========================
自动检测代码/文档/配置的质量，按 QUALITY_CONSTITUTION.md 标准评分。

用法:
  python3 tools/qa_check.py --changed        # 只检查变更文件
  python3 tools/qa_check.py --all             # 全量检查
  python3 tools/qa_check.py --file path.py    # 指定文件
  python3 tools/qa_check.py --type image      # 按类型过滤

退出码: 0=全部通过, 1=有警告, 2=有错误
"""
import os, sys, re, json, subprocess
from pathlib import Path

# ═══ 配置 ═══
PASS_THRESHOLD = 6.0
WARN_THRESHOLD = 4.0

# ═══ 检测器 ═══

def check_code(filepath: str) -> dict:
    """检测代码文件质量"""
    with open(filepath) as f:
        lines = f.readlines()
        content = "".join(lines)
    
    total = len(lines)
    issues = []
    
    # 1. 文件大小
    if total > 500:
        issues.append({"severity": "warning", "rule": "file_size",
                       "message": f"文件 {total} 行 (阈值 500)", "fix": "拆分为多个模块"})
    
    # 2. 函数长度
    func_starts = []
    for i, line in enumerate(lines):
        if line.strip().startswith("def ") and not line.strip().startswith("def __"):
            func_starts.append(i)
    
    long_funcs = []
    for j, start in enumerate(func_starts):
        end = func_starts[j+1] if j+1 < len(func_starts) else total
        length = end - start
        if length > 40:
            name = lines[start].strip().split("(")[0].replace("def ", "")
            long_funcs.append((name, length))
    
    if long_funcs:
        for name, length in long_funcs[:3]:
            issues.append({"severity": "warning", "rule": "func_length",
                          "message": f"函数 '{name}' {length} 行 (阈值 40)", "fix": f"拆分 {name}"})
    
    # 3. 圈复杂度 (简化为 if/for/while 计数)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("def "):
            func_name = stripped.split("(")[0].replace("def ", "")
            # Count branches until next def or dedent
            branches = 0
            for j in range(i+1, min(i+100, total)):
                sl = lines[j].strip()
                if sl.startswith("def "):
                    break
                if any(sl.startswith(kw) for kw in ["if ", "for ", "while ", "elif ", "except "]):
                    branches += 1
            if branches > 10:
                issues.append({"severity": "warning", "rule": "complexity",
                              "message": f"'{func_name}' 分支数 {branches} (阈值 10)", "fix": f"拆分 {func_name}"})
    
    # 4. 注释率
    comment_lines = sum(1 for l in lines if l.strip().startswith("#") or '"""' in l or "'''" in l)
    blank_lines = sum(1 for l in lines if not l.strip())
    code_lines = total - blank_lines - comment_lines
    comment_ratio = comment_lines / max(total, 1)
    if comment_ratio < 0.05:
        issues.append({"severity": "info", "rule": "comments",
                      "message": f"注释率 {comment_ratio:.1%} (建议 ≥5%)", "fix": "添加模块和函数注释"})
    
    # 5. 单字母变量
    single_letter = len(re.findall(r'\b[a-zA-Z]\b(?=\s*[=,\]\\)])', content))
    if single_letter > 3:
        issues.append({"severity": "info", "rule": "naming",
                      "message": f"{single_letter} 个单字母变量", "fix": "使用描述性变量名"})
    
    # Calculate score
    score = 10.0
    for issue in issues:
        if issue["severity"] == "error": score -= 2.0
        elif issue["severity"] == "warning": score -= 1.0
        else: score -= 0.3
    score = max(0, round(score, 1))
    
    return {"file": filepath, "type": "code", "score": score, "lines": total,
            "issues": issues, "passed": score >= PASS_THRESHOLD}


def check_config(filepath: str) -> dict:
    """检测配置文件质量"""
    with open(filepath) as f:
        content = f.read()
    
    issues = []
    try:
        data = json.load(f)
    except:
        return {"file": filepath, "type": "config", "score": 0, "issues": [
            {"severity": "error", "rule": "parse", "message": "JSON 解析失败"}]}
    
    # Check for Chinese in filenames
    if any('\u4e00' <= c <= '\u9fff' for c in os.path.basename(filepath)):
        issues.append({"severity": "warning", "rule": "slug",
                      "message": "文件名包含中文，应使用英文 slug", "fix": "重命名为英文"})
    
    # Check for empty values
    empty_count = 0
    for k, v in data.items():
        if isinstance(v, str) and not v.strip():
            empty_count += 1
        elif isinstance(v, (list, dict)) and len(v) == 0:
            empty_count += 1
    
    if empty_count > len(data) * 0.2:
        issues.append({"severity": "warning", "rule": "empty_values",
                      "message": f"{empty_count} 个空值", "fix": "填充必要内容或移除空字段"})
    
    score = 10.0 - len(issues) * 1.5
    score = max(0, round(score, 1))
    
    return {"file": filepath, "type": "config", "score": score, "issues": issues,
            "passed": score >= PASS_THRESHOLD}


def check_document(filepath: str) -> dict:
    """检测文档质量"""
    with open(filepath) as f:
        lines = f.readlines()
    content = "".join(lines)
    total = len(lines)
    issues = []
    
    # Check for essential sections based on doc type
    basename = os.path.basename(filepath).lower()
    
    if "design" in basename or "architecture" in basename:
        required = ["架构", "architecture", "设计", "design"]
        found = sum(1 for r in required if r.lower() in content.lower())
        if found < 2:
            issues.append({"severity": "warning", "rule": "structure",
                          "message": "设计文档缺少架构/设计相关章节"})
    
    # Word count per section
    sections = re.split(r'^##\s', content, flags=re.MULTILINE)
    thin_sections = [s[:30] for s in sections[1:] if len(s) < 100]
    if thin_sections:
        issues.append({"severity": "info", "rule": "content_depth",
                      "message": f"{len(thin_sections)} 个章节内容过短 (<100字)", 
                      "fix": "补充详细说明"})
    
    score = 10.0 - len(issues) * 1.0
    score = max(0, round(score, 1))
    
    return {"file": filepath, "type": "document", "score": score, "issues": issues,
            "passed": score >= PASS_THRESHOLD}


# ═══ 主流程 ═══

def detect_type(filepath: str) -> str:
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".py", ".ts", ".js", ".vue"): return "code"
    if ext in (".json", ".yaml", ".yml"): return "config"
    if ext in (".md", ".txt"): return "document"
    return "unknown"

def main():
    import argparse
    p = argparse.ArgumentParser(description="Yaxiio Quality Check")
    p.add_argument("--changed", action="store_true", help="Check git changed files")
    p.add_argument("--all", action="store_true", help="Check all project files")
    p.add_argument("--file", help="Check specific file")
    p.add_argument("--type", help="Filter by type: code/config/document")
    p.add_argument("--threshold", type=float, default=PASS_THRESHOLD, help="Pass threshold")
    args = p.parse_args()
    
    files = []
    if args.file:
        files = [args.file]
    elif args.changed:
        result = subprocess.run(["git", "diff", "--name-only", "HEAD"], 
                               capture_output=True, text=True)
        files = [f for f in result.stdout.strip().split("\n") if f]
    elif args.all:
        for root, _, fs in os.walk("."):
            if "node_modules" in root or ".git" in root or "__pycache__" in root:
                continue
            for fn in fs:
                fp = os.path.join(root, fn)
                if detect_type(fp) != "unknown":
                    files.append(fp)
    else:
        p.print_help()
        return
    
    if not files:
        print("No files to check.")
        return
    
    results = []
    for fp in files:
        if not os.path.exists(fp):
            continue
        t = detect_type(fp)
        if args.type and t != args.type:
            continue
        
        if t == "code":
            r = check_code(fp)
        elif t == "config":
            r = check_config(fp)
        elif t == "document":
            r = check_document(fp)
        else:
            continue
        results.append(r)
    
    # Output
    passed = 0; warned = 0; failed = 0
    for r in sorted(results, key=lambda x: x["score"]):
        icon = "✅" if r["score"] >= PASS_THRESHOLD else ("⚠️" if r["score"] >= WARN_THRESHOLD else "🔴")
        print(f"{icon} {r['file']}: {r['score']}/10 ({r['type']})")
        for issue in r.get("issues", []):
            print(f"   [{issue['rule']}] {issue['message']}")
            if issue.get("fix"):
                print(f"   💡 {issue['fix']}")
        
        if r["score"] >= PASS_THRESHOLD: passed += 1
        elif r["score"] >= WARN_THRESHOLD: warned += 1
        else: failed += 1
    
    print(f"\n{passed} passed, {warned} warnings, {failed} failed")
    sys.exit(0 if failed == 0 else (1 if warned == 0 else 2))


if __name__ == "__main__":
    main()
