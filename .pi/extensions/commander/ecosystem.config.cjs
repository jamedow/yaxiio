/**
 * PM2 Ecosystem — Commander 全局路由器守护进程配置
 * =================================================
 *
 * 用法:
 *   pm2 start ecosystem.config.cjs
 *   pm2 save && pm2 startup
 *
 * 服务说明:
 *   - commander-daemon:  Commander 扩展以 RPC 模式运行，监听所有 pi 会话
 *   - commander-backend: Python Commander 后端 (Redis + MongoDB)
 *
 * 依赖:
 *   - Redis 已在独立容器/进程中运行
 *   - MongoDB 已在独立容器/进程中运行
 */

module.exports = {
  apps: [
    {
      name: "commander-daemon",
      script: "pi",
      args: "--extension .pi/extensions/commander --mode rpc --model deepseek-chat",
      cwd: process.env.PI_PROJECT_ROOT || "/app",
      interpreter: "none", // pi is a global npm bin
      env: {
        NODE_ENV: "production",
        REDIS_URL: process.env.REDIS_URL || "redis://127.0.0.1:6379",
        REDIS_PASSWORD: process.env.REDIS_PASSWORD || "",
        LLM_API_KEY: process.env.LLM_API_KEY || "",
        LLM_BASE_URL: process.env.LLM_BASE_URL || "https://api.deepseek.com/v1",
        LLM_MODEL: process.env.LLM_MODEL || "deepseek-chat",
      },
      instances: 1,
      exec_mode: "fork",
      max_memory_restart: "512M",
      restart_delay: 3000,
      max_restarts: 10,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      error_file: "/var/log/commander/daemon-error.log",
      out_file: "/var/log/commander/daemon-out.log",
      merge_logs: true,
    },

    {
      name: "commander-backend",
      script: ".pi/skills/commander/commander_v2.py",
      args: "--mode daemon",
      cwd: process.env.PI_PROJECT_ROOT || "/app",
      interpreter: "python3",
      env: {
        PYTHONUNBUFFERED: "1",
        REDIS_HOST: process.env.REDIS_HOST || "127.0.0.1",
        REDIS_PORT: process.env.REDIS_PORT || "6379",
        REDIS_PASSWORD: process.env.REDIS_PASSWORD || "",
        LLM_API_KEY: process.env.LLM_API_KEY || "",
        LLM_BASE_URL: process.env.LLM_BASE_URL || "https://api.deepseek.com/v1",
        LLM_MODEL: process.env.LLM_MODEL || "deepseek-chat",
      },
      instances: 1,
      exec_mode: "fork",
      max_memory_restart: "1G",
      restart_delay: 5000,
      max_restarts: 10,
      watch: false,
      log_date_format: "YYYY-MM-DD HH:mm:ss Z",
      error_file: "/var/log/commander/backend-error.log",
      out_file: "/var/log/commander/backend-out.log",
      merge_logs: true,
    },
  ],
};
