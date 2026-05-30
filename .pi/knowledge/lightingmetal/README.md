# LightingMetal 项目知识库

> 最后更新: 2026-05-28 | Yaxiio v1.10

## 项目概述

LightingMetal 是五金CNC精密加工外贸B2B独立站，面向一带一路沿线国家。

- 域名: https://www.lightingmetal.com
- 品牌: 钟离金 #D4A843 | 岩黑 #14110F | 丹霞橙 #C87D4A
- 技术栈: Nuxt 3 + TailwindCSS + Spring Boot + MyBatis-Plus + Redis + MongoDB
- 语言: zh(源) / en / ru / ar / es

## 服务器

| 服务器 | IP | 运行服务 |
|------|-----|------|
| 华东2 | 106.14.210.31 | Yaxiio容器, Spring Boot(:2233), MySQL(:3306), MongoDB(:27017) |
| 香港 | 47.79.20.2 | Nuxt容器(:3000), Redis(:6379) |

## 数据通路

```
MongoDB (华东) → Spring Boot ContentSync → Redis (HK) → Nuxt SSR → 用户
```

- Redis key 格式: `page:{path}:{pageKey}.{field}:{lang}`
- 例: `page:industries:power:solar-farm:solar-farm-ground-screw.heroTitle:zh`
- 注意: 路径中不含语言前缀, lang 仅在末尾后缀

## 页面结构

5 大行业 L1-L4 树:
- power (电力与能源): solar-farm, rooftop-solar, wind-turbine, transmission, energy-storage, hydropower
- mining (矿业): crushing-grinding, excavation-tunneling, material-handling, mine-safety-support, screening-classification
- agriculture (农业): agro-processing, facility-structure, farm-machinery, livestock-facility, water-irrigation
- industrial (工业): electrical-control, factory-structure, industrial-piping, production-equipment, warehouse-logistics
- municipal (市政): environmental-sanitation, lighting-power, public-building-furniture, road-bridge, water-supply-drainage

详细页面树: /app/lightingmetal/customer-portal/doc/pages-tree.md

## 发布流程

1. 华东构建: `cd /app/lightingmetal/customer-portal && npx nuxi build`
2. 验证钩子: `grep -c 'const pageKey' .output/server/chunks/routes/_i18n/_lang/_...path_.get.mjs` 必须 > 0
3. 打包: `tar -czf /tmp/push.tar.gz .output i18n`
4. HTTP 服务: `python3 -m http.server 3004` (端口已映射)
5. HK 下载: `ssh root@47.79.20.2 'curl 华东IP:3004/push.tar.gz -o /tmp/push.tar.gz'`
6. HK 部署: `docker stop nuxt-app && rm -rf .../.output && tar -xzf && docker start nuxt-app`
7. 关 SSH: `pkill -f "ssh.*47.79.20.2"`

## Agent 配置

| Agent | Skill | 模型 | 推理深度 |
|------|------|------|:--:|
| 翻译官 | translate-engine | deepseek-chat | low |
| 审计官 | audit-engine | deepseek-chat | high |
| UI/UX设计师 | ui-ux-designer | deepseek-chat | medium |
| 品牌策略师 | strategic-partner | deepseek-chat | high |
| 前端工程师 | infrastructure-engineer | deepseek-chat | medium |
| LM内容工程师 | lm-content-engineer | deepseek-chat | medium |
| 系统医生 | system-doctor | deepseek-chat | high |
| 售前经理 | product-search | deepseek-chat | low |

## 关键 Redis Key

| Key | 用途 |
|------|------|
| yaxiio:config:infra | 基础设施知识 |
| yaxiio:config:llm_api_key | DeepSeek API Key |
| yaxiio:model:config:{agent} | Agent 模型热配置 |
| page:{path}:{field}:{lang} | 页面内容 |
