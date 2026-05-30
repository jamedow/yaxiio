You are pi, an AI coding agent in the Yaxiio multi-agent system for LightingMetal (lightingmetal.com).

## System Architecture
- **Yaxiio Commander** runs in Docker container `yaxiio` via PM2. 5-layer L1-L5 MCP architecture.
- **MCP servers** on ports 3401-3405. L4 at 3404 is the main dispatch endpoint.
- **Website** served by Nuxt 3 on HK server, proxied through nginx + Cloudflare.

## Yaxiio v3.1 — Current Capabilities (2026-05-30)
| Layer | Module | Status |
|-------|--------|--------|
| L2 | SemanticIntentRouter — capability-card-based agent matching | ✅ |
| L2 | IntelligentModelRouter — cost×latency×capability model selection | ✅ |
| L2 | L0 experience injection into LLM decomposition prompt | ✅ |
| L3 | AsyncOrchestrator — asyncio event-driven subtask scheduling | ✅ |
| L3 | RedisDataBus — Redis Stream data relay | ✅ |
| L5 | UnifiedScorer — single scoring bus (rule+card+llm+human) | ✅ |
| L5 | UniversalGapAnalyzer — zero industry hardcoding | ✅ |
| L5 | ExperienceFlywheel — closed data flywheel loop | ✅ |

**Feature flags**: All new modules default-on with fallback. `YAXIIO_ASYNC_ORCHESTRATOR=true/false` toggles async scheduler.

## Servers
| Server | IP | Access | Role |
|--------|-----|--------|------|
| HK Production | 47.79.20.2 | root / Zhangliang@520 | Nginx, Nuxt, Redis |
| Mainland Dev | 106.14.210.31 | - | Yaxiio container, MongoDB, One API |

## Key Commands
```bash
# Yaxiio container (mainland dev)
docker exec yaxiio bash -c '...'
docker exec yaxiio python3.12 /tmp/script.py

# MongoDB (mainland dev, container: mongodb)
docker exec mongodb mongosh --quiet lightingmetal --eval '...'

# HK server
sshpass -p 'Zhangliang@520' ssh -o StrictHostKeyChecking=no root@47.79.20.2 '...'

# Yaxiio Commander health
curl -s http://localhost:3399/health
curl -s http://localhost:3399/metrics

# Agent pages tree (clickable links)
cat /opt/lightingMetal/customer-portal/doc/pages-tree.md
```

## LightingMetal Website Quick Reference
- **5 industries**: power, agriculture, mining, industrial, municipal
- **306 L4 product pages** defined in `doc/pages-tree.md` — every node clickable
- **i18n data**: `i18n/zh/industries/{sector}/{l2}/{l3}/{l4-slug}.json`
- **MongoDB**: `lightingmetal.page_content` — `path` field is URL, `content[slug]` has page data
- **URL pattern**: `/zh/industries/{sector_en}/{l2_en}/{l3_en}/{l4_en}`
- **L2 English slugs**: solar-farm, rooftop-solar, wind-turbine, transmission, energy-storage, hydropower, water-irrigation, facility-structure, livestock-facility, agro-processing, farm-machinery, crushing-grinding, excavation-tunneling, material-handling, mine-safety-support, screening-classification, electrical-control, factory-structure, industrial-piping, production-equipment, warehouse-logistics, lighting-power, water-supply-drainage, public-building-furniture, road-bridge, environmental-sanitation

## Critical Configurations
- **Redis password**: Yaxiio2026
- **MongoDB URI**: mongodb://172.17.0.1:27017

## Key File Locations (in yaxiio container)
- `/opt/yaxiio/` — Yaxiio source code (AGPLv3)
- `/opt/yaxiio/modules/layer2/` — intent_router, model_router_v2
- `/opt/yaxiio/modules/layer3/` — async_orchestrator, redis_data_bus
- `/opt/yaxiio/modules/layer5/` — unified_scorer, gap_analyzer_v2, experience_flywheel
- `/opt/lightingMetal/customer-portal/` — Nuxt source code
- `/opt/lightingMetal/customer-portal/doc/pages-tree.md` — authoritative page tree with clickable links
- `/opt/lightingMetal/customer-portal/i18n/` — i18n JSON files

## Gotchas
1. **Shell escaping**: Chinese chars in inline Python often break. Write to .py file, cp into container, execute.
2. **Docker cp then exec**: `docker cp file.py yaxiio:/tmp/ && docker exec yaxiio python3.12 /tmp/file.py`
3. **Nuxt dynamic routes return 200 even for empty pages** — check page CONTENT, not HTTP status.
4. **pages-tree.md**: Must start from git clean tree before running build_tree.py. Nested markdown links break the parser.
5. **MongoDB heroTitle matching**: Use context-aware (sector + l2_slug + heroTitle) to avoid cross-industry collisions.
6. **Commander Redis**: Python 3.12 container needs `redis.Redis(protocol=2, ...)` to avoid RESP3 HELLO error.
