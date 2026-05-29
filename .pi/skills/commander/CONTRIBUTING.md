# Contributing / 贡献指南

[English](#english) | [中文](#chinese)

---

## English

### Getting Started

1. Fork the repository and clone it
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Install dependencies: `pip install redis pymongo openai requests`

### Development Guidelines

- Python 3.12+ required
- Follow [PEP 8](https://peps.python.org/pep-0008/)
- All commits must be signed: `git commit -s`
- By contributing, you agree to the [CLA](CLA.md)

### Pull Request Process

1. Ensure all changes are tested
2. Update documentation if needed
3. Submit PR with clear description (bilingual preferred)
4. Wait for maintainer review

### Commit Convention

```
feat:     New feature / 新功能
fix:      Bug fix / 修复
docs:     Documentation / 文档
refactor: Code restructuring / 重构
security: Security improvement / 安全改进
```

---

## 中文

### 入门

1. Fork 仓库并克隆到本地
2. 创建功能分支：`git checkout -b feature/你的功能`
3. 安装依赖：`pip install redis pymongo openai requests`

### 开发规范

- 需要 Python 3.12 以上
- 遵循 [PEP 8](https://peps.python.org/pep-0008/) 代码风格
- 所有提交必须签名：`git commit -s`
- 贡献即表示同意 [CLA](CLA.md)

### PR 流程

1. 确保修改已充分测试
2. 必要时更新文档
3. 提交 PR 并附清晰说明（建议双语）
4. 等待维护者审核

---

© 2026 Yaxiio Contributors.

## Good First Issues

适合新贡献者入手的任务，每个任务预计 2-4 小时：

### 🟢 入门级

1. **Dashboard 暗色/亮色主题切换**
   - 文件: `dashboard/style.css`
   - 难度: ⭐ | CSS 变量切换
   - 提示: 在 `:root` 和新增的 `[data-theme="light"]` 之间切换 CSS 变量

2. **Agent 状态刷新优化**
   - 文件: `dashboard/app.jsx`
   - 难度: ⭐⭐ | 用 WebSocket 替代 8 秒轮询
   - 提示: Flask-SocketIO 或直接用 SSE (Server-Sent Events)

3. **添加 Agent 内存使用量监控**
   - 文件: `neuron.py`
   - 难度: ⭐⭐ | `psutil.Process().memory_info().rss` 
   - 提示: 在 `_heartbeat` 方法中上报内存数据到 Redis

### 🟡 进阶级

4. **SQLite 模式 — 移除 Redis 依赖**
   - 文件: `neuron.py`, `workflow_engine.py`
   - 难度: ⭐⭐⭐ | 用 SQLite + 轮询替代 Redis Pub/Sub
   - 提示: 参考 `modules/layer2/agent_memory.py` 已有的 SQLite 实现

5. **添加 `yaxiio --version` CLI**
   - 文件: `yaxiio.py`
   - 难度: ⭐⭐ | argparse 添加 `--version` 参数
   - 提示: 从 Redis `yaxiio:self` 或环境变量读取版本号

### 贡献流程

1. 在 Issue 下留言认领任务
2. Fork → 新建分支 → 开发 → 提交 PR
3. 确保 `git commit -s` 签名
4. PR 描述中引用相关 Issue

### 本地开发

```bash
# 一键启动
docker-compose up -d

# 访问
open http://localhost:3004
```
