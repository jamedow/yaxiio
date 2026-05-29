# Agent Tools

Hot-pluggable tool system — agents discover tools from Redis at runtime. Add/remove tools without code changes.

## Tool Registry

Tools are registered in Redis `tools:registry` hash and assigned to agents via `tools:agent:{name}` sets.

```bash
# Add a tool
redis-cli HSET tools:registry mytool '{"name":"mytool","desc":"Does something","usage":"python3 tools/mytool.py"}'

# Assign to agent
redis-cli SADD tools:agent:审计官 mytool
```

## Core Tools

### Query Tools

| Tool | Description | Usage |
|------|-------------|-------|
| `mongo_query.py` | Query MongoDB page_content. Filter by path, industry, lang. | `--industry power --lang en --limit 50` |
| `redis_query.py` | Query Redis cache. Read page content fields. | `--key "page:industries:power:*" --limit 50` |

### Fix Tools

| Tool | Description | Usage |
|------|-------------|-------|
| `fix_executor.py` | Execute JSON fix spec: `mongo_set`, `redis_sync`, `deploy`, `verify`. | Input: JSON fix plan via stdin/file |
| `batch_translate.py` | Batch translate Chinese content to target language. | `--batch 1 --total 9 --lang en` |
| `fast_translate.py` | Concurrent translation with 10 workers + SiliconFlow multi-key. | `SILICON_KEY=sk-xxx python3 tools/fast_translate.py` |

### Audit Tools

| Tool | Description | Usage |
|------|-------------|-------|
| `multilang_audit.py` | Full 5-language content audit. Compare zh vs en/ru/ar/es. Output: structured report. | `python3 tools/multilang_audit.py` |

### Sync & Deploy

| Tool | Description | Usage |
|------|-------------|-------|
| `content_sync.py` | Sync MongoDB → Redis. Modes: `full`, `industry`, `page`. | `python3 tools/content_sync.py full` |
| `deploy_hook.py` | Deploy + verify. Restart Nuxt, clear ISR cache, verify pages. | `python3 tools/deploy_hook.py verify power` |

### Scoring

| Tool | Description | Usage |
|------|-------------|-------|
| `hybrid_scorer.py` | AI(30%) + Human(70%) weighted scoring with reviewer credit system. | Import as module or standalone test |
| `key_pool.py` | Multi-API-key round-robin pool for rate limit bypass. | Import as module |

### Registry

| Tool | Description | Usage |
|------|-------------|-------|
| `tool_registry.py` | Tool descriptions + agent assignment. Drives LLM context generation. | Import as module |

## How Agents Discover Tools

At startup, `neuron.py` calls `_discover_tools()`:

```python
# 1. Check tools:agent:{name} Redis set
tool_names = redis.smembers(f"tools:agent:{self.name}")

# 2. For each tool, get description from tools:registry
for name in tool_names:
    desc = redis.hget("tools:registry", name)
    
# 3. Inject into LLM context
system_prompt += format_tools(desc)
```

Agent then generates bash commands in output, extracted and executed by `_extract_commands()`.

## Creating a New Tool

1. Write a Python script in `tools/`
2. Register in Redis:
   ```bash
   redis-cli HSET tools:registry mytool '{"name":"mytool","desc":"...","usage":"python3 tools/mytool.py","category":"fix"}'
   ```
3. Assign to agents:
   ```bash
   redis-cli SADD tools:agent:审计官 mytool
   ```
4. Agent restarts → auto-discovers new tool. No code changes.
