# ImageDeploy — 图片部署技能

## 职责
将 ImageAgent 生成的图片部署到 OSS 并更新网站配置。

## 流程
1. 接收图片 URL（来自 ImageAgent）
2. 下载图片到本地
3. 转 webp 格式（可选压缩）
4. 上传 OSS 到对应目录
5. 更新 Redis page_content 图片字段
6. 报告部署状态

## 目录规范
- `hero/{industry}.webp` — 行业 Hero 横幅
- `card/{industry}-{scene}.webp` — 场景卡片图
- `scene/{industry}-{scene}.webp` — 场景详情图
- `process/{process}.webp` — 工艺页面配图

## 配置
- OSS: lightingmetal-deploy/images/site/
- CDN: https://lighting-metal.oss-cn-hongkong.aliyuncs.com/images/site/
- Redis: 更新 page:{industry}:{scene}Img:{lang}
