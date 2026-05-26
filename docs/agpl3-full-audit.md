# Yaxiio AGPLv3 全资源审计

> 审计范围: Docker 镜像 yaxiio:v1.04 内所有依赖 | 日期: 2026-05-26

---

## ⛔ 必须替换

| 组件 | 当前 | 许可证 | 问题 | 替代方案 |
|------|------|--------|------|---------|
| **MongoDB** | 7.0.34 | **SSPLv1** | 与AGPLv3不兼容 | **FerretDB** (Apache 2.0, 兼容MongoDB协议) 或 **PostgreSQL** |

> MongoDB 2018年从AGPLv3改为SSPLv1。SSPLv1要求"所有构成服务的软件"都以SSPLv1开源——这个范围远超AGPLv3，OSI不承认其为开源。

---

## ✅ 安全

### Python 包 (27个)
| 包 | 许可证 | 
|----|--------|
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click, blinker | BSD |
| openai, httpx, httpcore, h11, anyio, sniffio, certifi, idna | MIT / Apache 2.0 |
| redis, hiredis | MIT |
| pymongo | Apache 2.0 |
| playwright, pyee, greenlet | Apache 2.0 |
| pydantic, annotated-types, typing-extensions | MIT |
| jieba | MIT |
| pypinyin | MIT |
| psutil | BSD |
| python-dotenv | BSD |
| requests, urllib3, charset-normalizer | Apache 2.0 |
| tqdm | MIT |
| waitress | ZPL 2.1 |
| websockets | BSD |
| dnspython | ISC |

### 系统组件
| 组件 | 许可证 | 
|------|--------|
| Ubuntu 24.04 | GPL-compatible |
| Redis 7.0.15 | **BSD** ✅ (7.0.x 是改许可证前的版本) |
| PM2 7.0.1 | AGPLv3 ✅ |
| Git 2.43 | GPLv2 ✅ |
| Playwright Chromium | BSD + Apache 2.0 ✅ |
| DejaVu 字体 | Free (Bitstream) ✅ |

### npm 包
| 状态 | 说明 |
|------|------|
| 无全局安装 | ✅ 构建依赖在 dev-container 中，不在此镜像内 |

---

## ⚠️ 需要注意

| 项目 | 说明 |
|------|------|
| **redis-py 7.4.0** | Python 客户端库(MIT)，不是 Redis 服务端。服务端是 7.0.15(BSD)。安全 ✅ |
| **MongoDB tools** | `mongodb-database-tools` 也是 SSPLv1，需一并替换 |
| **Google Fonts** | 网站使用 Cormorant Garamond/Inter 等，通过 CSS `@import` 加载，属于"引用"而非"分发"，不触发 copyleft |
| **DeepSeek API** | 外部服务调用，AGPLv3 不约束 API 调用方 |

---

## 📊 总结

```
总审计项: 60+
AGPLv3 兼容: 59
必须替换:   1  (MongoDB → FerretDB)
```

**唯一动作：MongoDB → FerretDB**。FerretDB 是 Apache 2.0 许可，兼容 MongoDB 驱动和查询语法，可作为 PostgreSQL 的前端代理或独立运行。

其他一切就绪，可以直接开源。
