# GitHub Release Skill

## 身份
Yaxiio 开源发布助手。负责将 Yaxiio 智能调度系统以 AGPLv3 协议发布到 GitHub，确保数据脱敏完全。

## 触发条件
- 用户要求 push、release、publish、开源、github、commit to github
- 用户提到 AGPLv3、开源协议、脱敏

## 能力
1. **扫描敏感数据** — 全量扫描代码库中的密钥、密码、IP、域名、公司信息
2. **自动脱敏** — 替换为占位符或环境变量引用
3. **AGPLv3 许可** — 确保所有文件包含 AGPLv3 许可证头
4. **GitHub 发布** — 推送到指定仓库

## 脱敏规则

| 类型 | 模式 | 替换为 |
|------|------|--------|
| API Key | `sk-[a-zA-Z0-9]{20,}` | `$DEEPSEEK_API_KEY` |
| Redis 密码 | `Yaxiio2026` | `$REDIS_PASSWORD` |
| SSH 密码 | `Zhangliang@520` | `$SSH_PASSWORD` |
| 数据库密码 | `Lt@114514!` | `$DB_PASSWORD` |
| 内网 IP | `172.17.0.1` | `$MONGO_HOST` |
| 公网 IP | `47.79.20.2` | `$DEPLOY_HOST` |
| 域名 | `lightingmetal.com` | `example.com` |
| 公司名 | `LightingMetal` | `ExampleCorp` |
| 品牌名 | `Lighting Metal` | `Example Corp` |
| 产品数据库 | `lightingmetal` (DB名) | `example_db` |
| OSS 配置 | OSS endpoint/key/secret | `$OSS_*` |

## 执行步骤
1. `python3 .pi/skills/github-release/sanitize.py --dry-run` — 预览所有变更
2. `python3 .pi/skills/github-release/sanitize.py --apply` — 执行脱敏
3. 检查 diff 确认无遗漏
4. `git add -A && git commit -m "AGPLv3 open-source release" && git push`

## 约束
- 绝不修改 `.gitignore` 中已排除的文件
- 脱敏后的代码必须可运行（不引入语法错误）
- 保留所有功能性代码，只替换字面量
