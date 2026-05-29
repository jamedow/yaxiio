#!/usr/bin/env node
/**
 * Commander Client SDK — Node.js 版本
 * =====================================
 * 外部 Agent 通过标准化协议接入 Commander 生态。
 *
 * 安装: npm install commander-client   (或直接复制此文件)
 *
 * 使用:
 *   const { CommanderClient } = require('./commander_client');
 *
 *   const client = new CommanderClient({
 *     host: '172.17.0.6',
 *     agentId: 'my-node-agent',
 *     capabilities: ['search', 'index']
 *   });
 *
 *   await client.start();
 *   const result = await client.dispatchAndWait('翻译官', '翻译: Hello → 俄语');
 *   console.log(result);
 */

const http = require('http');
const WebSocket = require('ws');
const crypto = require('crypto');

// ═══════════════════════════════════════════════════════════════
// CommanderClient
// ═══════════════════════════════════════════════════════════════

class CommanderClient {
  /**
   * @param {Object} options
   * @param {string} options.host - Commander 主机 (默认 127.0.0.1)
   * @param {number} options.httpPort - HTTP 心跳端口 (默认 3399)
   * @param {number} options.wsPort - WebSocket 端口 (默认 3398)
   * @param {string} options.agentId - Agent 唯一标识
   * @param {string} options.agentIp - Agent IP
   * @param {number} options.agentPort - Agent 本地端口
   * @param {string[]} options.capabilities - 能力标签
   * @param {Object} options.metadata - 元数据
   */
  constructor(options = {}) {
    this.host = options.host || '127.0.0.1';
    this.httpBase = `http://${this.host}:${options.httpPort || 3399}`;
    this.wsUrl = `ws://${this.host}:${options.wsPort || 3398}`;
    this.agentId = options.agentId || `node-agent-${crypto.randomBytes(4).toString('hex')}`;
    this.agentIp = options.agentIp || '127.0.0.1';
    this.agentPort = options.agentPort || 9900;
    this.capabilities = options.capabilities || ['generic'];
    this.metadata = options.metadata || {};

    this._running = false;
    this._ws = null;
    this._clientId = null;
    this._pending = new Map();  // correlation_id → { resolve, reject, timer }
    this._handlers = new Map(); // msg_type → [handler]
  }

  // ── HTTP 通道 ──────────────────────────────────────

  _heartbeatPayload() {
    return {
      agent_id: this.agentId,
      ip: this.agentIp,
      port: this.agentPort,
      capabilities: this.capabilities,
      metadata: this.metadata,
    };
  }

