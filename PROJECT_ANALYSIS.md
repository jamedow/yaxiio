# Lighting Metal 项目分析文档

> **项目定位**：外贸独立站，主营五金制造出口  
> **主要市场**：一带一路沿线国家（东南亚、中东、非洲、中亚、南美等）  
> **网站域名**：https://www.lightingmetal.com  
> **生成日期**：2026-05-08  
> **最后更新**：2026-05-23（全面架构审计修正）

---

## 目录

1. [项目总览](#1-项目总览)
2. [技术架构（实际）](#2-技术架构实际)
3. [项目模块划分](#3-项目模块划分)
4. [前端架构（customer-portal）](#4-前端架构customer-portal)
5. [后台管理系统（service-backend）](#5-后台管理系统service-backend)
6. [后端API服务（lighting-metal-web / Java）](#6-后端api服务lighting-metal-web--java)
7. [数据库设计](#7-数据库设计)
8. [国际化与多语言](#8-国际化与多语言)
9. [SEO策略](#9-seo策略)
10. [业务核心流程](#10-业务核心流程)
11. [部署架构](#11-部署架构)
12. [AI集成](#12-ai集成)
13. [目录结构总览](#13-目录结构总览)
14. [内容即服务架构（CaaS）](#14-内容即服务架构caas)
15. [开发注意事项](#15-开发注意事项)

---

## 1. 项目总览

### 1.1 品牌介绍

Lighting Metal 是一家中国专业五金CNC制造商的独立外贸站，品牌定位：

- **核心工艺**：CNC精密加工 + MIM粉末冶金（双工艺）
- **六大行业赛道**：基建紧固件 / 光伏新能源 / 油气风电 / 矿业工程 / 农业基建 / 市政安防工业
- **认证**：ISO9001质量管理体系认证
- **服务承诺**：24小时快速报价、支持打样（1-100件）到批量量产（10,000+件）
- **月产能**：500万件+，精度控制 ±0.005mm
- **材料**：50+材料种类，涵盖不锈钢304/316、碳钢、合金钢、钛合金等
- **表面处理**：镀锌、发黑、达克罗、热浸锌、钝化等

### 1.2 客户画像

- 一带一路沿线国家基建承包商
- 东南亚、中东、非洲中小型贸易商/制造商
- 欧美定制化工业客户
- 医疗·通信·汽车行业

### 1.3 项目组成

本项目由 **4个子模块** 组成：

| 模块             | 目录                    | 技术栈                                            | 说明                 |
|----------------|-----------------------|------------------------------------------------|--------------------|
| **客户门户（前台）**   | `customer-portal/`    | Nuxt 3 + Vue 3 + TypeScript + TailwindCSS      | SSR多语言外贸官网         |
| **后台管理系统**     | `service-backend/`    | Vue 3 + Vite + TypeScript + Element Plus       | 内部员工管理系统           |
| **后端API服务**    | `lighting-metal-web/` | Spring Boot 3.2 + MyBatis-Plus + MySQL + Redis | RESTful API + 业务逻辑 |
| **AI辅助服务**     | `ai-server/`          | Node.js + Express + DeepSeek                   | AI网关（对话/交叉引用/缓存）   |

---

## 2. 技术架构（实际）

### 2.1 整体架构图（内容即服务 CaaS 模式）

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户访问层                                   │
│  ┌──────────────────────┐    ┌──────────────────────────────┐       │
│  │  客户门户 (Nuxt SSR)  │    │  后台管理系统 (Vue SPA)       │       │
│  │  www.lightingmetal.com│    │  admin.lightingmetal.com     │       │
│  │  端口: 3000           │    │  端口: 8081 (内部)           │       │
│  └──────────┬───────────┘    └──────────────┬───────────────┘       │
└─────────────┼──────────────────────────────┼─────────────────────────┘
              │                              │
    ┌─────────┴──────────────────────────────┴──────────┐
    │                   Nginx (反向代理)                    │
    │  SSL终止 / Gzip / 静态资源缓存 / API代理              │
    └──────────────────────┬─────────────────────────────┘
                           │
         ┌─────────────────┼──────────────────────┐
         ▼                 ▼                      ▼
┌─────────────────┐ ┌──────────────┐  ┌──────────────────────┐
│  Redis (缓存层)   │ │  MongoDB     │  │  Spring Boot (后端)   │
│  page:字段级缓存   │ │  page_content│  │  端口: 2233           │
│  47.79.20.2:6379 │ │  (原始内容)   │  │  JWT/RBAC/业务逻辑    │
└─────────────────┘ └──────────────┘  └──────────┬───────────┘
                                                  │
                                     ┌────────────┼────────────┐
                                     ▼            ▼            ▼
                                ┌─────────┐ ┌──────────┐ ┌──────────┐
                                │  MySQL  │ │  Redis   │ │ 阿里云OSS │
                                │  数据库  │ │  (业务)   │ │  文件存储  │
                                └─────────┘ └──────────┘ └──────────┘
```

**关键架构特征**：
- **前台页面内容不经过 Java 后端**：Nuxt Server Route 直接从 Redis 读取页面内容（MongoDB 兜底）
- **Java 后端面向管理后台**：Spring Boot 服务于 `admin.lightingmetal.com` 的管理操作
- **内容同步**：`ContentSyncController` 将 MongoDB `page_content` 同步到 Redis 字段级缓存

### 2.2 数据流（前台页面渲染）

```
浏览器 → Nuxt SSR Server (/[lang]/industries/...)
           │
           ├─ usePageI18n() → $fetch('/_i18n/{lang}/{path}')
           │                    │
           │                    ▼
           │    Nuxt Server Route (_i18n/[lang]/[...path].get.ts)
           │                    │
           │                    ├─ 优先: Redis (page:{path}:{field}:{lang})
           │                    │         │
           │                    │         └─ ContentSync 同步自 MongoDB
           │                    │
           │                    └─ 兜底: MongoDB page_content (modules/content 字段)
           │
           ├─ vue-i18n (构建时: import.meta.glob 加载 i18n JSON)
           │
           └─ 渲染 HTML → 返回浏览器
```

### 2.3 技术栈明细

| 层次                | 技术                                  | 版本              |
|-------------------|-------------------------------------|-----------------|
| **前端SSR框架**       | Nuxt.js                             | 3.21.2          |
| **前端UI框架**        | Vue.js                              | 3.5.33          |
| **国际化**           | vue-i18n                            | 10.0.7          |
| **UI组件库**         | Element Plus                        | 2.13.6          |
| **CSS框架**         | TailwindCSS                         | 3.4.x           |
| **Markdown渲染**    | marked                              | 18.0.4          |
| **状态管理**          | Pinia                               | 3.0.4           |
| **后端框架**          | Spring Boot                         | 3.2.4           |
| **Java**           | Java                                | 17              |
| **ORM**            | MyBatis-Plus                        | 3.5.9           |
| **数据库**           | MySQL                               | 8.0.x           |
| **文档数据库**         | MongoDB                             | -               |
| **缓存**            | Redis                               | 7.x             |
| **对象存储**          | 阿里云OSS                              | -               |
| **安全管理**          | Spring Security + JWT               | -               |
| **AI网关**          | Node.js Express + DeepSeek API      | -               |
| **自动化**           | Playwright                          | 1.60.x          |

---

## 3. 项目模块划分

### 3.1 客户门户（customer-portal）

**入口**：`customer-portal/app.vue` → `layouts/default.vue` → `pages/`

面向最终客户的品牌展示与询价平台。采用 **[lang] 动态路由** 架构（单一代码库，按语言参数渲染）。

核心页面（全部通过动态路由 `[lang]` 承载）：

| 路由模式                                      | 功能说明                             |
|-------------------------------------------|----------------------------------|
| `/`                                       | 根入口，引导用户选择语言                    |
| `/[lang]`                                 | 多语言首页（6语言）                      |
| `/[lang]/industries/[industry]`           | L1 行业落地页 (landing.vue) / 行业首页    |
| `/[lang]/industries/[industry]/[l2]`      | L2 场景页                           |
| `/[lang]/industries/[industry]/[l2]/[l3]` | L3 品类页                           |
| `/[lang]/industries/[industry]/[l2]/[l3]/[l4]` | L4 产品坦克页                         |
| `/[lang]/process/[...slug]`              | 工艺能力展示（CNC/MIM等）                 |
| `/[lang]/about`                          | 关于我们                             |
| `/[lang]/capabilities`                   | 生产能力展示                           |
| `/[lang]/careers`                        | 招聘页面                             |
| `/[lang]/contact`                        | 联系我们                             |
| `/[lang]/faq`                            | 常见问题                             |
| `/[lang]/privacy`                        | 隐私政策                             |
| `/[lang]/forum`                          | 论坛首页                             |
| `/[lang]/forum/article/[id]`            | 论坛文章详情                           |
| `/[lang]/forum/category/[id]`           | 论坛分类                             |
| `/[lang]/whitepapers`                   | 白皮书列表页                           |
| `/[lang]/whitepaper/[id]`               | 白皮书下载详情页                         |
| `/[lang]/[...slug]`                     | 通配路由（文章SEO友链等）                   |

**渲染策略**（ISR 混合渲染）：

- **ISR 24h**：`/zh/industries/**`、`/zh/process/**`、`/en/industries/**`、`/en/process/**`
- **ISR 7d**：`/zh/article/**`、`/en/article/**`、`/zh/faq`、`/en/faq`
- **ISR 14d**：`/ru/faq`、`/ar/faq`、`/es/faq`、`/fr/faq` 等小语种 FAQ
- **SSR**：`/zh/forum/**`、`/zh/whitepaper/**`、`/en/forum/**`、`/en/whitepaper/**`（内容实时变化）
- **默认 ISR 1h**：所有其他页面（about/contact/capabilities/privacy/careers 等）

**注意**：预渲染（prerender）在实际配置中 routes 为空数组，即**所有页面走 ISR/SSR 按需生成**。

### 3.2 后台管理系统（service-backend）

**入口**：`service-backend/src/App.vue` → `layouts/StaffLayout.vue` → `views/`

内部员工使用的全功能管理系统，实际包含 **70+ Vue 视图文件**：

| 模块        | 路由前缀                              | 核心视图                            |
|-----------|-----------------------------------|---------------------------------|
| **仪表盘**   | `/staff/dashboard`                | Dashboard.vue                   |
| **询价管理**  | `/staff/inquiry/*`                | StaffInquiryList / StaffInquiryDetail |
| **报价管理**  | `/staff/quote/*`                  | QuoteList / QuoteEdit / QuoteDetail / QuoteStrategyList |
| **跟单中心**  | `/staff/follow-up/*`              | FollowUpCockpit / FollowUpForm / FollowUpList（含18个组件） |
| **供应商管理** | `/staff/supplier/*`               | SupplierList / SupplierDetail    |
| **货代管理**  | `/staff/forwarder/*`              | ForwarderList / ForwarderDetail  |
| **物流管理**  | `/staff/logistics/*`              | LogisticsList                   |
| **订单管理**  | `/staff/order/*`                  | OrderList                       |
| **四流监控**  | `/staff/four-flow/*`              | FourFlowMonitor                 |
| **客户管理**  | `/staff/customer/*`               | CustomerDashboard / CustomerProfileList / CustomerProfileDetail / CustomerTagManage |
| **CMS管理** | `/staff/cms/*`                    | CmsArticleList / CmsArticleEdit / CmsCategoryList / CmsStyleThemeEdit / CmsStyleThemeList |
| **邮件管理**  | `/staff/email/*`                  | EmailTemplateList               |
| **AI工具**  | `/staff/ai/*`                     | AiSkillManage / AiExecutionHistory / AiModelOptimize |
| **系统设置**  | `/staff/settings`                 | Settings / AttrManagement / FieldManagement / SeoRanking / TrackStatistics / UserManagement / CustomerManagement |
| **白皮书管理** | `/staff/whitepaper/*`             | WhitepaperDashboard / WhitepaperList / WhitepaperEdit / WhitepaperDownloads |
| **后台任务**  | `/staff/background/*`             | BackgroundDashboard / BackgroundTaskList / BackgroundReportDetail |
| **数据分析**  | `/staff/analytics`                | AnalyticsDashboard              |
| **内容同步**  | `/staff/content`                  | ContentSyncManager              |
| **登录/注册** | `/staff/login`, `/staff/register` | StaffLogin / StaffRegister      |
| **产品管理**  | `/staff/product/*`                | ProductManagement / ProductDetailEdit |

**前端 API 模块**（25个）：aiSkillV3, ai, analytics, attr, autoFlow, background, cms, config, contentSync, customer, email, enums, exports, followUp, forwarder, fourFlow, inquiry, logistics, product, quote, staff, supplier, whitepaper, types, index

---

## 4. 前端架构（customer-portal）

### 4.1 目录结构

```
customer-portal/
├── app.vue                       # 根组件
├── nuxt.config.ts                # Nuxt配置（SSR/ISR路由规则/API代理/构建优化）
├── package.json                  # 依赖管理
├── tailwind.config.js            # TailwindCSS配置
├── tsconfig.json                 # TypeScript配置
│
├── assets/
│   └── css/
│       ├── main.scss             # 全局样式
│       ├── fonts.css             # 字体定义
│       ├── textures.css          # 纹理/装饰效果
│       └── variables.scss        # SCSS变量
│
├── components/                   # 38个公共组件
│   ├── AppFooter.vue             # 全局页脚（行业赛道链接+联系方式）
│   ├── AppNavbar.vue             # 全局导航栏（含产品下拉菜单）
│   ├── AiChatWidget.vue          # AI对话浮窗
│   ├── Breadcrumb.vue            # 面包屑导航
│   ├── CapabilityImageViewer.vue # 生产能力图片查看器
│   ├── ContactForm.vue           # 联系表单
│   ├── ContractSeal.vue          # 合同印章
│   ├── CoreProcessBadge.vue      # CNC+MIM工艺徽章
│   ├── CoverTemplate.vue         # 文章封面模板组件
│   ├── EngagementHints.vue       # 用户互动引导
│   ├── FAQAccordion.vue          # FAQ手风琴
│   ├── FAQBottomCTA.vue          # FAQ底部CTA
│   ├── FAQSearchHero.vue         # FAQ搜索入口
│   ├── FileUploader.vue          # 文件上传组件
│   ├── GoldenButton.vue          # 金色按钮组件
│   ├── IndustryCard.vue          # 行业卡片
│   ├── IndustryCaseCard.vue      # 行业案例卡片
│   ├── InquiryForm.vue           # 询价表单
│   ├── L2IndustryPage.vue        # L2行业页面通用组件
│   ├── LanguageSwitcher.vue      # 语言切换器
│   ├── PhoneInput.vue            # 国际电话输入
│   ├── ProcessCapabilityCard.vue # 工艺能力卡片
│   ├── ProcessComparisonTable.vue # 工艺对比表
│   ├── ProcessEquipment.vue      # 工艺设备展示
│   ├── ProcurementFAQ.vue        # 采购FAQ组件
│   ├── PromiseBadge.vue          # 品牌承诺徽章
│   ├── ReadingProgress.vue       # 阅读进度条
│   ├── RelatedProducts.vue       # 相关产品推荐
│   ├── SectionTitle.vue          # 段落标题
│   ├── TrustBadges.vue           # 信任标识
│   ├── WhitePaperCTA.vue         # 白皮书CTA
│   ├── WhitepaperDownload.vue    # 白皮书下载组件
│   ├── WhitepaperList.vue        # 白皮书列表
│   ├── YoutubeEmbed.vue          # YouTube视频嵌入
│   └── useCmsApi.ts              # CMS API客户端
│
├── composables/                  # 9个组合式函数
│   ├── useBlogInjector.ts        # 博客内链注入
│   ├── useBreadcrumb.ts          # 面包屑数据
│   ├── useHreflang.ts            # hreflang多语言SEO标签
│   ├── useIndustryTheme.ts       # 行业配色主题
│   ├── useLocaleStrings.ts       # 多语言UI字符串定义（硬编码常量）
│   ├── usePageI18n.ts            # 页面级i18n动态加载（从Redis获取）
│   ├── useRouteLink.ts           # 路由链接规范化
│   ├── useSchemaOrg.ts           # Schema.org结构化数据
│   └── useSmartLink.ts           # 智能链接（自动添加语言前缀）
│
├── layouts/
│   └── default.vue               # 默认布局
│
├── pages/                        # 页面组件（[lang] 动态路由）
│   ├── index.vue                 # 根路径 - 语言选择入口
│   ├── [lang]/
│   │   ├── index.vue             # 语言首页
│   │   ├── about.vue             # 关于我们
│   │   ├── capabilities.vue      # 生产能力
│   │   ├── careers.vue           # 招聘
│   │   ├── contact.vue           # 联系我们
│   │   ├── faq.vue               # FAQ
│   │   ├── privacy.vue           # 隐私政策
│   │   ├── whitepapers.vue       # 白皮书列表
│   │   ├── whitepaper/[id].vue  # 白皮书详情
│   │   ├── forum/
│   │   │   ├── index.vue         # 论坛首页
│   │   │   ├── article/[id].vue # 论坛文章
│   │   │   └── category/[id].vue # 论坛分类
│   │   ├── industries/
│   │   │   └── [industry]/
│   │   │       ├── landing.vue   # L1行业落地页（带导航）
│   │   │       ├── index.vue     # L1行业首页
│   │   │       └── [l2]/
│   │   │           ├── index.vue # L2场景页
│   │   │           └── [l3]/
│   │   │               ├── index.vue  # L3品类页
│   │   │               └── [l4].vue   # L4产品坦克页
│   │   ├── process/[...slug].vue # 工艺页（通配路由）
│   │   └── [...slug].vue         # 通用通配路由（文章SEO友链等）
│   └── zh/forum/index.vue        # 中文论坛首页（特殊路由）
│
├── server/                       # Nuxt服务端路由（SSR时执行）
│   ├── api/
│   │   ├── blog/list.get.ts      # 博客列表API
│   │   └── indexing/notify.post.ts # Google索引通知
│   ├── routes/
│   │   ├── _i18n/[lang]/[...path].get.ts  # ⭐ 核心：页面内容动态路由（Redis→MongoDB）
│   │   ├── _api/blog/[slug].get.ts        # 博客详情API
│   │   ├── _api/cross-refs/product.get.ts # 产品交叉引用
│   │   ├── _api/industries/children.get.ts # 行业子页面查询
│   │   ├── api/refresh-pages.ts           # 页面刷新API
│   │   ├── sitemap.xml.ts                 # 主Sitemap
│   │   ├── sitemap-forum.xml.ts           # 论坛Sitemap
│   │   └── __sitemap__/urls.ts            # Sitemap URL生成器
│   ├── middleware/redirect.ts    # 重定向中间件
│   └── utils/
│       ├── mongodb.ts            # MongoDB连接与查询
│       ├── redis.ts              # Redis连接与缓存
│       ├── flatten.ts            # 数据扁平化/反扁平化工具
│       └── googleIndexing.ts     # Google Indexing API
│
├── plugins/
│   └── i18n.ts                   # vue-i18n插件初始化（import.meta.glob加载JSON）
│
├── i18n/                         # i18n JSON翻译文件（构建时打包）
│   ├── zh/                       # 中文（基础语言）
│   ├── en/                       # 英文
│   ├── ru/                       # 俄文
│   ├── ar/                       # 阿拉伯文
│   ├── es/                       # 西班牙文
│   └── fr/                       # 法文
│   每个语言目录包含：
│   ├── pages/                    # 静态页面翻译
│   ├── components/               # 组件翻译
│   ├── industries/               # 行业/产品翻译
│   └── process/                  # 工艺页翻译
│
├── public/
│   ├── LOGO.png / favicon.ico / favicon.svg
│   ├── robots.txt
│   └── sitemap.xml
│
└── utils/
    ├── coverTemplate.ts          # 封面模板工具
    └── validators.ts             # 表单验证规则
```

### 4.2 页面渲染机制详解

项目采用 **内容即服务 (CaaS)** 模式，页面不硬编码内容，而是在运行时从缓存/数据库动态加载。

**两级加载策略**：

1. **构建时（静态 i18n）**：`vue-i18n` 通过 `import.meta.glob` 加载 `i18n/` 目录下所有 JSON，用于 UI 字符串（导航、按钮、页脚等）
2. **运行时（动态内容）**：`usePageI18n()` 在页面 `setup` 中调用 `$fetch('/_i18n/{lang}/{path}')`，从 Redis/MongoDB 加载页面内容（标题、FAB、规格、FAQ 等）

**内容存储格式（Redis）**：
```
page:industries:power:solar-farm:solar-farm-foundation-structure:solar-farm-ground-screw:heroTitle:en
page:industries:power:solar-farm:solar-farm-foundation-structure:solar-farm-ground-screw:spec1Param:en
...（字段级缓存，以 page: 为前缀，字段路径 + 语言为后缀）
```

---

## 5. 后台管理系统（service-backend）

### 5.1 技术栈

- Vue 3 + Vite + TypeScript
- Element Plus 组件库
- Pinia 状态管理
- Vue Router（含权限守卫）
- 25个前端 API 模块

### 5.2 路由结构

```
/staff/login                    # 登录
/staff/register                 # 注册
/staff/dashboard                # 仪表盘
/staff/inquiry/list             # 询价列表
/staff/inquiry/detail/:id       # 询价详情
/staff/quote/list               # 报价列表
/staff/quote/edit[/:id[/:mode]] # 报价编辑/新建
/staff/supplier/list            # 供应商列表
/staff/supplier/detail          # 供应商详情
/staff/forwarder/list           # 货代列表
/staff/forwarder/detail         # 货代详情
/staff/product                  # 产品管理
/staff/product/detail-edit[/:path] # 产品详情编辑
/staff/customer/*               # 客户管理（仪表盘/列表/详情/搜索/标签）
/staff/cms/*                    # CMS（文章/分类/样式主题）
/staff/follow-up/*              # 跟单中心（驾驶舱/列表/表单）
/staff/four-flow                # 四流监控
/staff/logistics                # 物流管理
/staff/order                    # 订单归档
/staff/email                    # 邮件管理
/staff/ai/*                     # AI工具（技能管理/执行历史/模型优化）
/staff/background/*             # 后台任务
/staff/analytics                # 数据分析
/staff/content/sync             # 内容同步管理
/staff/settings                 # 系统设置
/staff/settings/attr            # 属性管理
/staff/settings/field           # 字段管理
/staff/settings/seo             # SEO排名
/staff/settings/track           # 轨迹统计
/staff/settings/user            # 用户管理
/staff/settings/customer        # 客户设置
/staff/whitepaper/*             # 白皮书管理
```

---

## 6. 后端API服务（lighting-metal-web / Java）

### 6.1 分层架构

```
controller/  (71个Controller)
    │
    ▼
service/     (100个Service接口 → 88个ServiceImpl)
    │
    ▼
mapper/      (99个Mapper接口 - MyBatis-Plus数据访问)
    │
    ▼
model/
  ├── entity/      (99个实体类)
  ├── dto/         (数据传输对象)
  └── vo/          (视图对象)
```

### 6.2 完整Controller列表（71个）

| 业务域 | Controller |
|--------|-----------|
| **认证** | AuthController |
| **用户** | UserController, StaffController |
| **询价** | InquiryController, InquiryFollowController, InquiryParseController, InquiryHeaderFieldController |
| **报价** | QuoteController, QuoteAiController, QuoteStrategyController |
| **合同/PI** | SalesContractController, ProformaInvoiceController |
| **四流** | OrderFourFlowsController, FourFlowReportController, FlowExceptionController |
| **跟单** | OrderTodoController, OrderTimelineController, OrderArchiveController |
| **物流** | LogisticsDocumentController |
| **货代** | ForwarderController, ForwarderBackgroundController |
| **供应商** | SupplierController, SupplierAttachmentController, SupplierBackgroundController |
| **客户** | CustomerProfileController, CustomerBehaviorController, CustomerStatisticsController, CustomerTagController |
| **CMS** | CmsArticleController, CmsMultilingualArticleController, CmsCategoryController, CmsStyleThemeController, CmsStyleNodeController, CmsArticleStyleRelController, CmsCoverTemplateController, CmsCoverDesignController, CmsCoverHistoryController, CmsAiController |
| **白皮书** | WhitepaperController |
| **SEO** | SeoController, BacklinkController |
| **邮件** | EmailTemplateController, EmailLogController, EmailSendController, EmailAiController |
| **AI** | AiSkillV3Controller, AiExecutionController, AiGatewayController |
| **内容** | ContentSyncController, ContentQueryController, BlogSyncController, ProductCategoryController |
| **自动化** | AutoFlowController, ProcurementRecommendController |
| **分析** | AnalyticsController, TrackEventController |
| **后台** | BackgroundTaskController, BackgroundReportController |
| **文件** | UploadController, VideoController |
| **配置** | ConfigController, AttrController, SysDictController, LangConfigController, CountryController |
| **财务** | BankReceiptController, VatInvoiceController |
| **单证** | OrderDocumentController |
| **其他** | EnumController, WebhookReceiverController, TestController |

### 6.3 核心配置与组件

| 组件 | 用途 |
|------|------|
| **SecurityConfig** | Spring Security + JWT 认证配置 |
| **JwtInterceptor** | JWT请求拦截验证 |
| **CorsConfig** | 跨域配置 |
| **WebConfig** | Web MVC配置（含拦截器注册） |
| **RedisConfig** | Redis缓存配置（Lettuce连接池） |
| **OssConfig** | 阿里云OSS文件存储 |
| **OneApiConfig** | AI接口统一网关配置 |
| **MybatisPlusConfig** | MyBatis-Plus分页插件 |
| **CmsAiExecutorConfig** | CMS AI执行器配置 |
| **SkillExecutorConfig** | AI技能执行器配置 |

### 6.4 业务枚举（14个）

| 枚举                        | 说明         |
|---------------------------|------------|
| `InquiryStatusEnum`       | 询价状态       |
| `QuoteStatusEnum`         | 报价状态       |
| `ContractStatusEnum`      | 合同状态       |
| `ContractFlowStatusEnum`  | 合同流程状态     |
| `FundFlowStatusEnum`      | 资金流状态      |
| `InvoiceFlowStatusEnum`   | 票据流状态      |
| `LogisticsFlowStatusEnum` | 物流状态       |
| `PurchaseStatusEnum`      | 采购状态       |
| `PrepareOrderStatusEnum`  | 备货状态       |
| `CurrencyEnum`            | 货币类型       |
| `MaterialEnum`            | 产品材质       |
| `ProductMaterialEnum`     | 产品材质分类     |
| `ProductTypeEnum`         | 产品类型       |
| `StaffRoleEnum`           | 员工角色       |

### 6.5 环境配置

- **开发环境** (`application-dev.yml`)：MySQL 106.14.210.31，Redis 47.79.20.2，JWT 30天有效期
- **生产环境** (`application-prod.yml`)：独立配置
- **服务端口**：2233

---

## 7. 数据库设计

### 7.1 数据库分布

项目使用 **双数据库** 架构：

| 数据库 | 用途 | 访问方 |
|--------|------|--------|
| **MySQL** (lighting_metal) | 业务数据（询价/报价/合同/四流/客户/CMS/系统） | Spring Boot (MyBatis-Plus) |
| **MongoDB** (lightingmetal) | 前台页面内容（`page_content` 集合） | Nuxt Server Route + Spring Boot (ContentSync) |
| **Redis** | 页面字段级缓存 + 业务缓存 | Nuxt SSR + Spring Boot |

### 7.2 MySQL表分类（约100+张表，对应99个Entity/99个Mapper）

| 类别 | 表数 | 核心表 |
|------|:--:|------|
| 询价/报价/合同 | ~12 | inquiry, inquiry_follow, inquiry_log, quote, quote_item, quote_strategy, contract, proforma_invoice, sales_contract |
| 四流系统 | ~8 | order_four_flows, fund_flow, goods_flow, invoice_flow, statement |
| 物流/单证 | ~12 | logistics_order, logistics_document, logistics_tracking, bill_of_lading, customs_declaration, certificate_of_origin, packing_list |
| 供应商/货代 | ~6 | supplier, supplier_attachment, forwarder, forwarder_route |
| CMS | ~10 | cms_article_main, cms_article_lang, cms_category, cms_style_theme, cms_cover_template, cms_whitepaper |
| 客户 | ~5 | customer_profile, customer_behavior, customer_tag |
| SEO | ~5 | seo_keyword, seo_keyword_ranking, seo_crawl_task, seo_backlink |
| AI/自动化 | ~6 | ai_skill_v3, ai_skill_execution_state, trade_automation_rule |
| 邮件 | ~3 | email_template, email_log |
| 系统基础 | ~10 | staff, user, sys_config, sys_dict_item, attr_type, attr_value |
| 财务 | ~4 | bank_receipt, vat_invoice, proforma_invoice_item |
| 后台 | ~3 | background_task, background_report |
| 其他 | ~10 | track_event, order_document, country 等 |

### 7.3 MongoDB page_content 集合

前台页面内容的核心数据源。文档结构：

```json
{
  "lang": "en",
  "path": "/en/industries/power/solar-farm/solar-farm-foundation-structure/solar-farm-ground-screw",
  "industry": "power",
  "l2": "solar-farm",
  "l3": "solar-farm-foundation-structure",
  "pageType": "l4",
  "modules": {
    "hero": { "title": "Solar Farm Ground Screws", "subtitle": "..." },
    "spec": { "items": [{"param": "Material", "value": "Q235B/Q355B"}], "itemFab": [...] },
    "fab": [{ "feature": "...", "advantage": "...", "benefit": "..." }],
    "faq": { "items": [{"q": "...", "a": "..."}] },
    "painPoints": [{ "title": "...", "desc": "...", "solution": "..." }],
    "cta": { "title": "Get Quote", "buttonText": "..." },
    "seo": { "title": "...", "description": "..." },
    "standards": ["ISO 1461", "ASTM A123"],
    ...
  }
}
```

---

## 8. 国际化与多语言

### 8.1 支持的语言（6种）

| 语言代码 | 语言   | hreflang | 文本方向    | 目标市场    |
|------|------|----------|---------|---------|
| `zh` | 中文   | `zh-CN`  | LTR     | 中国及华语地区 |
| `en` | 英语   | `en-US`  | LTR     | 全球通用    |
| `ru` | 俄语   | `ru`     | LTR     | 俄罗斯·中亚  |
| `ar` | 阿拉伯语 | `ar`     | **RTL** | 中东·北非   |
| `es` | 西班牙语 | `es`     | LTR     | 拉美·西班牙   |
| `fr` | 法语   | `fr`     | LTR     | 非洲法语国家  |

### 8.2 实现方式

**架构选择**：**单套代码 + 动态路由 `[lang]`**。

与传统的「每个语言一个目录」方案不同，本项目使用 Nuxt 动态路由参数：

- 所有语言共享同一套 Vue 页面文件（`pages/[lang]/...`）
- 路由参数 `lang` 驱动语言切换
- 页面内容通过 `usePageI18n()` 运行时从 Redis 获取，合并到 `vue-i18n` 消息中
- UI 字符串（导航、按钮等）通过 `vue-i18n` 的 `import.meta.glob` 在构建时打包

**三级 i18n 加载**：

| 级别 | 机制 | 内容 | 加载时机 |
|------|------|------|---------|
| UI 字符串 | `vue-i18n` glob import | 导航、按钮、页脚、CTA | 构建时（打包在JS中） |
| 页面内容 | `usePageI18n()` → `$fetch('/_i18n/...')` | 标题、FAB、规格、FAQ | 运行时SSR（从Redis） |
| 组件常量 | `useLocaleStrings.ts` 硬编码 | 表单标签、错误提示 | 构建时（TS常量） |

### 8.3 各语言的页面覆盖

由于采用动态路由架构，所有语言理论上共享同一套页面结构。实际覆盖率取决于 Redis/MongoDB 中各语言的翻译数据是否完整。

### 8.4 hreflang 实现

`useHreflang.ts` 自动为每个页面生成多语言交替链接，含自引用（self-referencing）hreflang 标签，用于SEO多语言信号。

---

## 9. SEO策略

### 9.1 技术SEO

1. **SSR服务端渲染**：Nuxt SSR 模式，搜索引擎可直接抓取完整HTML
2. **ISR增量静态再生成**：核心页面按需生成并缓存，兼顾性能与新鲜度
3. **hreflang多语言标签**：`useHreflang.ts` 自动生成6语言交替链接
4. **Schema.org结构化数据**：`useSchemaOrg.ts` 提供 Organization / Product / BreadcrumbList / WebPage 等类型
5. **canonical标签**：各语言页面均设置 self-canonical
6. **sitemap.xml**：动态生成（Nuxt server route，读Redis数据）
7. **robots.txt**：静态文件
8. **Google Indexing API**：`server/utils/googleIndexing.ts` 支持主动推送

### 9.2 内容SEO

1. **SEO管理系统**：后端 `SeoController` 管理关键词、排名跟踪、爬虫任务
2. **CMS文章SEO**：`cms_article_main` + `cms_article_lang` 支持独立SEO标题/描述/关键词
3. **产品页SEO**：每个产品页可通过 `modules.seo` 配置独立SEO元数据
4. **内链系统**：`useBlogInjector` 自动注入产品交叉引用和博客内链

### 9.3 ISR TTL策略

| 语言 | 页面类型 | TTL | 原因 |
|------|---------|-----|------|
| zh/en | industries/process | 24h | 核心页面，更新频率中等 |
| zh/en | article | 7d | 文章内容较稳定 |
| zh/en | faq | 7d | FAQ更新频率中等 |
| ru/ar/es/fr | faq | 14d | 小语种FAQ基本不变 |
| 所有 | 论坛/白皮书 | SSR | 内容实时变化 |
| 所有 | 其他页面 | 1h | 兜底策略 |

---

## 10. 业务核心流程

### 10.1 外贸全链路流程

```
采购商 → 发送询价(Inquiry)
   ↓
销售 → 向供应商询价 → 发送报价(Quotation)
   ↓
确认报价 → 提供形式发票(PI)
   ↓
确认PI → 下发采购订单(PO)给工厂
   ↓
工厂确认订单 → 安排生产
   ↓
发货 → 订舱 → 出口报关(海关)
   ↓
运输货物 → 提供提单(B/L)给客户
   ↓
客户付款(T/T, L/C, D/P, D/A)
   ↓
出口退税(税务局)
   ↓
对账(Statement) → 交易完成
```

### 10.2 四流系统

本项目最核心的业务创新：

- **资金流**（Fund Flow）：收款→结汇→付款跟踪
- **货物流**（Goods Flow）：备货→质检→发货→运输→签收
- **票据流**（Invoice Flow）：发票开具→传递→认证→归档
- **信息流**：合同→PI→PO→B/L→报关单→CO等所有单据流转

四流系统通过 `FollowUpCockpit` 跟单驾驶舱统一管理，支持状态异常自动告警。

---

## 11. 部署架构

### 11.1 部署拓扑

```
新加坡服务器 (47.79.20.2) — 前台
├── Nuxt SSR (PM2/Docker, 端口3000)
│   └── Redis 连接 (本机 6379，页面缓存)
├── Nginx (SSL/Gzip/反向代理)
│   ├── www.lightingmetal.com → Nuxt SSR
│   └── /api/** → proxy_pass → admin.lightingmetal.com
└── MongoDB (172.17.0.1:27017, page_content)

华东服务器 (106.14.210.31) — 后台
├── Spring Boot (Docker, 端口2233, 内部映射8080)
├── MySQL (端口3306, lighting_metal)
├── Redis (47.79.20.2:6379, 业务缓存+页面缓存)
├── Nginx
│   └── admin.lightingmetal.com → service-backend (Vue SPA, 内部端口8081)
└── MongoDB (172.17.0.1:27017)
```

### 11.2 部署脚本

| 脚本 | 用途 |
|------|------|
| `deploy-singapore-frontend.sh` | 新加坡前端部署：华东构建 `.output` → OSS上传 → 新加坡下载 → PM2重启 |
| `deploy-eastchina-app.sh` | 华东后端部署：Git拉取 → Maven构建 → Docker镜像构建 → 容器替换 |
| `deploy-test-customer-portal.sh` | 测试环境前端部署 |
| `restart-backend.sh` | 后端快速重启 |

### 11.3 构建/部署流程

**前端（新加坡）**：
```
华东构建服务器(106.14.210.31)
  → npm run build (nuxt build, 8GB堆内存)
  → 生成 .output/ 目录
  → 打包 tar.gz → 上传 OSS
新加坡服务器(47.79.20.2)
  → 从 OSS 下载 tar.gz
  → 解压到 /var/www/customer-portal/.output
  → 备份旧版本 (.output.bak.{timestamp})
  → PM2 reload
```

**后端（华东）**：
```
Git pull → Maven package → Docker build → 
docker stop 旧容器 → docker run 新容器 → 健康检查 → 清理旧镜像
```

### 11.4 本地开发

PM2 管理两个进程（`ecosystem.config.cjs`）：
- `customer-portal`：Nuxt dev (端口3000)
- `ai-server`：Express AI网关 (端口8080)

---

## 12. AI集成

### 12.1 AI功能模块

| 功能 | 实现 | API/路由 |
|------|------|---------|
| **AI技能管理V3** | CRUD技能配置 | `/api/ai-skill-v3` |
| **AI执行引擎** | 多步骤技能执行 | AiExecutionController |
| **AI网关** | 统一AI接口路由 | `/api/ai/gateway` |
| **CMS内容优化** | 文章SEO全字段优化(DeepSeek-V4) | `CmsAiController.optimize()` |
| **CMS翻译** | 文章多语言翻译(DeepSeek-V4) | `CmsAiController.translate()` |
| **CMS封面生成** | AI生成文章封面图 | `CmsAiController.generateImage()` |
| **邮件AI** | AI生成商务邮件 | `EmailAiController` |
| **报价AI** | AI辅助报价策略 | `QuoteAiController` |
| **前端AI对话** | AI聊天浮窗 | `AiChatWidget.vue` → ai-server:3002 `/api/ai` |
| **产品交叉引用** | AI推荐相关产品 | ai-server:3002 `/api/cross-refs` |

### 12.2 AI服务架构

```
用户浏览器
  ├── AiChatWidget → Nuxt API代理 (/api/ai/**) → ai-server (Express, 端口3002)
  │                                                      │
  │                                                      ├── DeepSeek API
  │                                                      ├── MongoDB (CrossRef模型)
  │                                                      └── Redis (缓存)
  │
  └── 管理后台AI操作 → Spring Boot → OneApiConfig → DeepSeek API
```

### 12.3 AI技能系统

```
SkillConfigDTO (技能配置)
  ├── InputSchemaDTO      输入参数定义
  ├── OutputSchemaDTO     输出配置
  ├── ExecutionConfigDTO  执行配置（模型、温度、提示词）
  └── ResultMappingConfigDTO  结果映射

AiSkillExecutionState     执行状态（多步骤）
  └── AiSkillExecutionStep  执行步骤记录
```

---

## 13. 目录结构总览

```
/app/
├── customer-portal/          # Nuxt 3 多语言外贸前台
│   ├── pages/[lang]/         # 动态路由（20个.vue文件，承载所有语言和行业页面）
│   ├── components/           # 38个公共组件
│   ├── composables/          # 9个组合式函数
│   ├── server/               # Nuxt Server Routes (15+个)
│   ├── plugins/i18n.ts       # vue-i18n 初始化
│   ├── i18n/                 # 翻译JSON (6语言)
│   └── utils/                # 工具函数
│
├── service-backend/          # Vue 3 内部管理系统
│   ├── src/
│   │   ├── api/              # 25个API模块
│   │   ├── views/            # 70+个页面视图
│   │   ├── components/       # 公共组件
│   │   ├── layouts/          # StaffLayout
│   │   └── router/           # 路由配置（含权限守卫）
│   └── vite.config.ts
│
├── lighting-metal-web/       # Spring Boot 3 后端API
│   ├── src/main/java/com/lighting/metal/
│   │   ├── controller/       # 71个REST控制器
│   │   ├── service/          # 100个Service接口 + 88个实现
│   │   ├── mapper/           # 99个MyBatis Mapper
│   │   ├── model/
│   │   │   ├── entity/       # 99个实体类
│   │   │   ├── dto/          # 数据传输对象
│   │   │   └── vo/           # 视图对象
│   │   ├── config/           # 10个配置类
│   │   ├── enums/            # 14个业务枚举
│   │   ├── event/            # 事件类
│   │   ├── filter/           # HTTP过滤器(XSS)
│   │   ├── annotation/       # 自定义注解
│   │   ├── aspect/           # AOP切面
│   │   └── handler/          # 类型处理器
│   └── pom.xml
│
├── ai-server/                # Node.js AI网关
│   ├── src/
│   │   ├── index.js          # Express入口(端口3002)
│   │   ├── routes/           # chat.js, crossRefs.js
│   │   ├── services/         # deepseek.js, cache.js, db.js
│   │   ├── models/           # CrossRef.js
│   │   └── prompts/          # system.md
│   └── package.json
│
├── i18n-backup/              # i18n JSON备份（1768个文件，6语言）
│
├── deploy-singapore-frontend.sh  # 新加坡前端部署
├── deploy-eastchina-app.sh       # 华东后端部署
├── ecosystem.config.cjs          # PM2本地开发配置
├── PROJECT_ANALYSIS.md           # 本文档
│
└── .pi/                      # Pi AI Agent配置
    ├── skills/               # 9个AI技能
    ├── agents/               # 2个AI代理
    └── blackboard/           # Agent协作总线
```

---

## 14. 内容即服务架构（CaaS）

### 14.1 设计理念

前台页面内容与 Java 后端解耦。页面内容存储在 MongoDB，通过 ContentSync 同步到 Redis，Nuxt Server Route 在 SSR 时直接读取缓存。

**优势**：
- 前端页面渲染不依赖 Java 后端可用性
- Redis 字段级缓存提供亚毫秒级读取
- MongoDB 保存完整文档结构，支持灵活的内容编辑
- 同一套 Vue 代码 + 动态路由承载所有语言和行业页面

### 14.2 内容同步管道

```
管理员编辑内容（service-backend）
       │
       ▼
MongoDB page_content (更新)
       │
       ▼  ContentSyncController.syncPage()
Redis (字段级写入: page:{path}:{field}:{lang})
       │
       ▼
Nuxt SSR 读取 → $fetch('/_i18n/{lang}/{path}')
       │
       ▼  优先 Redis，兜底 MongoDB
浏览器渲染 HTML
```

### 14.3 内容层次架构（产品树）

```
L1 行业 (5个)
├── power         (电力与能源)
├── mining        (矿业)
├── agriculture   (农业)
├── industrial    (工业)
└── municipal     (市政)

L2 场景 (每行业5-6个)
power: solar-farm, rooftop-solar, wind-turbine, transmission, energy-storage, hydropower
mining: crushing-grinding, excavatation-tunneling, material-handling, mine-safety-support, screening-classification
agriculture: agro-processing, facility-structure, farm-machinery, livestock-facility, water-irrigation
industrial: electrical-control, factory-structure, industrial-piping, production-equipment, warehouse-logistics
municipal: environmental-sanitation, lighting-power, public-building-furniture, road-bridge, water-supply-drainage

L3 品类 → L4 产品坦克页 (动态路由 [l3]/[l4])
```

---

## 15. 开发注意事项

### 15.1 前端开发

- **页面路由**：所有页面通过 `pages/[lang]/` 动态路由承载，不需要为每个语言创建目录
- **i18n 加载**：UI 字符串在 `i18n/{lang}/*.json` 中维护；页面内容通过 `usePageI18n()` 从 Redis 动态加载
- **新增页面**：在 `pages/[lang]/` 下创建 .vue 文件，Nuxt 自动注册路由
- **组件开发**：所有组件接收 `locale` prop 或通过 `useI18n()` 获取当前语言
- **API 调用**：前台通过 Nuxt API 代理 `/api/**` 调用后端，AI 调用走 `/api/ai/**`
- **SSR 注意**：`localStorage`/`window`/`document` 等浏览器 API 需要 `if (import.meta.client)` 或 `process.client` 判断
- **构建**：使用 `npm run build`（8GB堆内存），产物在 `.output/` 目录
- **内容更新**：修改 MongoDB page_content 后，需执行 ContentSync 同步到 Redis

### 15.2 后端开发

- **新 Controller**：遵循 RESTful 规范，`@RestController` + `@RequestMapping`
- **权限控制**：使用 `@RequiresPermission` 注解
- **操作日志**：使用 `@OperationLog` 注解
- **分页查询**：使用 `BasePageDTO` 作为基类
- **统一响应**：`R.ok(data)` / `R.error(msg)` 格式
- **枚举管理**：新增状态枚举放在 `enums/` 包下
- **配置文件**：开发用 `application-dev.yml`，生产用 `application-prod.yml`

### 15.3 内容管理

- **页面内容**：通过 `ContentQueryController` 查询 MongoDB page_content
- **内容同步**：通过 `ContentSyncController` 将 MongoDB 内容同步到 Redis
- **CMS 文章**：通过 `CmsArticleController` + `CmsMultilingualArticleController` 管理
- **白皮书**：通过 `WhitepaperController` 管理

### 15.4 i18n JSON 文件

- 构建时通过 `import.meta.glob` 打包到 JS bundle 中
- 大量产品翻译数据存放在 `/app/i18n-backup/`（1768个JSON文件），已迁移到 MongoDB/Redis
- `i18n/` 目录下仅保留 UI 字符串和少量静态内容
- 新增翻译 → 更新 MongoDB/Redis，然后执行 ContentSync

### 15.5 部署流程

1. 代码推送到阿里云 Codeup
2. 执行部署脚本（`deploy-singapore-frontend.sh` 或 `deploy-eastchina-app.sh`）
3. 前端：华东构建 → OSS上传 → 新加坡下载 → PM2 reload
4. 后端：Git pull → Maven build → Docker build → 容器替换
5. 验证：检查首页、产品页、多语言、Sitemap 是否正常

---

> **文档更新日志**
> - 2026-05-08：初始版本
> - 2026-05-23：全面架构审计修正 — 重写页面路由架构（[lang]动态路由）、i18n机制（三级加载：vue-i18n glob + usePageI18n Redis + useLocaleStrings）、CaaS内容数据流（MongoDB→Redis→Nuxt Server）、更新Controller/Mapper/Entity精确数量、修正部署拓扑、移除不存在的i18n-pages模板系统和prerender配置、补充ai-server模块、修正枚举/组件/视图数量
