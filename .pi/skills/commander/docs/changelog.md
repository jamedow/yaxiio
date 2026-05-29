# Yaxiio Changelog

## v1.7 — Code Quality Firewall (2026-05-28)

**Architecture:**
- 4-reviewer pipeline: code → architecture → security → testing
- 11 agents total in capability card registry
- workflow_engine.py: 1384 → 1036 lines (↓25%)
- 1 monolithic file → 8 modular components
- L3/L4 MCP coordination fully landed (_execute_subtask via dispatch_and_await)
- GapAnalyzer, WorkflowUtils, ParallelOrchestrator extracted

**Security:**
- config.py: all hardcoded defaults → env vars
- pi_guardian_v3.py: hardcoded paths → env vars + architecture note
- AnySearch MCP Server registered
- Browser Harness MCP Server registered
- Architecture Reviewer Skill created

**Documentation:**
- All docs consolidated into docs/ (architecture, agent-cards, API, constitution, deployment)
- GitHub release package: 30 files, AGPLv3, bilingual docs

## v1.4-v1.6 — Agent System v2 (2026-05-28)

- Phase 1: 7 capability cards + Neuron state machine (IDLE→EXECUTING→TIMEOUT→FAULT→RECOVERING)
- Phase 2: SchemaValidator + WorkflowSnapshot + FieldMapping
- Phase 3: HybridScorer (AI 30% + Human 70%) + Reviewer credit system
- Phase 4: AgentFactory + auto-scaling + data-driven batch decomposition
- Human review dashboard on port 3005

## v1.1-v1.3 — Foundation (2026-05-27)

- Template Clone: isolated agent memory per task (`agent:{name}:{task_id}:memory`)
- Sandbox Session lifecycle: clone → execute → L5 evaluate → cleanup → destroy
- Multi-round self-check loop with content-aware gap analysis
- L0 Memory Layer: experience storage + web knowledge caching
- Data-driven batch decomposition (extract numbers → calculate batches → assign agents)
- Verification loop: re-audit after each round, continue until issues < 100
- Batch translate tool with concurrent LLM calls
- Background fix loop for autonomous site maintenance

## v1.0 — Initial Release

- Five-layer MCP architecture (L1-L5)
- Commander orchestration engine
- Constitution review framework
- Agent neuron runtime with LLM + tool execution
- Redis Pub/Sub agent communication
- PM2 + Guardian process supervision
