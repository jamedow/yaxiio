/**
 * Commander Global Event Router — v2.3.1 Pi 扩展
 * ================================================
 *
 * 事件驱动的多Agent全局调度器。
 * 通过 Redis Pub/Sub 与 Python Commander 后端通信，
 * 监听所有 pi 会话生命周期事件，智能路由用户请求。
 *
 * 架构:
 *   pi session → [Commander Hook] → Redis Pub/Sub → Python Commander → Agent Pool
 *                                    ↑                                     │
 *                                    └─── 结果回传 ─────────────────────────┘
 *
 * 三大模式:
 *   - self:    简单问答，不干预 (pass-through)
 *   - dispatch:识别意图，分派给专业 Agent
 *   - handle:  Commander 自行处理后注入回复
 *
 * 安全: 所有 tool_call 事件经过 Governance 层审计
 *
 * 用法:
 *   pi --extension .pi/extensions/commander
 *
 * 命令:
 *   /commander status    — 查看 Commander 状态
 *   /commander agents    — 列出活跃 Agent
 *   /commander dispatch  — 手动分派任务
 */

import { Type } from "typebox";
import { createClient } from "redis";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { recognizeIntent, type RoutingDecision } from "./router.js";
import { discoverAgents, dispatchToAgentPool } from "./agent-pool.js";
import { createGovernor, type GovernanceRule } from "./governance.js";

// ═══════════════════════════════════════════════════════════
// 配置
// ═══════════════════════════════════════════════════════════

const CONFIG = {
  redis: {
    url: process.env.REDIS_URL || "redis://127.0.0.1:6379",
    password: process.env.REDIS_PASSWORD || "",
  },
  /** 任务超时 (ms) */
  taskTimeoutMs: 120_000,
  /** 结果等待超时 (ms) */
  resultTimeoutMs: 300_000,
  /** 调度结果同步回当前会话 */
  injectResults: true,
  /** 自处理模式: Commander 自带 LLM 能力处理简单请求 */
  selfHandleSimple: false,
};

// ═══════════════════════════════════════════════════════════
// 治理规则
// ═══════════════════════════════════════════════════════════

const GOVERNANCE_RULES: GovernanceRule[] = [
  { allow: ["read", "bash", "edit", "write", "mcp", "subagent"] },
  { allow: ["ls", "find", "grep", "rg", "cat", "head", "tail", "wc"] },
  { deny: ["sudo", "su", "passwd"], reason: "特权操作: 需人工审批" },
  { deny: ["rm -rf /", "mkfs", "dd if=", ":(){ :|:& };:"], reason: "破坏性操作: 已阻止" },
  { throttle: { tools: ["bash"], limit: 30, perSeconds: 60 } },
  { throttle: { tools: ["mcp"], limit: 20, perSeconds: 60 } },
  { audit: ["write", "edit"], reason: "文件变更操作: 记录审计" },
];

// ═══════════════════════════════════════════════════════════
// 状态
// ═══════════════════════════════════════════════════════════

interface CommanderStats {
  totalIntercepted: number;
  totalDispatched: number;
  totalAudited: number;
  totalBlocked: number;
  activeAgents: number;
  uptime: string;
  redisConnected: boolean;
  lastDispatchedAgent: string;
  lastDispatchedAt: string;
}

// ═══════════════════════════════════════════════════════════
// 主扩展入口
// ═══════════════════════════════════════════════════════════

