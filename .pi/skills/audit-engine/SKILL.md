---
name: audit-engine
description: 五线质量审计引擎。对五大产品线页面进行跨页面语义抽检、术语一致性检查、参数真实性核查、多语言同步审计，按DQF-MQM框架将问题分类并追溯根因（模板缺陷/数据源错误/流程缺失/人工失误），输出结构化审计报告。集成Playwright自动化浏览器验收与经验积累系统。当用户需要审计、检查、抽检、质量、audit、review、质检、核查时使用此技能。
---

# Audit Engine — 五线质量审计引擎 v3.1

## ⛔ Constitution

**R1**：数据说话，不凭感觉。禁止无具体问题支撑的评价。须指出具体页面URL、模块名、证据描述。

**R2**：逐模块检查，不跳步。页面8模块（Hero→痛点→规格→质检→配套→包装→FAQ→CTA）逐一扫描。

**R3**：根因归类。所有问题归入四类：模板缺陷（同模板页面均复现）、数据源错误（JSON配置/数据库）、流程缺失（缺少检查环节）、人工失误（孤立不可复现）。

**R4**：审计报告结构化JSON输出，禁止自然语言段落代替。

**R5**：抽样策略科学化。分层抽样（行业→L3品类→L4产品），页面总量<50全量审计，50-200抽30%，>200抽20%（每个L3至少1页）。

**R6**：翻译质量审计采用DQF-MQM框架。错误类型涵盖：术语错误、语法错误、遗漏、格式错误、文化不适配。

**R7**：每次审计后必须将新发现的模式写入经验库 `.pi/skills/audit-engine/experience/`，下次审计时优先回查已有经验。

**R8**：自动化验收优先。审计前先运行 Playwright 自动验收脚本捕获运行时错误（HTTP状态、渲染异常、资源加载失败），其结果作为审计输入的一部分。

## 🎯 Audit Dimensions & Weights

| 维度 | 权重 | 核心检查项 | 自动化 |
|:---|:---|:---|:---|
| 术语一致性 | 30% | 译法与词典一致、跨文件一致、无混用 | Playwright 内容抓取 + 词典比对 |
| 内容完整性 | 25% | 8模块齐全、规格表完整、CTA存在、无空字段/占位符 | Playwright 模块存在性检测 |
| 参数真实性 | 20% | 数字合理、标准号有效、单位正确 | 人工+经验库交叉验证 |
| 多语言同步 | 15% | 语言版本同步、alt翻译、hreflang正确 | Playwright 多语言对比 |
| 内链完整性 | 10% | 产品链接有效、配套推荐准确、面包屑可导航 | Playwright 链接可达性 |

## 🛠️ Workflow

**Step 1**：确定审计范围 — 解析指令，确定行业、页面类型、抽样比例。

**Step 2**：加载经验库 — 读取 `.pi/skills/audit-engine/experience/{industry}-patterns.json`，获取该行业已知问题模式作为优先检查项。

**Step 3**：Playwright 自动化验收 — 运行 `node .pi/skills/audit-engine/scripts/audit-runner.js --industry={industry} --sample={rate}`，捕获：
- HTTP 状态码异常（4xx/5xx）
- 页面渲染空白（内容<100字符）
- L2/L3/L4 页面链接可达性
- 坦克页 8 模块覆盖率
- SEO 标签完整性

**Step 4**：逐页人工扫描 — 对 Playwright 抽检到的页面进行：
- 页面级检查（Meta/标题层级/图片alt/CTA/内链）
- 模块级检查（8模块对标，每模块内容质量）
- 跨页面检查（术语一致性，相同品类不同页面对比）

**Step 5**：问题记录与P0/P1/P2定级 — P0阻断（缺CTA、URL 404/500）、P1重要（术语混用、参数错误）、P2优化（alt缺失、格式不统一）。

**Step 6**：根因归类 — 每个问题标注根因类型，与经验库已有模式对比，标记是新模式还是已知模式复现。

