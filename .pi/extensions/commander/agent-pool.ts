/**
 * Commander Agent 池 — Agent 发现与通信
 * ======================================
 * 通过 Redis Pub/Sub 与 Python Commander 后端通信，
 * 维护 Agent 池状态，实现任务分发与结果收集。
 *
 * Redis 通道:
 *   lightingmetal:agent:{agent_id}    — 向指定 Agent 发送任务
 *   commander:ack:{taskId}            — 等待确认
 *   commander:results:{taskId}        — 等待结果
 *   commander:agents:registry (Hash)  — Agent 注册表
 *   commander:task_queue (List)       — 任务队列
 */

import type { RedisClientType } from "redis";

// ═══════════════════════════════════════════════════════════
// 类型
// ═══════════════════════════════════════════════════════════

export interface AgentInfo {
  agentId: string;
  role: string;
  channel: string;
  status: "idle" | "busy" | "offline";
  quadrant?: string;
  capabilities: string[];
  lastHeartbeat: number;
}

export interface DispatchRequest {
  target: string;
  task: string;
  timeoutMs: number;
  context?: Record<string, unknown>;
}

export interface DispatchResult {
  taskId: string;
  output: string;
  agentId: string;
  duration: number;
  status: "success" | "fail" | "timeout";
}

// ═══════════════════════════════════════════════════════════
// Agent 发现
// ═══════════════════════════════════════════════════════════

/**
 * 从 Redis 发现所有活跃 Agent。
 * 合并: registry Hash + heartbeat 键
 */
export async function discoverAgents(redis: RedisClientType): Promise<number> {
  const agents = new Map<string, AgentInfo>();

  // 1. 从 registry Hash 获取
  try {
    const registry = await redis.hGetAll("commander:agents:registry");
    for (const [agentId, raw] of Object.entries(registry)) {
      try {
        const info = JSON.parse(raw);
        agents.set(agentId, {
          agentId,
          role: info.role || agentId,
          channel: `lightingmetal:agent:${agentId}`,
          status: "offline",
          capabilities: info.capabilities || [],
          lastHeartbeat: 0,
        });
      } catch {
        // 解析失败，跳过
      }
    }
  } catch {
    // Redis 不可用
  }

  // 2. 检查心跳
  try {
    const keys = await redis.keys("commander:agent:heartbeat:*");
    const now = Date.now();

    for (const key of keys) {
      const agentId = key.replace("commander:agent:heartbeat:", "");
      const tsRaw = await redis.get(key);
      const ts = tsRaw ? parseInt(tsRaw, 10) : 0;

      if (now - ts < 60_000) {
        // 60秒内有心跳 → online
        if (agents.has(agentId)) {
          agents.get(agentId)!.status = "idle";
          agents.get(agentId)!.lastHeartbeat = ts;
        } else {
          agents.set(agentId, {
            agentId,
            role: agentId,
            channel: `lightingmetal:agent:${agentId}`,
            status: "idle",
            capabilities: [],
            lastHeartbeat: ts,
          });
        }
      }
    }
  } catch {
    // Redis 不可用
  }

  // 3. 确保静态 Agent 都在池中
  const staticAgents = ["翻译官", "商务经理", "售前经理"];
  for (const name of staticAgents) {
    if (!agents.has(name)) {
      agents.set(name, {
        agentId: name,
        role: name,
        channel: `lightingmetal:agent:${name}`,
        status: "offline",
        capabilities: [],
        lastHeartbeat: 0,
      });
    }
  }

  return agents.size;
}

/**
 * 获取指定 Agent 信息
 */
export async function getAgentInfo(
  redis: RedisClientType,
  agentId: string
): Promise<AgentInfo | null> {
  try {
    const raw = await redis.hGet("commander:agents:registry", agentId);
    if (!raw) return null;

    const info = JSON.parse(raw);
    const tsRaw = await redis.get(`commander:agent:heartbeat:${agentId}`);
    const lastHeartbeat = tsRaw ? parseInt(tsRaw, 10) : 0;

    return {
      agentId,
      role: info.role || agentId,
      channel: `lightingmetal:agent:${agentId}`,
      status: Date.now() - lastHeartbeat < 60_000 ? "idle" : "offline",
      capabilities: info.capabilities || [],
      lastHeartbeat,
    };
  } catch {
    return null;
  }
}

// ═══════════════════════════════════════════════════════════
// 任务分发
// ═══════════════════════════════════════════════════════════