export default async function (api: ExtensionAPI) {
  // ── Redis 连接 ──
  const redis = createClient({
    url: CONFIG.redis.url,
    password: CONFIG.redis.password || undefined,
  });

  redis.on("error", (err) => {
    console.error("[Commander] Redis error:", err.message);
  });

  try {
    await redis.connect();
    console.log("[Commander] ✅ Redis 已连接:", CONFIG.redis.url);
  } catch (err: any) {
    console.error("[Commander] ❌ Redis 连接失败:", err.message);
    api.registerCommand("commander", {
      description: "Commander 状态 (Redis 未连接)",
      handler: async (_args, ctx) => {
        ctx.ui.notify("Commander Redis 未连接 — 无法调度", "error");
      },
    });
    return; // 无 Redis 则降级为纯命令模式
  }

  // ── 初始化治理层 ──
  const governor = createGovernor(GOVERNANCE_RULES);

  // ── 统计 ──
  const stats: CommanderStats = {
    totalIntercepted: 0,
    totalDispatched: 0,
    totalAudited: 0,
    totalBlocked: 0,
    activeAgents: 0,
    uptime: new Date().toISOString(),
    redisConnected: true,
    lastDispatchedAgent: "",
    lastDispatchedAt: "",
  };

  const startTime = Date.now();

  // ═════════════════════════════════════════════════════════
  // 事件 Hook 1: session_start — 初始化
  // ═════════════════════════════════════════════════════════

  api.on("session_start", async (_event, ctx) => {
    ctx.ui.notify("Commander 全局路由器已就绪", "info");
    ctx.ui.setStatus("commander", "Commander 已就绪");
    // 同步 Agent 池
    stats.activeAgents = await discoverAgents(redis);
  });

  // ═════════════════════════════════════════════════════════
  // 事件 Hook 2: input — 智能路由 (核心)
  // ═════════════════════════════════════════════════════════

  api.on("input", async (event, ctx) => {
    const userInput = event.text;

    // 跳过 Commander 自己的命令
    if (userInput.startsWith("/commander")) {
      return { action: "continue" };
    }

    // 跳过注入的消息
    if (event.source === "extension") {
      return { action: "continue" };
    }

    stats.totalIntercepted++;

    // ── 意图识别 ──
    const decision: RoutingDecision = recognizeIntent(userInput);

    switch (decision.action) {
      case "self":
        // 简单对话，不干预
        api.log?.debug?.("[Commander] self → pass-through:", userInput.slice(0, 80));
        break;

      case "dispatch": {
        // 分派给专业 Agent
        stats.totalDispatched++;
        stats.lastDispatchedAgent = decision.target;
        stats.lastDispatchedAt = new Date().toISOString();

        ctx.ui.setStatus("commander", `调度中 → ${decision.target}`);

        try {
          const result = await dispatchToAgentPool(redis, {
            target: decision.target,
            task: decision.task,
            timeoutMs: CONFIG.resultTimeoutMs,
          });

          if (result && CONFIG.injectResults) {
            // 注入结果到会话，跳过 LLM 处理
            ctx.ui.notify(
              `Commander 已接管 → ${decision.target}`,
              "info"
            );
            return {
              action: "transform",
              text: `[Commander 已将此任务分派给 ${decision.target} Agent]\n\n---\n**${decision.target} 的执行结果**:\n\n${result.output}\n\n---\n\n请基於以上结果继续。`,
            };
          } else if (result) {
            // 注入到 context 但不跳过
            ctx.ui.notify(
              `Commander 调度完成: ${decision.target}`,
              "success"
            );
            return {
              action: "transform",
              text: `以下是由 ${decision.target} Agent 返回的结果:\n\n${result.output}\n\n请继续。`,
            };
          } else {
            ctx.ui.notify(
              `Commander 调度到 ${decision.target} 超时`,
              "error"
            );
          }
        } catch (err: any) {
          ctx.ui.notify(
            `Commander 调度失败: ${err.message}`,
            "error"
          );
        }
        break;
      }

      case "handle":
        // Commander 自行处理
        ctx.ui.setStatus("commander", "Commander 自行处理中...");
        // TODO: 使用 Commander 自带 LLM 能力处理
        ctx.ui.notify("Commander 自行处理模式 (待实现 LLM 集成)", "info");
        break;
    }

    return { action: "continue" };
  });

  // ═════════════════════════════════════════════════════════
  // 事件 Hook 3: tool_call — 安全治理 (每个工具调用前审计)
  // ═════════════════════════════════════════════════════════

  api.on("tool_call", async (event, ctx) => {
    stats.totalAudited++;

    const verdict = governor.audit({
      toolName: event.toolName,
      input: event.input as Record<string, unknown>,
    });

    if (verdict.action === "deny") {
      stats.totalBlocked++;
      ctx.ui.notify(
        `Commander 阻止: ${event.toolName} — ${verdict.reason}`,
        "error"
      );
      return {
        block: true,
        reason: `[Commander Governance] ${verdict.reason}`,
      };
    }

    if (verdict.action === "warn") {
      ctx.ui.notify(
        `Commander 审计: ${event.toolName} — ${verdict.reason}`,
        "warn"
      );
    }

    if (verdict.action === "throttle") {
      ctx.ui.notify(
        `Commander 限流: ${event.toolName} — ${verdict.reason}`,
        "warn"
      );
    }
  });

  // ═════════════════════════════════════════════════════════
  // 事件 Hook 4: session_shutdown — 清理
  // ═════════════════════════════════════════════════════════

  api.on("session_shutdown", async () => {
    await redis.quit();
    console.log("[Commander] Redis 连接已关闭");
  });

  // ═════════════════════════════════════════════════════════
  // 注册自定义工具: commander_dispatch (LLM可调用)
  // ═════════════════════════════════════════════════════════

  api.registerTool({
    name: "commander_dispatch",
    label: "Commander Dispatch",
    description:
      "将任务分派给 Commander 管理的专业 Agent。当检测到需要专业 Agent 处理的任务时调用此工具。",
    parameters: Type.Object({
      target: Type.String({
        description: "目标 Agent 名称 (如: 商务经理, 翻译官, 售前经理, 审计官)",
      }),
      task: Type.String({
        description: "要分派的任务描述",
      }),
    }),
    async execute(toolCallId, params, signal, onUpdate, ctx) {
      try {
        const result = await dispatchToAgentPool(redis, {
          target: params.target,
          task: params.task,
          timeoutMs: CONFIG.resultTimeoutMs,
        });

        stats.totalDispatched++;
        stats.lastDispatchedAgent = params.target;
        stats.lastDispatchedAt = new Date().toISOString();

        if (result) {
          return {
            content: [
              {
                type: "text" as const,
                text: `任务已由 **${params.target}** 完成:\n\n${result.output}`,
              },
            ],
            details: {
              agent: params.target,
              taskId: result.taskId,
              duration: result.duration,
            },
          };
        } else {
          return {
            content: [
              {
                type: "text" as const,
                text: `Agent **${params.target}** 未在超时时间内响应。`,
              },
            ],
            details: {},
            isError: true,
          };
        }
      } catch (err: any) {
        return {
          content: [
            {
              type: "text" as const,
              text: `Commander 调度失败: ${err.message}`,
            },
          ],
          details: {},
          isError: true,
        };
      }
    },
  });

  // ═════════════════════════════════════════════════════════
  // 注册命令: /commander status
  // ═════════════════════════════════════════════════════════

  api.registerCommand("commander", {
    description: "Commander 全局路由器 — 查看状态、管理 Agent",
    handler: async (args, ctx) => {
      const subCommand = args?.trim() || "status";

      switch (subCommand) {
        case "agents": {
          const agents = await discoverAgents(redis);
          stats.activeAgents = agents;
          const agentList = agents > 0
            ? `活跃 Agent 数: **${agents}**\n\n使用 \`/commander dispatch <agent> <task>\` 分派任务`
            : "暂无活跃 Agent";
          ctx.ui.notify(agentList, "info");
          break;
        }

        case "dispatch": {
          ctx.ui.notify(
            '用法: `/commander dispatch <target> <task>`\n\n例如: `/commander dispatch 翻译官 翻译这份产品规格书`',
            "info"
          );
          break;
        }

        case "status":
        default: {
          const uptimeSec = Math.floor((Date.now() - startTime) / 1000);
          const uptimeStr = uptimeSec < 60
            ? `${uptimeSec}s`
            : uptimeSec < 3600
            ? `${Math.floor(uptimeSec / 60)}m ${uptimeSec % 60}s`
            : `${Math.floor(uptimeSec / 3600)}h ${Math.floor((uptimeSec % 3600) / 60)}m`;

          await discoverAgents(redis).then((n) => { stats.activeAgents = n; });

          ctx.ui.notify(
            `**Commander 全局路由器状态**\n\n` +
            `| 指标 | 值 |\n|------|----|\n` +
            `| 运行时间 | ${uptimeStr} |\n` +
            `| 已拦截指令 | ${stats.totalIntercepted} |\n` +
            `| 已调度任务 | ${stats.totalDispatched} |\n` +
            `| 已审计调用 | ${stats.totalAudited} |\n` +
            `| 已阻止操作 | ${stats.totalBlocked} |\n` +
            `| 活跃 Agent | ${stats.activeAgents} |\n` +
            `| Redis 连接 | ${stats.redisConnected ? "✅" : "❌"} |\n` +
            `| 最近调度 | ${stats.lastDispatchedAgent || "—"} (${stats.lastDispatchedAt || "—"})`,
            "info"
          );
          break;
        }
      }
    },
  });

  // ═════════════════════════════════════════════════════════
  // 周期性心跳
  // ═════════════════════════════════════════════════════════

  const heartbeatInterval = setInterval(async () => {
    try {
      await redis.publish(
        "commander:heartbeat",
        JSON.stringify({
          source: "pi-extension",
          timestamp: Date.now(),
          stats: {
            intercepted: stats.totalIntercepted,
            dispatched: stats.totalDispatched,
            blocked: stats.totalBlocked,
          },
        })
      );
    } catch {
      // 静默处理
    }
  }, 30_000); // 每30秒

  // 清理心跳定时器
  api.on("session_shutdown", () => {
    clearInterval(heartbeatInterval);
  });

  console.log("[Commander] 🚀 全局事件路由器已启动");
}
