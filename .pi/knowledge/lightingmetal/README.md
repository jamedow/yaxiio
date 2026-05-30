# LightingMetal 项目知识库

> 最后更新: 2026-05-30 | Yaxiio v3.1

## 项目概述
LightingMetal 是五金CNC精密加工外贸B2B独立站。
- 域名: https://www.lightingmetal.com
- 品牌: 钟离金 #D4A843 | 岩黑 #14110F
- 技术栈: Nuxt 3 + TailwindCSS + MongoDB + Redis
- 语言: zh / en / ru / ar / es
- L4 产品页面: 306 个（5 大行业）

## 页面树
权威结构定义在 `customer-portal/doc/pages-tree.md`，所有节点可点击。

### 五大行业 L2 slug
| 行业 | L2 slug |
|------|--------|
| power | solar-farm, rooftop-solar, wind-turbine, transmission, energy-storage, hydropower |
| agriculture | water-irrigation, facility-structure, livestock-facility, agro-processing, farm-machinery |
| mining | crushing-grinding, excavation-tunneling, material-handling, mine-safety-support, screening-classification |
| industrial | electrical-control, factory-structure, industrial-piping, production-equipment, warehouse-logistics |
| municipal | lighting-power, water-supply-drainage, public-building-furniture, road-bridge, environmental-sanitation |

## URL 结构
`/{lang}/industries/{sector}/{l2_en}/{l3_en}/{l4_en}`

## 数据通路
MongoDB (page_content) → sync → HK Redis → Nuxt SSR → 用户
i18n JSON 本地缓存: `i18n/{lang}/industries/{sector}/{l2}/{l3}/{l4}.json`

## MongoDB page_content 结构
```json
{
  "lang": "zh",
  "path": "/zh/industries/power/solar-farm/...",
  "content": { "slug": { "heroTitle": "...", "spec1Param": "...", ... } }
}
```
