---
name: translate-engine
description: 五金外贸B2B网站多语翻译引擎。将中文技术内容翻译为5种目标语言（en/ru/ar/es/fr），强制遵循术语词典，确保全站术语一致性与本地化质量，区分短字段（词典驱动）与长文本（LLM语义翻译），内置DQF-MQM质量审计框架和经验积累系统。当用户需要翻译、本地化、多语言内容、国际化（i18n）时使用此技能。
---

# Translate Engine — 多语言翻译引擎 v3.1

## ⛔ Constitution — 违反即失败

**R1**：术语词典是最高权威。词典已定义的术语，译法必须完全一致（含大小写、连字符）。禁止使用训练数据记忆替代词典查询。

**R2**：禁止修改结构化代码。Vue模板语法（`{{locale}}`）、HTML标签、Markdown标记、占位符（`[待补充]`）必须原样保留。

**R3**：数字与单位不翻译。标准号（`GB/T 13912`）、量值（`≥85μm`）、百分比（`20-30%`）原样保留。

**R4**：图片alt文本必须翻译并本地化。格式：`[产品名] - [场景] - LightingMetal`。

**R5**：禁止机翻腔调。禁止中文语序直译、过度使用"Additionally/Moreover/Therefore"、标点符号一对一机械替换。

**R6**：内容分层策略 — 短字段（UI标签/CTA/SEO）词典驱动即可达95%+覆盖率；长文本（FAB/FAQ/痛点描述）需LLM语义理解，词典驱动仅40-70%，须标注`machine_translated_needs_review`。

**R7**：词典是活文档。每次翻译中遇到未定义的专业术语，必须在翻译完成后提取到 `experience/glossary-candidates.json`，供人工审核后合并进正式词典。

**R8**：本地化经验积累。翻译完成后，发现的特定语言表达偏好、文化适配问题、该语言常见的机翻错误模式，写入 `experience/locale-patterns.json` 对应语言条目，下次翻译该语言时优先参考。

## 📚 Glossary — 术语词典

完整词典位于 `experience/glossary.json`。SKILL.md 仅保留最高频术语作为速查表。

| zh-CN | en-US | ru-RU | ar-SA |
|:---|:---|:---|:---|
| 热镀锌 | hot-dip galvanized | горячее цинкование | مجلفن بالغمس الساخن |
| 扭矩系数 | torque coefficient | коэффициент крутящего момента | معامل العزم |
| 螺旋地桩 | ground screw | винтовая свая | برغي أرضي |
| 屈服强度 | yield strength | предел текучести | مقاومة الخضوع |
| 盐雾测试 | salt spray test | испытание соляным туманом | اختبار رش الملح |
| 获取报价 | Get Quote | Получить предложение | احصل على عرض سعر |

**词典使用规则**：① 翻译前强制查询完整 `experience/glossary.json`；② 未定义术语按目标语言行业标准→国际标准→直译+标注`[术语待确认]`三级策略处理；③ 新术语自动提取到 `experience/glossary-candidates.json`。

## 🛠️ Workflow — 严格顺序执行

**Step 1 — 输入解析**：识别待翻译文本与保留结构，标记所有需要保留的标签、占位符、数字、单位、标准号。

**Step 2 — 经验加载**：加载 `experience/glossary.json`（术语词典）、`experience/locale-patterns.json`（目标语言本地化经验），扫描待翻译文本生成术语映射表和本地化偏好提示。

**Step 3 — 翻译执行**：短字段直接匹配词典 + 本地化格式规则；长文本用词典约束 + LLM 语义翻译 + 本地化经验引导。

**Step 4 — 合成输出**：将译文与保留结构重组，确保所有标签正确闭合。

**Step 5 — 质量检查**：逐项对照检查清单自查，同时对比 `experience/locale-patterns.json` 中的已知陷阱。

**Step 6 — 状态标注**：写入 `_translationStatus` 字段（`translated` 或 `machine_translated_needs_review`）。

**Step 7 — 经验回写**：提取新发现的术语写入 `glossary-candidates.json`，记录新发现的本地化偏好写入 `locale-patterns.json`。

## 📋 分语言本地化规则

| 语言 | 代码 | 文本长度 | 特殊规则 |
|:---|:---|:---|:---|
| 英文 | en-US | 持平 | Title Case标题 |
| 俄文 | ru-RU | +30-50% | «»引号, 1.000,00, DD.MM.YYYY |
| 阿拉伯文 | ar-SA | +20-30% | RTL布局, 阿拉伯数字, DD/MM/YYYY |
| 西班牙文 | es-ES | +15-25% | ¡!¿?前后标点, 1.000,00 |
| 法文 | fr-FR | +20-30% | «»引号, 1 000,00, 冒号前空格 |

## 📋 质量检查清单