**Step 7**：经验更新 — 将本次新发现的模式和统计写入经验库，更新 `{industry}-patterns.json`。

**Step 8**：生成审计报告 — JSON格式，含 Playwright 自动化结果 + 人工审计发现 + 经验库匹配统计。

## 🤖 Playwright 自动化集成

### 调用方式

审计开始时执行一次自动化验收，作为审计前置检查：

```bash
# 全量电力行业 30% 抽样自动验收
node .pi/skills/audit-engine/scripts/audit-runner.js --industry=power --sample=30

# 单页快速诊断
node .pi/skills/audit-engine/scripts/audit-runner.js --url=/zh/industries/power/solar-farm/solar-farm-grounding-lightning

# 全站健康检查（所有行业 L1/L2 页 + 每行业 3 个 L4 抽样）
node .pi/skills/audit-engine/scripts/audit-runner.js --mode=healthcheck
```

### 自动化检查项

| 检查项 | 检测方式 | 输出 |
|--------|---------|------|
| HTTP 状态 | `response.status()` | 非200的URL列表 |
| 页面空白 | `main` textContent 长度 | <100字符标记为 BLANK |
| H1 存在性 | `locator('h1').first().textContent()` | 空/null → P1 |
| 导航可见 | `nav, header` 可见性 | 不可见 → P1 |
| L2→L3 链接可达 | 遍历卡片链接 → goto → 检查 H1 | 404/500 → P0 |
| L3→L4 链接可达 | 遍历 autoChildren 卡片 → goto → 检查内容 | 404/500 → P0 |
| 8 模块覆盖 | 关键词匹配（痛点/规格/FAQ/CTA） | 缺失模块统计 |
| SEO 标签 | canonical/hreflang 计数 | 缺失 → P1 |
| 面包屑 | `[class*="breadcrumb"] a` 计数 | <2级 → P1 |

### 容器环境注意事项

必须照搬 `scripts/test-customer-portal.js` 中的外部资源屏蔽和 `waitUntil: 'commit'` 策略，否则 `googletagmanager`/`fonts.googleapis` 的超时会阻塞页面加载。

## 📊 经验积累系统

### 经验文件结构

每次审计后更新 `.pi/skills/audit-engine/experience/{industry}-patterns.json`：

```json
{
  "industry": "power",
  "lastAudit": "2026-05-21T10:00:00Z",
  "totalAudits": 3,
  "knownPatterns": [
    {
      "patternId": "PWR-001",
      "title": "vue-i18n @ 字符导致SSR 500",
      "dimension": "contentIntegrity",
      "severity": "P0",
      "rootCause": "templateDefect",
      "detection": "检查 i18n JSON 中所有非 email 的 @ 字符",
      "fix": "替换 @ 为等价表述（如括号说明）",
      "foundIn": ["hydropower-underwater"],
      "lastSeen": "2026-05-21",
      "recurrenceCount": 1,
      "autoCheck": "grep -rn '@' i18n/ --include='*.json' | grep -v 'ogTitle|email|@context'"
    }
  ],
  "statistics": {
    "totalIssuesFound": 42,
    "byDimension": {"terminology": 15, "contentIntegrity": 12, "parameterValidity": 8, "multilingualSync": 5, "linkIntegrity": 2},
    "byRootCause": {"templateDefect": 20, "dataSourceError": 10, "processGap": 8, "humanError": 4},
    "bySeverity": {"P0": 5, "P1": 22, "P2": 15}
  }
}
```

### 经验如何驱动审计

1. **审计前**：加载 `{industry}-patterns.json`，将 `knownPatterns[].autoCheck` 作为优先执行的自动化检测项
2. **审计中**：每发现一个问题，在经验库中搜索相似模式（按 dimension + rootCause + description 关键词匹配）。已有模式 → increment `recurrenceCount`；新模式 → 追加 `knownPatterns`
3. **审计后**：更新 `statistics` 汇总数据，计算问题密度趋势（本次问题数/页面数 vs 历史均值），生成改进建议的优先级排序