/**
 * 向 Agent 池分发任务。
 *
 * 流程:
 *   1. 发布任务到 Agent 专属频道
 *   2. 等待确认 (commander:ack:{taskId})
 *   3. 等待结果 (commander:results:{taskId})
 *   4. 超时则返回失败
 */
export async function dispatchToAgentPool(
  redis: RedisClientType,
  request: DispatchRequest
): Promise<DispatchResult | null> {
  const { target, task, timeoutMs } = request;
  const taskId = `task-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const startTime = Date.now();

  // 验证目标 Agent
  const agent = await getAgentInfo(redis, target);
  if (!agent) {
    throw new Error(`Agent "${target}" 未注册`);
  }

  // ── 发送任务到 Agent 频道 ──
  const message = JSON.stringify({
    type: "task",
    taskId,
    parentTaskId: taskId,
    source: "pi-commander-extension",
    payload: {
      type: target.includes("翻译") ? "translation" : target.includes("商务") ? "business" : "general",
      agent_type: target,
      note: task,
      task_description: task,
    },
    timestamp: Date.now(),
  });

  await redis.publish(agent.channel, message);

  // 同时推入任务队列 (让 Python Commander 也能处理)
  await redis.rPush(
    "commander:task_queue",
    JSON.stringify({
      taskId,
      target,
      task,
      source: "pi-extension",
      timestamp: Date.now(),
    })
  );

  // ── 等待确认 ──
  const ackChannel = `commander:ack:${taskId}`;
  const ackPromise = new Promise<boolean>((resolve) => {
    const ackTimeout = Math.min(timeoutMs * 0.3, 10_000);

    redis.subscribe(ackChannel, (msg) => {
      try {
        const ack = JSON.parse(msg);
        if (ack.taskId === taskId) {
          resolve(true);
        }
      } catch {
        resolve(false);
      }
    });

    setTimeout(() => resolve(false), ackTimeout);
  });

  const ackReceived = await ackPromise;

  // ── 等待结果 ──
  const resultChannel = `commander:results:${taskId}`;
  const resultPromise = new Promise<DispatchResult | null>((resolve) => {
    const resultTimeout = timeoutMs - (Date.now() - startTime);

    redis.subscribe(resultChannel, (msg) => {
      try {
        const result = JSON.parse(msg);
        resolve({
          taskId: result.taskId || taskId,
          output: result.output || result.summary || JSON.stringify(result),
          agentId: result.agentId || target,
          duration: Date.now() - startTime,
          status: result.status || "success",
        });
      } catch {
        resolve({
          taskId,
          output: msg,
          agentId: target,
          duration: Date.now() - startTime,
          status: "success",
        });
      }
    });

    setTimeout(() => {
      if (ackReceived) {
        // 收到 ack 但超时
        resolve({
          taskId,
          output: `Agent "${target}" 已确认但未在 ${timeoutMs / 1000}s 内返回结果`,
          agentId: target,
          duration: timeoutMs,
          status: "timeout",
        });
      } else {
        resolve(null);
      }
    }, resultTimeout);
  });

  return resultPromise;
}

/**
 * 批量并发分发
 */
export async function dispatchBatch(
  redis: RedisClientType,
  requests: DispatchRequest[],
  maxConcurrency: number = 4
): Promise<Map<string, DispatchResult | null>> {
  const results = new Map<string, DispatchResult | null>();

  // 分批并发执行
  for (let i = 0; i < requests.length; i += maxConcurrency) {
    const batch = requests.slice(i, i + maxConcurrency);
    const batchResults = await Promise.allSettled(
      batch.map(async (req) => {
        try {
          return await dispatchToAgentPool(redis, req);
        } catch (err: any) {
          return {
            taskId: `error-${Date.now()}`,
            output: `调度错误: ${err.message}`,
            agentId: req.target,
            duration: 0,
            status: "fail" as const,
          };
        }
      })
    );

    for (let j = 0; j < batch.length; j++) {
      const result = batchResults[j];
      results.set(
        batch[j].target,
        result.status === "fulfilled" ? result.value : null
      );
    }
  }

  return results;
}

/**
 * 检查 Agent 是否存活
 */
export async function checkAgentHealth(
  redis: RedisClientType,
  agentId: string
): Promise<boolean> {
  try {
    const tsRaw = await redis.get(`commander:agent:heartbeat:${agentId}`);
    if (!tsRaw) return false;
    const ts = parseInt(tsRaw, 10);
    return Date.now() - ts < 60_000; // 60秒内有心跳
  } catch {
    return false;
  }
}