  _http(method, path, data = null) {
    return new Promise((resolve, reject) => {
      const url = new URL(path, this.httpBase);
      const payload = data ? JSON.stringify(data) : null;
      const options = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname + url.search,
        method,
        headers: { 'Content-Type': 'application/json' },
        timeout: 10000,
      };

      const req = http.request(options, (res) => {
        let body = '';
        res.on('data', (chunk) => body += chunk);
        res.on('end', () => {
          try { resolve(JSON.parse(body)); }
          catch { resolve({ raw: body }); }
        });
      });
      req.on('error', reject);
      req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
      if (payload) req.write(payload);
      req.end();
    });
  }

  async register() {
    const result = await this._http('POST', '/heartbeat', this._heartbeatPayload());
    console.log(`[${this.agentId}] ✅ 已注册: ${result.status}`);
    return result;
  }

  async heartbeat() {
    try {
      return await this._http('POST', '/heartbeat', this._heartbeatPayload());
    } catch { return { status: 'error' }; }
  }

  async deregister() {
    try {
      await this._http('POST', '/heartbeat/deregister', { agent_id: this.agentId });
      console.log(`[${this.agentId}] 👋 已注销`);
    } catch {}
  }

  async listOnlineAgents() {
    return this._http('GET', '/heartbeat/online');
  }

  async findAgentByCapability(capability) {
    return this._http('GET', `/heartbeat/capability?q=${capability}`);
  }

  async getHealth() {
    return this._http('GET', '/heartbeat/status');
  }

  _startHeartbeatLoop() {
    this._heartbeatTimer = setInterval(() => this.heartbeat(), 30000);
  }

  // ── WebSocket 通道 ─────────────────────────────────

  connectWs() {
    return new Promise((resolve, reject) => {
      this._ws = new WebSocket(this.wsUrl);
      this._ws.on('open', () => {});  // 等到收到 connected 消息
      this._ws.on('error', reject);

      this._ws.once('message', (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.type === 'connected') {
          this._clientId = msg.client_id;
          // 发送注册
          this._ws.send(JSON.stringify({
            action: 'register',
            agent_id: this.agentId,
            capabilities: this.capabilities,
          }));
        }
      });

      this._ws.once('message', (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.type === 'registered') {
          console.log(`[${this.agentId}] 🌉 WS 已连接 (client=${this._clientId})`);

          // 后续消息 → 分发
          this._ws.on('message', (data) => {
            try {
              const msg = JSON.parse(data.toString());
              this._dispatchMessage(msg);
            } catch {}
          });

          this._ws.on('close', () => {
            console.log(`[${this.agentId}] ⚠️ WS 断开`);
            this._ws = null;
          });

          resolve(this._clientId);
        }
      });
    });
  }

  disconnectWs() {
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
  }

  /**
   * 派发任务
   * @returns {string} correlation_id
   */
  dispatch(target, task, correlationId = null) {
    if (!this._ws) throw new Error('未连接 WebSocket');
    const corrId = correlationId || `${this.agentId}-${crypto.randomBytes(4).toString('hex')}`;
    this._ws.send(JSON.stringify({
      action: 'dispatch',
      target,
      task,
      correlation_id: corrId,
    }));
    console.log(`[${this.agentId}] 📤 派发 → ${target} (id=${corrId})`);
    return corrId;
  }

  /**
   * 等待任务结果
   * @param {string} correlationId
   * @param {number} timeout - 超时秒数
   * @returns {Promise<Object>}
   */
  waitResult(correlationId, timeout = 120) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this._pending.delete(correlationId);
        resolve({ type: 'task_error', error: `超时 (${timeout}s)` });
      }, timeout * 1000);

      this._pending.set(correlationId, { resolve, reject, timer });
    });
  }

  /**
   * 派发并等待结果（一步完成）
   */
  async dispatchAndWait(target, task, timeout = 120) {
    const corrId = this.dispatch(target, task);
    return this.waitResult(corrId, timeout);
  }

  ping() {
    if (this._ws) this._ws.send(JSON.stringify({ action: 'ping' }));
  }

  queryStatus() {
    return new Promise((resolve) => {
      if (!this._ws) return resolve({});
      const handler = (msg) => {
        if (msg.type === 'agent_status') {
          resolve(msg);
          const h = this._handlers.get('agent_status') || [];
          this._handlers.set('agent_status', h.filter(x => x !== handler));
        }
      };
      this.onMessage('agent_status', handler);
      this._ws.send(JSON.stringify({ action: 'status' }));
    });
  }

  /**
   * 注册消息处理器
   */
  onMessage(msgType, handler) {
    if (!this._handlers.has(msgType)) this._handlers.set(msgType, []);
    this._handlers.get(msgType).push(handler);
  }

  _dispatchMessage(msg) {
    const msgType = msg.type || '';
    const corrId = msg.correlation_id || '';

    // 1. 检查是否匹配 pending 中的请求
    if (corrId && this._pending.has(corrId)) {
      const { resolve, timer } = this._pending.get(corrId);
      clearTimeout(timer);
      this._pending.delete(corrId);
      if (msgType === 'task_result') {
        console.log(`[${this.agentId}] ✅ 结果: ${msg.agent} (${msg.elapsed_ms}ms)`);
      }
      resolve(msg);
      return;
    }

    // 2. 分发给注册的处理器
    for (const handler of this._handlers.get(msgType) || []) {
      try { handler(msg); } catch (e) { console.error(`[${this.agentId}] 处理异常:`, e); }
    }
  }

  // ── 生命周期 ───────────────────────────────────────

  async start() {
    this._running = true;
    await this.register();
    this._startHeartbeatLoop();
    await this.connectWs();

    // WS ping 循环
    this._pingTimer = setInterval(() => this.ping(), 30000);

    return this._clientId;
  }

  async stop() {
    this._running = false;
    if (this._heartbeatTimer) clearInterval(this._heartbeatTimer);
    if (this._pingTimer) clearInterval(this._pingTimer);
    await this.deregister();
    this.disconnectWs();
    console.log(`[${this.agentId}] 🛑 已停止`);
  }
}

// ═══════════════════════════════════════════════════════════════
// 导出
// ═══════════════════════════════════════════════════════════════

module.exports = { CommanderClient };

// CLI 入口
if (require.main === module) {
  const [,, cmd, ...args] = process.argv;
  const host = process.env.COMMANDER_HOST || '127.0.0.1';

  (async () => {
    const client = new CommanderClient({ host });

    if (cmd === 'status' || !cmd) {
      await client.start();
      const status = await client.queryStatus();
      console.log(JSON.stringify(status, null, 2));
    } else if (cmd === 'agents') {
      const agents = await client.listOnlineAgents();
      console.log(JSON.stringify(agents, null, 2));
    } else if (cmd === 'health') {
      const health = await client.getHealth();
      console.log(JSON.stringify(health, null, 2));
    } else if (cmd === 'dispatch') {
      const target = args[0];
      const task = args.slice(1).join(' ');
      if (!target || !task) {
        console.log('用法: node commander_client.js dispatch <target> <task>');
        process.exit(1);
      }
      await client.start();
      const result = await client.dispatchAndWait(target, task);
      console.log(JSON.stringify(result, null, 2));
    }

    await client.stop();
  })().catch(e => {
    console.error('Error:', e.message);
    process.exit(1);
  });
}