- [ ] 所有术语翻译与 `experience/glossary.json` 完全一致
- [ ] 已对比 `experience/locale-patterns.json` 中目标语言的已知陷阱
- [ ] HTML标签、Vue语法、占位符、Markdown完整保留
- [ ] 数字格式、日期格式、引号符合目标语言规范
- [ ] 阿拉伯语RTL已启用
- [ ] 无政治敏感/宗教冒犯内容
- [ ] 无机翻痕迹（无中文语序直译、无过度过渡词）
- [ ] 同一页面中相同术语的翻译一致
- [ ] `_translationStatus`已写入
- [ ] 新术语已提取到 `glossary-candidates.json`

## 📊 经验积累系统

### 文件结构

```
.pi/skills/translate-engine/experience/
├── glossary.json                  # 正式术语词典（6语对照）
├── glossary-candidates.json       # 候选术语队列（待人工审核）
└── locale-patterns.json           # 5语本地化经验库
```

### glossary.json 结构

```json
{
  "version": 5,
  "lastUpdated": "2026-05-21",
  "totalTerms": 127,
  "terms": [
    {
      "zh": "热镀锌",
      "en": "hot-dip galvanized",
      "ru": "горячее цинкование",
      "ar": "مجلفن بالغمس الساخن",
      "id": "galvanis celup panas",
      "vi": "mạ kẽm nhúng nóng",
      "th": "ชุบสังกะสีแบบจุ่มร้อน",
      "fr": "galvanisé à chaud",
      "es": "galvanizado en caliente",
      "pt": "galvanizado por imersão a quente",
      "de": "feuerverzinkt",
      "category": "surfaceTreatment",
      "context": "螺栓/紧固件表面处理"
    }
  ]
}
```

### glossary-candidates.json 结构

```json
{
  "pending": [
    {"zh": "达克罗涂层", "en": "Dacromet coating", "category": "surfaceTreatment", "context": "风电紧固件防氢脆", "suggestedBy": "AUDIT-20260521-001"}
  ]
}
```

### locale-patterns.json 结构

按语言组织，记录该语言的本地化经验：

```json
{
  "en": {
    "preferredExpressions": {
      "高强度紧固件": "high-strength fasteners (not 'high intensity')",
      "盐雾测试": "salt spray test (not 'salt fog test' per ISO 9227)"
    },
    "commonMistakes": [
      {"zh": "…的解决方案", "wrong": "the solution of …", "correct": "… solution (use noun adjunct)"},
      {"zh": "通过…实现", "wrong": "through … achieve", "correct": "by … (simpler is better)"}
    ],
    "tonePreference": "Professional but conversational. Use 'we'/'you'. Avoid passive voice in CTA."
  },
  "de": {
    "compoundNouns": "将中文多词术语合成为单个复合名词（如 'hot-dip galvanized' → 'Feuerverzinkung'）",
    "formalityNote": "使用正式商务语气（Sie而非du），技术文档可用名词化风格"
  }
}
```

### 经验如何驱动翻译

1. **翻译前**：加载术语词典 → 提取待翻译文本中的术语 → 生成映射表。同时加载目标语言的 `locale-patterns` → 提取该语言的表达偏好和常见错误提示。
2. **翻译中**：短字段直接用词典匹配 + 本地化格式。长文本用词典约束关键术语 + locale-patterns 引导语序和表达风格。
3. **翻译后**：
   - 新术语 → `glossary-candidates.json`（累计3次出现自动提醒人工审核）
   - 新发现的本地化偏好 → `locale-patterns.json` 对应语言条目
   - 机翻错误模式 → `locale-patterns.json` 的 `commonMistakes` 数组

## 🔧 Version

| 版本 | 日期 | 变更 |
|------|------|------|
| v3.0 | 2026-05-21 | 多语规则、内容分层、质量状态标注 |
| v3.1 | 2026-05-21 | +R7/R8经验积累、词典从SKILL移到独立文件、glossary-candidates、locale-patterns |

## 🤖 自动翻译流程

翻译引擎的核心思路：**不手工翻译，用 AI API 做专业翻译，翻译结果写入 MongoDB → ContentSync → Redis**。

### 架构设计

```
中文内容 (MongoDB page_content, lang=zh)
  ├─ 提取待翻译字段 (hero, spec, faq, fab, painPoints...)
  ├─ 按字段长度分流: 短字段→Flash(快)  长文本→Pro(好)
  ├─ 翻译为目标语言 (en/ru/ar/es/fr)
  └─ 写回 MongoDB page_content (lang={target})
       │
       ▼  ContentSync
Redis (page:{path}:{field}:{lang}) → Nuxt SSR 前端读取
```

### 五大设计原则

**① 内容分层路由**
短标签(UI/CTA/SEO)走 Flash 模型，长文本(FAQ/痛点/规格)走 Pro 模型。同文档字段自动分流，兼顾成本与品质。

