module.exports = {
  apps: [
    {
      name: 'agent-translator',
      script: '/app/.pi/agents/runtime/agent-translator.py',
      interpreter: 'python3',
      env: { AGENT_NAME: '翻译官', REDIS_HOST: '127.0.0.1', REDIS_PASS: 'Lt@114514!' },
      autorestart: true,
      max_memory_restart: '200M',
    },
    {
      name: 'agent-business',
      script: '/app/.pi/agents/runtime/agent-business.py',
      interpreter: 'python3',
      env: { AGENT_NAME: '商务经理', REDIS_HOST: '127.0.0.1', REDIS_PASS: 'Lt@114514!' },
      autorestart: true,
      max_memory_restart: '200M',
    },
    {
      name: 'agent-presales',
      script: '/app/.pi/agents/runtime/agent-presales.py',
      interpreter: 'python3',
      env: { AGENT_NAME: '售前经理', REDIS_HOST: '127.0.0.1', REDIS_PASS: 'Lt@114514!' },
      autorestart: true,
      max_memory_restart: '200M',
    },
    {
      name: 'agent-commander',
      script: '/app/.pi/agents/runtime/agent-commander.py',
      interpreter: 'python3',
      env: { REDIS_HOST: '127.0.0.1', REDIS_PASS: 'Lt@114514!' },
      autorestart: true,
    }
  ]
};
