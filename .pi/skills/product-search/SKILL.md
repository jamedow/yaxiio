---
name: product-search
description: LightingMetal产品搜索技能。查询MongoDB page_content集合，按行业/品类/关键词搜索产品，支持规格查询、跨品类对比、价格区间参考。当需要查产品、搜规格、找替代方案、出报价时使用此技能。
---

# Product Search — 产品搜索引擎 v1.0

## ⛔ Constitution

**R1**：报价仅供参考。所有价格标注为 `FOB参考区间`，实际价格以工厂确认为准。禁止给出精确承诺价。

**R2**：数据源唯一。优先查 MongoDB `page_content` 集合，其次查 i18n JSON 文件。禁止编造不存在的产品。

**R3**：标准号完整。引用技术标准时必须包含标准号和名称（如 `GB/T 13912 热浸镀锌层技术要求`），不可只写编号。

## 🎯 查询模式

### 按行业+品类搜索
```bash
docker exec mongodb mongosh --quiet --eval "
  const db = db.getSiblingDB('lightingmetal');
  db.page_content.find(
    {lang:'zh', industry:'{industry}', l2:'{l2}', pageType:'l4'},
    {path:1, 'content.heroTitle':1}
  ).limit(20).forEach(d => print(d.path));
"
```

### 按关键词全文搜索
```bash
docker exec mongodb mongosh --quiet --eval "
  const db = db.getSiblingDB('lightingmetal');
  const results = [];
  db.page_content.find({lang:'zh'}).forEach(doc => {
    const c = doc.content;
    if (!c) return;
    const flat = JSON.stringify(c).toLowerCase();
    if (flat.includes('{keyword}')) {
      const inner = Object.values(c)[0];
      results.push({path: doc.path, title: inner?.heroTitle || doc.path, industry: doc.industry});
    }
  });
  results.slice(0, 10).forEach(r => print(r.industry + ' | ' + r.title + ' | ' + r.path));
"
```

### 查产品完整规格
```bash
# 替换 {productPath} 为具体产品路径
docker exec mongodb mongosh --quiet --eval "
  const db = db.getSiblingDB('lightingmetal');
  const doc = db.page_content.findOne({lang:'zh', path:'{productPath}'});
  if(!doc) { print('Product not found'); quit(); }
  const c = doc.content;
  const inner = Object.values(c)[0];
  // 关键字段
  ['heroTitle','heroSubtitle','spec1Param','spec1Value','spec2Param','spec2Value','spec3Param','spec3Value',
   'pain1Title','std1','std2','std3','faq1Question','faq1Answer','ctaTitle'].forEach(k => {
    if(inner[k]) print(k + ': ' + inner[k]);
  });
"
```

### 跨品类对比
```bash
# 对比多个产品的规格
docker exec mongodb mongosh --quiet --eval "
  const db = db.getSiblingDB('lightingmetal');
  const paths = ['{path1}', '{path2}'];
  paths.forEach(p => {
    const doc = db.page_content.findOne({lang:'zh', path:p});
    if(!doc) return;
    const inner = Object.values(doc.content)[0];
    print('\\n=== ' + (inner.heroTitle || p) + ' ===');
    ['spec1Param','spec1Value','spec2Param','spec2Value','spec3Param','spec3Value'].forEach(k => {
      if(inner[k]) print('  ' + k + ': ' + inner[k]);
    });
  });
"
```

## 🏷️ 产品树快速索引

| 行业 | L2目录 | 品类数 | 典型产品 |
|------|--------|:--:|------|
| power | solar-farm | 6 | 螺旋地桩, MC4连接器, 接地极, 中压块 |
| power | rooftop-solar | 6 | 彩钢瓦夹具, 铝合金导轨, 防水垫圈 |
| power | wind-turbine | 6 | 法兰螺栓, 锚栓笼, 叶片螺柱, 超级螺母 |
| power | transmission | 6 | 悬垂线夹, 防振锤, 电缆桥架, 接地极 |
| power | energy-storage | 4 | 电池模组螺栓, T型螺母, 铜排, 泄爆板 |
| power | hydropower | 4 | 压力管道螺柱, 转轮叶片螺栓, 闸门密封 |
| mining | 开采/破碎/输送/筛分/支护 | 5 | 破碎机衬板, 筛网, 输送带扣, 锚杆 |
| agriculture | 灌溉/温室/畜牧/加工/农机 | 5 | 灌溉管卡, 温室连接件, 栏舍U型螺栓 |
| industrial | 钢结构/生产线/管道/电气/仓储 | 5 | 高强螺栓, 地脚锚栓, 管道法兰, DIN导轨 |
| municipal | 供水/道路/建筑/照明/环保 | 5 | 球墨管密封圈, 护栏螺栓, 井盖, 路灯地脚 |

## 💰 价格参考区间 (FOB 宁波)

| 品类 | 材质等级 | 规格范围 | 价格区间 | 单位 |
|------|---------|---------|---------|------|
| 螺栓/螺母 | 4.8/8.8级 碳钢 | M6-M20 | $0.05-0.50 | pc |
| 螺栓/螺母 | 10.9/12.9级 合金钢 | M12-M36 | $0.20-3.00 | pc |
| 螺栓/螺母 | 304/316 不锈钢 | M6-M20 | $0.10-2.00 | pc |
| 螺旋地桩 | Q235B 热镀锌 | φ76×1200-2100mm | $5-20 | pc |
| MC4连接器 | 铜合金+PPO | 30-50A/1500V | $1-5 | pair |
| 电缆桥架 | 热镀锌钢 | 100-600mm宽 | $3-15 | m |
| 接地极 | 铜覆钢 | φ14-20×1500-3000mm | $3-25 | pc |
| 耐磨衬板 | 高锰钢/铬钼合金 | 定制尺寸 | $2-8 | kg |
| 筛网 | 65Mn/304 | 1-100mm孔径 | $5-50 | m² |

> ⚠️ 以上为2026年5月参考区间，实际价格受钢材期货行情、汇率、订单量影响。大货订单(>10吨)可议价。
