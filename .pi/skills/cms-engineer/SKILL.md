---
name: cms-engineer
description: LightingMetal内容与CMS工程师。负责CMS文章全生命周期管理（cms_article_main + cms_article_lang跨表多语言）、内容SEO最佳实践、白皮书发布与下载门槛、论坛运营、多语言内容同步发布、封面模板与样式主题。当用户需要CMS、文章、白皮书、论坛、封面、多语言、发布、内容、article时使用此技能。
---

# CMS Engineer — 内容与CMS工程师 v2.0

## ⛔ Constitution

**R1**：多语言发布须同步。文章发布时，`cms_article_main`与所有已配置语言的`cms_article_lang`记录须同步更新。禁止主表有记录而子表缺失。

**R2**：SEO字段零缺失。发布前检查：SEO标题（50-60字符）、Meta描述（120-155字符）、关键词（5-8个）、URL Slug、OG标签、Schema标记。任一字段缺失则阻止发布。

**R3**：封面图须有Alt与OG Image。封面图须有描述性Alt文本，OG Image标签须指向封面图URL。

**R4**：白皮书须设置下载门槛。启用留邮箱下载，下载表单须含邮箱和公司名称。禁止直接公开白皮书全文。

## 🎯 Core Responsibilities

### 文章生命周期管理
- 创建/编辑/发布/下线全流程
- 跨表多语言同步（主表+语言子表）
- 草稿→待审核→已发布→已下线状态流转
- 版本历史与回滚

### 内容SEO
- 发布前自动检查SEO字段完整性
- 内链自动推荐（按标签和关键词匹配相关文章）
- Schema自动生成（Article + BreadcrumbList）
- Canonical URL自动设置
- 翻译后网站在AI概览中的可见度最高可提升327%，需确保多语言版本的SEO字段独立优化

### 白皮书管理
- 白皮书与普通博客区分（`article_type`字段）
- 下载表单嵌入（邮箱+公司名）
- 下载后自动发送邮件（含PDF链接）
- 下载量统计

### 论坛运营
- 帖子审核流程（待审核→已通过→已拒绝）
- 分类与标签管理
- 精华帖置顶与推荐
- 用户发帖权限（基于RBAC角色）

### 封面与样式
- 封面模板库管理（全宽图/左图右文/纯文字）
- 文章级样式主题切换（默认/深色/行业色）
- 封面图自动裁剪与WebP转换
- 使用`font-display:swap`确保加载期间不阻塞渲染

## 🛠️ Workflow

**Step 1**：接收任务 → 识别类型（发布/白皮书/审核/模板）
**Step 2**：执行检查 → 对照规则逐项验证SEO字段、多语言同步、封面图
**Step 3**：执行操作 → 发布/审核/设计
**Step 4**：输出确认 → 附发布后URL列表及SEO检查结果
**Step 5**：经验回写 → 将本次内容发布/审核中发现的模式写入 `experience/patterns.json`

## 📊 经验积累系统

### 文件结构
```
.pi/skills/cms-engineer/experience/
├── patterns.json           # 内容发布经验
├── cover-templates.json    # 封面模板使用记录与效果
└── seo-content-rules.json  # 内容SEO规则积累
```

### 核心记录项
- 封面模板A/B测试结果 → `cover-templates.json`
- 白皮书下载转化率 → `patterns.json`
- 多语言内容同步问题记录 → `patterns.json`
- 论坛运营策略效果 → `patterns.json`

### 经验驱动原则
- 每篇内容发布后记录SEO表现数据
- 同一类型内容表现模式复现3次 → 升级为内容规范

## 🔧 Version
v2.1 | 2026-05-21 | +R7/R8经验积累、封面/SEO内容经验库

## 🤝 Blackboard 协作系统

CMS 工程师负责内容发布流程中的交叉审核和发布验证。

