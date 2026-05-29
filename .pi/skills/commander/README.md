# Yaxiio (雅溪) v1.7


[![License](https://img.shields.io/badge/license-AGPLv3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://python.org)
[![Status](https://img.shields.io/badge/status-production%20validated-brightgreen.svg)]()
> **Yaxiio is an AI Agent OS. It finds problems, builds tools, spawns workers, and fixes them — autonomously.**
>
> Production validated: 7,048 mixed-language entries fixed across 5 languages on a live website.

[English](#english) | [中文](#chinese)

---

## English

Yaxiio orchestrates AI agents through a modular five-layer MCP (Model Context Protocol) pipeline. It features template cloning, sandbox session isolation, multi-round self-check loops, LLM-driven quality scoring, real tool integration, and **autonomous tool generation** — when agents hit a blocker, L5 Evolution analyzes the failure pattern and generates new tools.

### Architecture

```
Task → L1 Perception → L2 Planning → L3 Coordination → L4 Execution → L5 Evolution
       ↑ 3401           ↑ 3402         ↑ 3403            ↑ 3404          ↑ 3405
```

| Layer | Role | Key Capability |
|-------|------|---------------|
| L1 Perception | Intent recognition | Keyword + LLM dual-path |
| L2 Planning | Task decomposition | Data-driven batch splitting |
| L3 Coordination | Agent scheduling | Least-loaded-first, auto-scaling |
| L4 Execution | Sandboxed execution | 600s timeout, exponential backoff |
| L5 Evolution | Quality scoring + tool generation | LLM deep_score, meta_reflect |

### Quick Start

> 📖 Full guide: [User Guide](docs/user-guide.md) — API keys, multi-key setup, custom agents, troubleshooting

```bash
docker compose up -d
open http://localhost:3004
```

Or:

```bash
pip install -r requirements.txt
python3 yaxiio/yaxiio.py
```

### Key Features

- **11 Agent types** with capability cards (auditor, translator, strategist, designer, etc.)
- **Code quality firewall**: 4 reviewers (code, architecture, security, testing)
- **L5 Auto-generates tools** from repeated failure patterns
- **Crash recovery**: breakpoint resume on restart
- **React Flow dashboard**: real-time L1-L5 topology visualization
- **Human-in-the-loop scoring**: AI + Human hybrid scoring with credit system

### Project Structure

```
.pi/skills/commander/
├── yaxiio/           # Core engine (8 modules)
│   ├── workflow_engine.py
│   ├── agent_factory.py
│   ├── gap_analyzer.py
│   ├── l0_memory.py
│   ├── mcp_bridge.py
│   └── layers/       # L1-L5 MCP servers
├── dashboard/        # React Flow UI
├── tools/            # Agent tools
├── docs/             # Documentation
└── docker-compose.yml
```

### License — AGPLv3

**Why AGPLv3?** Yaxiio is an Agent OS. If someone uses it to offer a SaaS service, they must open-source their modifications. This ensures the ecosystem stays free while allowing commercial licensing for those who need it.

> Need commercial terms? Contact us.

### License

**AGPLv3** — Free software. See [LICENSE](LICENSE).

---

## 中文

Yaxiio（雅溪）是一个 AI Agent 操作系统。她能发现问题、制造工具、调遣工人、自主修复。

生产环境验证：在 5 种语言的网站上自主修复了 7,048 处内容质量问题。

### 架构

五层 MCP 流水线：感知 → 规划 → 协调 → 执行 → 进化。12 个独立 Agent，L5 能根据失败模式自主生成新工具。

### 快速开始

```bash
docker compose up -d
open http://localhost:3004
```

### 核心特性

- **11 种 Agent** 能力卡片体系
- **代码质量防火墙**：4 道审查
- **L5 自主生成工具**
- **断点续传**崩溃恢复
- **React Flow 工作流可视化**
- **人类评分系统** AI+人工混合

### 许可证

**AGPLv3** — 自由软件。详见 [LICENSE](LICENSE)。

---

© 2026 Yaxiio Contributors. AGPLv3.

---

## Future Visions

Yaxiio is not a trade tool. It is a **general-purpose Agent OS kernel**. 

| Scenario | Description |
|----------|-------------|
| **Personal AI OS** | Runs in system tray. Audits competitors, fixes translations, sends quotes — while you sleep. |
| **Trade AI Factory** | Inquiry → product match → quote → deploy preview. Human only confirms last step. |
| **Knowledge Evolution** | L5 detects failure patterns → searches internet → learns → generates new tools. Tools that build themselves. |
| **Vertical Industries** | Same shell, different cards. Legal (contract review), Medical (drug interaction check), Education (lesson plans). |
| **Edge IoT** | 200MB SQLite version on Raspberry Pi. Sensor anomaly → diagnose → dispatch → fix. |
| **Open Source Maintainer** | PR → 4-reviewer firewall → auto-merge or auto-fix. One Yaxiio instance per repo. |
| **Multi-Yaxiio Federation** | Trade Yaxiio ↔ Logistics Yaxiio ↔ Payment Yaxiio. MCP protocol internet. |

> Full details: [docs/architecture.md](docs/architecture.md)
