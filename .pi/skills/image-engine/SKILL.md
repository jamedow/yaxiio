# ImageEngine — 网站图片生成引擎

## 职责
为 LightingMetal 网站生成专业工业产品图片，覆盖：
- **Banner/Hero 图**：主力页面 Hero 横幅 (1920×800)
- **行业场景图**：五大行业赛道场景配图 (1080×720)
- **卡片配图**：IndustryCard / TrustCard 缩略图 (800×600)
- **产品细节图**：L4 产品详情页工艺展示图 (1024×1024)

## 技术栈
- **生成模型**：gpt-image-2 (One API @ 172.17.0.1:3000)
- **存储**：阿里云 OSS (lightingmetal-deploy/images/site/)
- **配置**：Redis page_content 字段更新

## 调用方式
```json
{
  "action": "generate_image",
  "category": "hero|card|scene|product",
  "page": "power|agriculture|industrial|mining|municipal",
  "scene": "solar-farm",
  "product": "hex-bolts",
  "style": "dark-industrial|warm-factory|clean-studio"
}
```

## 输出
- 生成图片 → 下载 → 上传 OSS → 返回 URL → 更新 Redis
- 报告写入 /app/.pi/blackboard/reports/image-*.md

## 质量要求
- 工业产品真实感，非卡通
- 光线自然，材质纹理清晰
- 符合各行业视觉主题色调
- 无中文水印/文字
