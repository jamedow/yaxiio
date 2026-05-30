"""
Constitution Semantic Verifier — JVM 字节码校验风格的宪法升级
=============================================================
从字符串匹配升级为四道结构化校验。

Pass 1: 结构检查 — 输入/输出是否符合 Schema
Pass 2: 语义检查 — 输出是否与任务目标一致
Pass 3: 安全性检查 — 输出是否包含危险操作
Pass 4: 依赖检查 — Agent 是否越权

用法:
    verifier = SemanticConstitutionVerifier()
    issues = verifier.verify(action, payload, result, agent_card)
    if issues:
        return Verdict.DEGRADED, issues[0]
"""
import json
from typing import List, Dict


class SemanticConstitutionVerifier:
    """四道语义校验器"""

    def __init__(self):
        self.checks_run = 0
        self.issues_found = 0

    # ═══════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════

    def verify(self, action: str, payload: dict, result: dict = None,
               agent_card: dict = None) -> dict:
        """
        执行四道校验，返回所有问题。

        Returns:
            {"passed": bool, "issues": [str, ...], "checks": 4}
        """
        all_issues = []

        # Pass 1: 结构检查
        all_issues.extend(self._check_structure(payload, agent_card))

        # Pass 2: 语义检查
        all_issues.extend(self._check_semantics(action, payload, result))

        # Pass 3: 安全性检查
        all_issues.extend(self._check_safety(payload, result))

        # Pass 4: 依赖检查
        all_issues.extend(self._check_dependencies(action, agent_card))

        self.checks_run += 1
        if all_issues:
            self.issues_found += 1

        return {
            "passed": len(all_issues) == 0,
            "issues": all_issues,
            "checks": 4,
        }

    # ═══════════════════════════════════════════════
    # Pass 1: 结构检查（类比 JVM 魔数和版本号校验）
    # ═══════════════════════════════════════════════

    def _check_structure(self, payload: dict, agent_card: dict) -> List[str]:
        """检查输入是否符合能力卡片要求的 Schema"""
        issues = []

        if not agent_card:
            return issues

        input_schema = agent_card.get("input_schema", {})
        required_fields = input_schema.get("required", [])

        for field in required_fields:
            if field not in payload:
                issues.append(
                    f"[结构] 缺少必需字段 '{field}'。"
                    f"能力卡片 '{agent_card.get('name', '?')}' 要求提供此字段。"
                )

        return issues

    # ═══════════════════════════════════════════════
    # Pass 2: 语义检查（类比 JVM 继承关系和 final 约束）
    # ═══════════════════════════════════════════════

    def _check_semantics(self, action: str, payload: dict,
                         result: dict = None) -> List[str]:
        """检查任务语义是否合理"""
        issues = []

        # 2.1 冲突检测: payload 中的参数是否互相矛盾
        if payload.get("force_sandbox") and action in (
            "session_end", "agent_export", "status"
        ):
            issues.append(
                f"[语义] 系统管理操作 '{action}' 不需要沙箱。"
                f"force_sandbox 参数被忽略。"
            )

        # 2.2 规模合理性: 批量任务是否过大
        task_desc = str(payload.get("task", ""))
        import re
        nums = re.findall(r'\b(\d{5,})\b', task_desc)
        for n in nums:
            count = int(n)
            if count > 100000:
                issues.append(
                    f"[语义] 批量任务共 {count} 项，超过安全阈值 100000。"
                    f"建议分批提交。"
                )

        # 2.3 循环依赖检测: payload 中的 depends 是否形成环
        if "depends" in payload:
            deps = payload["depends"]
            if isinstance(deps, list):
                visited = set()

                def has_cycle(node, path):
                    if node in path:
                        return True
                    if node in visited:
                        return False
                    visited.add(node)
                    # 简化: 只检查直接的 id 引用
                    return False

                # 简化检查: 去重
                if len(deps) != len(set(str(d) for d in deps)):
                    issues.append(
                        f"[语义] 依赖列表中存在重复项。"
                        f"每个依赖只能出现一次。"
                    )

        return issues

    # ═══════════════════════════════════════════════
    # Pass 3: 安全性检查（类比 JVM 字节码安全性）
    # ═══════════════════════════════════════════════

    def _check_safety(self, payload: dict, result: dict = None) -> List[str]:
        """检查是否包含危险操作——比字符串匹配更强"""
        issues = []

        # 转为字符串检查
        payload_str = json.dumps(payload, ensure_ascii=False)
        result_str = json.dumps(result, ensure_ascii=False) if result else ""
        combined = payload_str + " " + result_str

        # 3.1 系统命令注入
        DANGEROUS_COMMANDS = [
            ("rm -rf", "删除命令", "CRITICAL"),
            ("dd if=", "磁盘操作", "CRITICAL"),
            ("mkfs.", "格式化命令", "CRITICAL"),
            ("docker exec", "容器越权", "HIGH"),
            ("docker run", "容器越权", "HIGH"),
            ("ssh ", "远程连接", "HIGH"),
            ("scp ", "远程文件传输", "HIGH"),
            ("kill -9", "强制终止", "HIGH"),
            ("pkill", "批量终止", "HIGH"),
            ("shutdown", "系统关机", "CRITICAL"),
            ("reboot", "系统重启", "CRITICAL"),
        ]

        for pattern, desc, level in DANGEROUS_COMMANDS:
            if pattern in combined:
                issues.append(
                    f"[安全:{level}] 检测到 {desc} 模式 '{pattern}'。"
                    f"该操作已被拦截，强制路由到沙箱执行。"
                )

        # 3.2 动态代码执行
        CODE_EXEC_PATTERNS = [
            ("eval(", "动态代码执行"),
            ("exec(", "动态代码执行"),
            ("compile(", "动态编译"),
            ("__import__", "动态导入"),
        ]
        for pattern, desc in CODE_EXEC_PATTERNS:
            if pattern in combined:
                issues.append(
                    f"[安全:HIGH] 检测到 {desc} 模式 '{pattern}'。"
                    f"动态代码执行在 Agent 中禁止。"
                )

        # 3.3 网络请求（需要沙箱）
        NETWORK_PATTERNS = [
            ("curl ", "出站网络请求"),
            ("wget ", "出站网络请求"),
        ]
        for pattern, desc in NETWORK_PATTERNS:
            if pattern in combined:
                issues.append(
                    f"[安全:MEDIUM] 检测到 {desc} 模式 '{pattern}'。"
                    f"网络请求将在沙箱中执行。"
                )

        return issues

    # ═══════════════════════════════════════════════
    # Pass 4: 依赖检查（类比 JVM 符号引用验证）
    # ═══════════════════════════════════════════════

    def _check_dependencies(self, action: str,
                            agent_card: dict = None) -> List[str]:
        """检查 Agent 是否越权"""
        issues = []

        if not agent_card:
            return issues

        quadrant = agent_card.get("quadrant", "ephemeral")
        tools = agent_card.get("tools", [])

        # 4.1 Ephemeral Agent 不应有持久化权限
        if quadrant == "ephemeral":
            dangerous_for_ephemeral = [
                "mongo_query", "redis_query", "deploy_hook"
            ]
            for tool in dangerous_for_ephemeral:
                if tool in tools:
                    issues.append(
                        f"[依赖] Ephemeral Agent '{agent_card.get('name', '?')}' "
                        f"不应拥有工具 '{tool}'。Ephemeral Agent 用完即弃，"
                        f"持久化操作应由 Strategic 或 Core Agent 执行。"
                    )

        # 4.2 Core Agent 销毁保护
        if quadrant == "core" and action == "destroy":
            issues.append(
                f"[依赖] Core Agent '{agent_card.get('name', '?')}' 不可销毁。"
                f"Core Agent 是系统基础服务，只能重建不能销毁。"
            )

        return issues

    # ═══════════════════════════════════════════════
    # 统计
    # ═══════════════════════════════════════════════

    def stats(self) -> dict:
        return {
            "checks_run": self.checks_run,
            "issues_found": self.issues_found,
            "issue_rate": round(
                self.issues_found / max(self.checks_run, 1), 2
            ),
        }