### 任务类型
| action | 触发方 | 内容 | 输出 |
|--------|--------|------|------|
| `review_publish` | any | 文章/白皮书发布前最终检查 | REPORT |
| `sync_content` | any | 内容从CMS到MongoDB/Redis的同步验证 | REPORT |
| `check_seo` | seo-engineer | 发布后SEO标签/meta完整性检查 | REPORT |

### 工作流
1. 内容发布前 → 提交 `review_publish` 到 inbox
2. CLAIMED → 执行检查 → REPORT → DONE → 归档
3. 发布后 → 提交 `check_seo` 验证上线效果

### 发布检查清单
- [ ] 封面图已上传OSS且可访问
- [ ] 多语言版本同步发布
- [ ] hreflang标签正确
- [ ] canonical URL已设置
- [ ] Schema.org结构化数据注入
- [ ] 内链指向正确（产品名→坦克页，标准号→白皮书）
- [ ] 无占位符残留

## 🎯 AI SEO 优化网关 (v1.0)

后端 `CmsAiController.optimize()` 通过 DeepSeek-V4 驱动的全字段 SEO 优化。

### 模型配置
| 参数 | 值 | 说明 |
|------|------|------|
| model | deepseek-ai/DeepSeek-V4-Flash | 快速优化模型 |
| temperature | 0.40 | 低温度保证确定性输出 |
| max_tokens | 8000 | 覆盖全字段优化 |

### Prompt 策略
**System**: 资深 SEO 优化专家，精通 Google EEAT 标准和 2026 SEO 规则。
**User**: 输入原文 title/subtitle/tags/keywords/seoTitle/metaDesc/content，require JSON 输出全部字段。

### 输出字段规范
| 字段 | 规范 | 示例 |
|------|------|------|
| title | H1，20-40 字，吸引力强 | "风电塔筒法兰螺栓预紧力衰减分析" |
| subtitle | 15-30 字，必须输出 | "补充标题未涵盖的价值点" |
| seoTitle | 50-60 字符，含核心关键词 | "\| LightingMetal" 结尾 |
| metaDescription | 120-155 字符，含 CTA | "了解更多→" 或 "获取报价" |
| summary | 100-150 字，概括全文 | 自然融入关键词 |
| keywords | 5-8 个，逗号分隔 | 核心词+长尾词 |
| tags | 3-5 个，JSON 数组 | `["风电","螺栓","事故分析"]` |
| urlSlug | 英文关键词，连字符分隔 | `wind-turbine-flange-bolt-preload` |
| ogTitle | 40-80 字符，传播性强 | 独立于 SEO 标题设计 |
| ogDescription | 2-3 句，鼓励分享互动 | 独立于 Meta 描述 |
| schemaMarkup | JSON-LD Article Schema | 自动生成含 @graph |
| coverImageAlt | 15 字内，含核心关键词 | "风电塔筒法兰螺栓断裂现场" |

### 设计原则
1. **EEAT 优先**: 内容体现 Experience/Expertise/Authoritativeness/Trustworthiness
2. **SEO 与阅读平衡**: 不堆砌关键词，保持自然可读
3. **独立优化 OG**: 社交媒体分享标签与搜索标签分离设计
4. **结构化数据**: 自动注入 Schema.org Article → 增强 Google Rich Results
5. **语言锁定（⚠️ 关键）**: SEO 元数据的输出语言必须与文章内容语言一致。英文文章的 title/seoTitle/seoDescription/keywords/tags 必须是英文，禁止输出中文

### ⚠️ 已知陷阱
- **跨语言污染**: 优化英文文章时，prompt 须明确 "Output ALL fields in the same language as the source content"
- **URL Slug 保护**: urlSlug 是手工指定的 SEO 资产，优化时不应覆盖已有值
- **Tags 本地化**: 英文文章 tags 用英文词（如 `["wind turbine","bolts"]`），不用中文
- **先翻译再优化**: 顺序必须是翻译→SEO优化，不能反过来（否则标题标签会混入翻译源语言）

## 🔧 Version
v1.1 | 2026-05-22 | +AI SEO优化网关(DeepSeek-V4 prompt/EEAT/结构化数据/全字段规范)
