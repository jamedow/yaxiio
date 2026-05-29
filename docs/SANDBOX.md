# 沙箱系统 (DinD)

## 概述

每个用户会话 = 一个独立的 Docker 容器，基于 `yaxiio-sandbox:lightingmetal` 镜像。

## 架构

```
宿主机 Docker Daemon
├── yaxiio 容器 (挂载 docker.sock)
│   └── SandboxManager
│         ├── create(user) → docker run -d sandbox-xxx
│         ├── exec(key, cmd) → docker exec sandbox bash -c "..."
│         └── destroy(key) → docker rm -f + volume rm
├── sandbox-alice 容器
├── sandbox-bob 容器
└── ...
```

## 挂载

每个沙箱自动挂载：
- `/var/run/docker.sock` — 操作宿主机 Docker (部署/重启)
- `/opt/lightingMetal` → `/app/lightingmetal` — 共享源码 (读/运行/构建)
- workspace volume → `/workspace` — 用户工作区 (不污染源码)

## 资源限制

- 内存: 4GB
- CPU: 2 核
- 最多同时运行: 30 个
- 超时自动销毁: 12 小时

## 镜像

`yaxiio-sandbox:lightingmetal` (3.25GB) 包含：
- JDK 17 + Node 20 + Python 3.12
- Maven + Docker CLI + PM2 + Git
- Redis CLI + mongosh + sshpass
- OpenAI SDK + Redis + pymongo + flask

## API

```python
from sandbox_manager import SandboxManager
mgr = SandboxManager()

# 创建沙箱
r = mgr.create("user-alice")
key = r["session_key"]

# 执行命令
mgr.exec(key, "java -version")
mgr.exec(key, "cd /app/lightingmetal/customer-portal && npm run build")

# 构建
mgr.build_frontend(key)
mgr.build_backend(key)

# 部署
mgr.deploy_hk(key)          # 部署到香港服务器
mgr.restart_service(key)    # 重启 Docker 容器

# 销毁
mgr.destroy(key)
```