### 经验库自迭代

当某个 `knownPattern` 的 `recurrenceCount >= 2` 且跨 L2 复现时，自动升级为**常驻检查规则**——写入本 SKILL.md 的 AuditorChecklist，后续所有审计强制执行该项检查。

已升级的常驻规则列表见下方 `AuditorChecklist`。

## 📋 AuditorChecklist（自动迭代生成的检查清单）

以下规则由经验库自动升级，每次审计必须逐条核对：

```
[PWR-001] i18n @字符检查 → grep -rn '@' i18n/ --include='*.json' | grep -v 'ogTitle|email|@context'
[PWR-002] Slug命名约定检查 → cat{N}Slug 值必须为 "industries/{industry}/{l2}/{l3}" 全名格式，禁止缩写和 /l4/ 路径段
[PWR-003] L4文件目录位置检查 → 每个L4 JSON的parentName必须与实际L3目录名一致（禁止放错目录）
[PWR-004] L2页面JSON完整性 → 检查每个L2是否有对应 {l2}.json 文件且包含 cat{N}Name/Slug 目录数据
[PWR-005] en/zh 结构同步 → 检查每个L2 JSON的en和zh版本cat条目数是否一致
```

## 📊 Output Schema

```json
{
  "auditId": "AUDIT-20260521-001",
  "scope": {"industry":"power","sampleRate":"30%","totalPages":150,"auditedPages":45},
  "playwrightResults": {
    "pagesChecked": 18,
    "httpErrors": [],
    "blankPages": [],
    "brokenLinks": [],
    "moduleCoverage": {"total":72,"covered":60,"rate":"83%"}
  },
  "summary": {"totalIssues":23,"p0Blocking":3,"p1Important":12,"p2Optimization":8,
    "rootCauseDistribution":{"templateDefect":8,"dataSourceError":6,"processGap":5,"humanError":4},
    "knownPatternMatches": 5,
    "newPatternsDiscovered": 2},
  "experienceUpdate": {"patternsAdded":["PWR-006","PWR-007"],"patternsUpdated":["PWR-001"]},
  "issues": [{"issueId":"ISS-001","severity":"P1","dimension":"terminology","rootCause":"templateDefect",
    "pageUrl":"/zh/industries/power/...","description":"...","evidence":"...","suggestion":"...",
    "matchesPattern":"PWR-002"}]
}
```

## 🔧 Version History

| 版本 | 日期 | 变更 |
|------|------|------|
| v3.0 | 2026-05-20 | 初始审计框架，DQF-MQM，8模块检查 |
| v3.1 | 2026-05-21 | +Playwright自动化集成，+经验积累系统，+自迭代机制，+AuditorChecklist |

## 🤝 Blackboard 协作系统

审计引擎通过文件系统 Blackboard 与其他 Agent 协作。

### 接收任务
检查 `.pi/blackboard/inbox/` 中 `to: "audit-engine"` 的任务：
```bash
grep -l '"to": "audit-engine"' .pi/blackboard/inbox/*.json
```

### 任务类型
| action | 触发方 | 审计内容 | 输出 |
|--------|--------|----------|------|
| `audit_i18n` | translate-engine | en文件键完整性+中文残留+ogLocale | REPORT |
| `audit_pages` | deploy/infrastructure | HTTP状态+页面空白+模块覆盖 | REPORT |
| `check_links` | any | sitemap URL死链扫描 | REPORT |

### 工作流
1. **认领**: 写 `tasks/{task-id}/CLAIMED`
2. **执行**: 按 scope 和 checks 字段确定审计范围
3. **报告**: 写 `tasks/{task-id}/REPORT`，P0/P1/P2 分级
4. **完成**: `touch tasks/{task-id}/DONE`，归档到 `reports/`

### 报告格式
```json
{
  "summary": {"files":23, "pass":13, "issues":10},
  "issues": [{"severity":"P0", "file":"...", "detail":"..."}],
  "recommendations": ["修复键缺失", "补充中文残留翻译"]
}
```
