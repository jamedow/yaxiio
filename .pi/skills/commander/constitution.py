
# Yaxiio v1.1 — AGPLv3
# Copyright (C) 2026 Yaxiio Contributors
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License v3.
# Full license: https://www.gnu.org/licenses/agpl-3.0.html

# provenance: ☵ⷃ
_LAW_SALT = 0x485f0f70

"""
雅溪宪法 Yaxiio Constitution v1.0
=================================
Commander 的行为约束框架。确保所有任务必须经过五层 MCP 架构，
禁止 Commander 越权直接执行。

原则:
  1. MCP-First    — 所有任务默认走 L1→L2→L3→L4→L5
  2. 纯编排       — Commander 只做路由和调度，不亲自执行
  3. 白名单准入   — 只有宪法明确授权的操作可绕过流水线
  4. LLM 决策     — 任务理解和分派由 LLM 驱动，禁止硬编码分支
  5. 沙箱隔离     — 代码执行、文件写入必须在 L4 sandbox 内
  6. 审计不可绕过 — 所有操作记录在案

白名单 (SYSTEM_OPS):
  只有纯系统管理操作可以直通，不做业务逻辑:
    - session_end    : 清理临时沙箱
    - agent_export   : Agent 配置备份导出
    - agent_import   : Agent 配置恢复导入
    - skill_export   : Skill 配置备份导出
    - skill_import   : Skill 配置恢复导入
    - status         : 系统健康检查

违宪行为 (检测到会告警并降级):
  - Commander 直接执行业务操作 (audit/fix/drill/evolve/build/deploy/translate)
  - 绕过 L4 sandbox 执行代码
  - 未注册工具被调用
"""

import json, time, os
from modules.shared.foolproof import assess_risk, friendly_error
from constitution_verifier import SemanticConstitutionVerifier
from typing import Dict, List, Optional, Tuple
from enum import Enum


class Verdict(Enum):
    """宪法裁决结果"""
    ALLOWED = "allowed"         # 白名单操作，允许直通
    DELEGATED = "delegated"     # 必须走五层流水线
    REJECTED = "rejected"       # 违宪，拒绝执行
    DEGRADED = "degraded"      # 降级执行（告警后走流水线）