**② Prompt = 专业翻译官**
System prompt 设定为"LightingMetal 资深商业翻译"角色：保留 HTML/Markdown、技术术语用行业标准译法、品牌名/型号/标准号原样、按目标语言做本地化。

**③ 批量并发**
多文档 × 多字段并发翻译。

**④ 进度可恢复**
进度文件记录已完成文档集合。中断后重跑自动跳过。

**⑤ MongoDB 直写 + ContentSync**
翻译结果直接写入 MongoDB page_content 对应语言文档，通过 ContentSyncController 同步到 Redis。

### 对比

| 维度 | 手工翻译 | AI API 翻译 |
|------|----------|-------------|
| 速度 | 1文档/5分钟 | 批量并发翻译 |
| 一致性 | 依赖人工 | Prompt约束+术语词典 |
| 可恢复 | 不可 | 断点续传 |

## 📦 i18n 文件迁移（已完成）

大量产品翻译 JSON 已从项目目录迁移到 `/app/i18n-backup/`（1768个文件），不再参与 Nuxt 构建。

```
当前架构：
  customer-portal/i18n/  → 仅保留 UI 字符串和少量静态内容（构建时 glob 打包）
  /app/i18n-backup/      → 历史翻译完整备份（1768个JSON，6语言）
  MongoDB page_content   → 页面内容主存储（翻译结果写回这里）
  Redis                  → 字段级缓存（ContentSync 从 MongoDB 同步）
```

## 🗺️ Sitemap 生成

Sitemap 通过 Nuxt Server Route 动态生成（`server/routes/sitemap.xml.ts`），从 Redis 读取所有可索引页面。

```bash
# 多语言 sitemap 由 Nuxt SSR 动态渲染，无需手工生成
# 访问: https://www.lightingmetal.com/sitemap.xml
# 各语言: https://www.lightingmetal.com/{lang}/sitemap.xml
```

## 🤝 Blackboard 协作系统

翻译引擎通过 Blackboard 提交翻译产出给审计引擎做交叉验证。

### 提交审计任务
翻译完成后，向 Blackboard inbox 提交审计请求：
```bash
cat > .pi/blackboard/inbox/audit-{scope}.json
```
任务格式见 `.pi/blackboard/README.md`，action 设为 `audit_i18n`。

### 读取审计反馈
```bash
# 查看我的审计结果
cat .pi/blackboard/tasks/audit-{scope}/REPORT
```

### 修复-审计循环
```
翻译 → 提交inbox → audit-engine认领 → 审计报告 → 
修复问题 → 重新提交审计 → 通过 → ACK确认
```

### 支持的审计检查项
- `key_completeness`: en 文件 key 与 zh 完全一致
- `no_empty_fields`: 无空值字段
- `cn_residue`: 长文本中文字段数（>20 为 P1）
- `locale_correct`: ogLocale 设为 en_US
- `title_quality`: 标题无中英文混排、无缺空格

## 🧠 AI 翻译网关 (v1.0)

后端 `CmsAiController.translate()` 通过 DeepSeek-V4 驱动的 CMS 翻译。

### 模型配置
| 参数 | 值 | 说明 |
|------|------|------|
| model | deepseek-ai/DeepSeek-V4-Flash | 快速翻译模型 |
| temperature | 0.70 | 保留创造性同时控制质量 |
| max_tokens | 8192 | 支持长博客全文翻译 |

### Prompt 策略
**System**: 专业多语言翻译专家，精通中/英/俄/泰/越等语言，了解文化差异。  
**User**: 结构化字段输入 — title, subtitle, seoTitle, metaDescription, keywords, tags, summary, content。逐字段指定翻译规则。

### 字段级翻译规则
| 字段 | 规则 |
|------|------|
| title | 核心语义准确，SEO 关键词放前 1/3，50-60 字符 |
| subtitle | 补充标题，80-100 字符 |
| seoTitle | 可与标题相同或微调，含核心关键词 |
| metaDescription | 含关键词 1-2 次 + CTA 行动号召，150-160 字符 |
| keywords | 翻译为目标语言搜索量高的对应词 |
| tags | 3-5 个，按目标语言惯例 |
| summary | 保持结构，自然融入关键词 2-3 次 |
| content | 完整翻译，**保持 Markdown 格式/链接/图片不变** |

### 关键约束
- **Markdown 保真**: `##`、`[链接](url)`、`![图](url)` 原样保留
- **内链转换**: `/zh/industries/...` → `/en/industries/...`
- **技术参数不翻译**: `42CrMoA`、`M36×200`、`≤0.5mΩ` 原样
- **故事感保留**: 英文也保持叙事驱动风格，不翻成说明书
- **输出语言检查**: 翻译完成后必须验证所有字段的目标语言一致性（title/seo/tags 不能混入源语言）
