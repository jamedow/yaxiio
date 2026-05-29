/**
 * Commander Governance Layer — 安全治理与审计
 * =============================================
 * 每次工具调用前进行安全审计:
 *   - deny:   阻止危险操作
 *   - warn:   警告但允许
 *   - throttle: 限流控制
 *   - audit:  记录审计日志
 *   - allow:  白名单通过
 *
 * Constitution R1: 治理日志写入 commander:governance:log
 */

// ═══════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════

export interface GovernanceRule {
  allow?: string[];
  deny?: string[];
  warn?: string[];
  throttle?: {
    tools: string[];
    limit: number;
    perSeconds: number;
  };
  audit?: string[];
  reason?: string;
}

export interface AuditEvent {
  toolName: string;
  input: Record<string, unknown>;
}

export interface AuditVerdict {
  action: "allow" | "deny" | "warn" | "throttle";
  reason?: string;
  details?: Record<string, unknown>;
}

export interface Governor {
  audit(event: AuditEvent): AuditVerdict;
  getStats(): {
    total: number;
    denied: number;
    warned: number;
    throttled: number;
  };
  resetCounters(): void;
}

// ═══════════════════════════════════════════════════════════
// 内置安全规则
// ═══════════════════════════════════════════════════════════

const DANGEROUS_BASH = [
  "rm -rf /",
  "mkfs",
  "dd if=",
  ":(){ :|:& };:",
  "chmod 777 /",
  "> /dev/sda",
  "mv / /dev/null",
  "wget -O - | sh",
  "curl | bash",
];

const SENSITIVE_PATHS = [
  ".env",
  ".env.production",
  ".env.local",
  "credentials.json",
  "service-account.json",
  "id_rsa",
  "id_ed25519",
  ".ssh/",
  ".aws/",
  ".gcloud/",
];

const FORBIDDEN_TOOLS = ["sudo", "su", "passwd", "reboot", "shutdown", "halt"];

// ═══════════════════════════════════════════════════════════
// Governor 工厂
// ═══════════════════════════════════════════════════════════

export function createGovernor(
  customRules: GovernanceRule[] = []
): Governor {
  const stats = { total: 0, denied: 0, warned: 0, throttled: 0 };

  // 合并规则: 自定义规则在前，优先级更高
  const rules = [...customRules];

  // 限流追踪
  const throttles = new Map<string, number[]>();

  function isInAllowlist(toolName: string, input: Record<string, unknown>): boolean {
    for (const rule of rules) {
      if (!rule.allow) continue;
      for (const allowed of rule.allow) {
        if (toolName === allowed) return true;
        // bash 子命令检查
        if (toolName === "bash" && input.command && typeof input.command === "string") {
          if ((input.command as string).startsWith(allowed + " ") ||
              (input.command as string) === allowed) {
            return true;
          }
        }
      }
    }
    return false;
  }

  function isInDenylist(toolName: string, input: Record<string, unknown>): string | null {
    // 内置危险检查
    if (toolName === "bash" && input.command && typeof input.command === "string") {
      const cmd = input.command as string;

      // 检查危险命令
      for (const dangerous of DANGEROUS_BASH) {
        if (cmd.includes(dangerous)) {
          return `禁止执行: "${dangerous}" — 破坏性操作`;
        }
      }

      // 检查禁止工具
      for (const forbidden of FORBIDDEN_TOOLS) {
        if (cmd.trim() === forbidden || cmd.startsWith(forbidden + " ")) {
          return `禁止执行: "${forbidden}" — 特权操作需人工审批`;
        }
      }
    }

    // 自定义规则
    for (const rule of rules) {
      if (!rule.deny) continue;
      for (const denied of rule.deny) {
        if (toolName === denied) return rule.reason || `禁止使用工具: ${denied}`;
        if (toolName === "bash" && input.command && typeof input.command === "string") {
          if ((input.command as string).includes(denied)) {
            return rule.reason || `禁止执行: ${denied}`;
          }
        }
      }
    }

    return null;
  }

  function isWarnable(toolName: string, input: Record<string, unknown>): string | null {
    // 敏感路径写入
    if (toolName === "write" || toolName === "edit") {
      const path = typeof input.path === "string" ? input.path : "";
      for (const sensitive of SENSITIVE_PATHS) {
        if (path.includes(sensitive)) {
          return `警告: 正在修改敏感文件 "${path}"`;
        }
      }
    }

    // 自定义规则
    for (const rule of rules) {
      if (!rule.warn) continue;
      for (const warned of rule.warn) {
        if (!rule.audit) continue;
        if (toolName === warned) return rule.reason || `警告: ${warned}`;
      }
    }

    for (const auditTool of rule.audit) {
      if (toolName === auditTool) {
        return rule.reason || `已记录: ${auditTool}`;
      }
    }

    return null;
  }

  function checkThrottle(toolName: string): string | null {
    for (const rule of rules) {
      if (!rule.throttle) continue;
      if (!rule.throttle.tools.includes(toolName)) continue;

      const { limit, perSeconds } = rule.throttle;
      const now = Date.now();
      const key = `throttle:${toolName}`;

      let timestamps = throttles.get(key) || [];
      // 清理过期记录
      timestamps = timestamps.filter((ts) => now - ts < perSeconds * 1000);

      if (timestamps.length >= limit) {
        const retryAfter = Math.ceil(
          (timestamps[0] + perSeconds * 1000 - now) / 1000
        );
        return `${toolName} 已达速率限制 (${limit}/${perSeconds}s)，请在 ${retryAfter}s 后重试`;
      }

      timestamps.push(now);
      throttles.set(key, timestamps);
    }

    return null;
  }

  // ── 主审计入口 ──
  function audit(event: AuditEvent): AuditVerdict {
    stats.total++;

    const { toolName, input } = event;

    // 1. 白名单 → 直接通过
    if (isInAllowlist(toolName, input)) {
      return { action: "allow" };
    }

    // 2. 限流检查 → 优先
    const throttleReason = checkThrottle(toolName);
    if (throttleReason) {
      stats.throttled++;
      return { action: "throttle", reason: throttleReason };
    }

    // 3. 黑名单 → 阻止
    const denyReason = isInDenylist(toolName, input);
    if (denyReason) {
      stats.denied++;
      return { action: "deny", reason: denyReason };
    }

    // 4. 警告 → 允许但记录
    const warnReason = isWarnable(toolName, input);
    if (warnReason) {
      stats.warned++;
      return { action: "warn", reason: warnReason };
    }

    // 5. 默认: 允许
    return { action: "allow" };
  }

  function getStats() {
    return { ...stats };
  }

  function resetCounters() {
    stats.total = 0;
    stats.denied = 0;
    stats.warned = 0;
    stats.throttled = 0;
    throttles.clear();
  }

  return { audit, getStats, resetCounters };
}