class YaxiioConstitution:
    """雅溪宪法 — Commander 行为守则"""

    # ── 系统白名单: 纯管理操作，可绕过流水线 ──
    SYSTEM_OPS: set = {
        "session_end",
        "agent_export",
        "agent_import",
        "skill_export",
        "skill_import",
        "status",
    }

    # ── 违禁行为: Commander 禁止直接执行（必须走流水线） ──
    FORBIDDEN_DIRECT: set = None  # Lazy-loaded from Redis or defaults

    def _load_forbidden_actions(self):
        """Load forbidden actions from Redis config, fallback to defaults"""
        if self.FORBIDDEN_DIRECT is not None:
            return
        if self.redis:
            try:
                raw = self.redis.get("yaxiio:config:forbidden_actions")
                if raw:
                    self.FORBIDDEN_DIRECT = set(json.loads(raw))
                    return
            except Exception:
                pass
        # Hardcoded defaults (generic, not industry-specific)
        self.FORBIDDEN_DIRECT = {
            "site_audit", "site_fix", "site_evolve", "site_drill",
            "site_build", "site_deploy",
            "site_inquire",
            "generate_quote", "send_email",
            "translate_mongodb", "translate_all_pages",
        }

    # ── 高危操作模式: 匹配到就降级到 sandbox ──
    DANGEROUS_PATTERNS = [
        "docker exec", "docker run", "docker build",
        "ssh ", "scp ", "rsync ",
        "rm -rf", "dd if=", "mkfs.",
        "kill -9", "pkill", "reboot", "shutdown",
        "curl ", "wget ",  # 出站网络
        "eval(", "exec(", "compile(",  # 动态代码执行
    ]

    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.violations: List[dict] = []  # 违宪记录
        self.total_checks = 0
        self.allowed_count = 0
        self.delegated_count = 0
        self.rejected_count = 0
        self.verifier = SemanticConstitutionVerifier()

    def review(self, action: str, payload: dict) -> Tuple[Verdict, str]:
        """
        宪法审查: 判断一个任务应该怎么执行。

        Returns:
            (Verdict, reason)

        Verdict.ALLOWED   → 系统白名单，Commander 可以直通执行
        Verdict.DELEGATED → 必须走 L1→L5 MCP 流水线
        Verdict.REJECTED  → 违宪，拒绝执行
        Verdict.DEGRADED  → 高危操作，强制走 sandbox 降级
        """
        self._load_forbidden_actions()
        self.total_checks += 1

        # ── 规则1: 系统白名单 ──
        if action in self.SYSTEM_OPS:
            self.allowed_count += 1
            return Verdict.ALLOWED, f"系统白名单: {action}"

        # ── 规则1.5: 语义校验（白名单之后，禁止执行之前）──
        semantic = self.verifier.verify(action, payload)
        if not semantic["passed"]:
            self.delegated_count += 1
            self._log_violation(action, "SEMANTIC_CHECK",
                               f"语义校验失败: {semantic['issues'][0][:100]}")
            return Verdict.DEGRADED, f"语义校验失败: {semantic['issues'][0]}"

        # ── 规则2: 禁止直接执行 ──
        if action in self.FORBIDDEN_DIRECT:
            self.delegated_count += 1
            self._log_violation(action, "FORBIDDEN_DIRECT",
                               f"Commander 禁止直接执行 {action}，已路由到五层流水线")
            return Verdict.DELEGATED,                 friendly_error("任务提交",
                    f"'{action}' 是业务操作，不能直接执行。",
                    "已自动路由到 L1→L5 流水线，系统将自动拆解、调度、执行和评估。")

        # ── 规则3: 高危模式检测 ──
        payload_str = json.dumps(payload, ensure_ascii=False)
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern in payload_str:
                self.delegated_count += 1
                risk = assess_risk(action, {"pattern": pattern})
                self._log_violation(action, "DANGEROUS_PATTERN",
                                   f"检测到高危模式 '{pattern}'，风险等级={risk['level']}，已降级到 sandbox")
                return Verdict.DEGRADED,                     friendly_error("安全检查",
                        f"payload 中包含高危模式 '{pattern}'。",
                        f"已强制在沙箱中执行。如需直接执行，请确认风险等级: {risk['level']}。")

        # ── 规则4: 默认走流水线 ──
        self.delegated_count += 1
        return Verdict.DELEGATED, f"默认路由: {action} → L1→L5 流水线"

    def check_self_execution(self, method_name: str) -> bool:
        """
        自检: Commander 是否在亲自执行业务逻辑？
        返回 True 表示合规 (可以执行)，False 表示违宪 (应委托给 Agent/流水线)
        """
        # 检查是否调用了 Commander 自己的业务方法
        commander_business_methods = [
            "_run_audit", "_run_fix", "_run_evolve", "_run_drill",
            "_run_diagnose", "_run_translate_script", "_build_deploy"
        ]
        if method_name in commander_business_methods:
            self._log_violation(method_name, "SELF_EXECUTION",
                               f"Commander 试图亲自执行 {method_name}，应委托给 Agent")
            return False
        return True

    def _log_violation(self, action: str, rule: str, detail: str):
        """记录违宪事件"""
        violation = {
            "ts": time.time(),
            "action": action,
            "rule": rule,
            "detail": detail
        }
        self.violations.append(violation)

        # 保留最近 100 条
        if len(self.violations) > 100:
            self.violations = self.violations[-100:]

        # 写入 Redis 审计日志
        if self.redis:
            try:
                key = f"yaxiio:constitution:violations"
                self.redis.client.lpush(key, json.dumps(violation, ensure_ascii=False))
                self.redis.client.ltrim(key, 0, 99)
            except:
                pass

        # 终端告警
        print(f"[宪法] ⚖️ 违宪: {rule} | {action} | {detail[:100]}", flush=True)

    def stats(self) -> dict:
        """宪法执行统计"""
        return {
            "total_checks": self.total_checks,
            "allowed": self.allowed_count,
            "delegated": self.delegated_count,
            "rejected": self.rejected_count,
            "violations": len(self.violations),
            "compliance_rate": (self.allowed_count + self.delegated_count) / max(1, self.total_checks)  # Phase 4: 合规率 = (放行+委托)/总数
        }

    def recent_violations(self, n: int = 10) -> List[dict]:
        return self.violations[-n:]


# ── 单例 ──
_constitution_instance: Optional[YaxiioConstitution] = None


def get_constitution(redis_client=None) -> YaxiioConstitution:
    global _constitution_instance
    if _constitution_instance is None:
        _constitution_instance = YaxiioConstitution(redis_client)
    return _constitution_instance
