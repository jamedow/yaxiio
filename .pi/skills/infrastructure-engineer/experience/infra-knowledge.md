# LightingMetal 基础设施配置
# ===========================
# Yaxiio & Agent 自动加载此配置, 无需人工复述

## 服务器

| 名称 | IP | 角色 |
|------|-----|------|
| 华东2 | 106.14.210.31 | Yaxiio容器 + Spring Boot后端 + MySQL + 构建环境 |
| 香港 | 47.79.20.2 | Nuxt前端 + Redis + MongoDB (page_content) |

## 服务端口

| 服务 | 地址 | 端口 |
|------|------|:--:|
| Yaxiio Dashboard | 106.14.210.31 | 3003 |
| Spring Boot API | 106.14.210.31 | 2233 |
| Nuxt SSR | 47.79.20.2 | 3000 (容器内) |
| HK Redis | 47.79.20.2 | 6379 |
| MongoDB | 106.14.210.31 | 27017 |

## 数据通路

```
MongoDB (华东, page_content集合)
    ↓ Spring Boot ContentSync API: /api/admin/sync/
Redis (HK, page:{path}:{field}:{lang})
    ↓ Nuxt SSR (ISR缓存)
浏览器 (用户)
```

## 发布流程

```
1. 华东 Yaxiio 容器内构建:
   cd /app/lightingmetal/customer-portal
   npx nuxi build
   tar -czf /tmp/nuxt-fix.tar.gz .output i18n

2. 传输到 HK:
   sshpass scp /tmp/nuxt-fix.tar.gz root@47.79.20.2:/tmp/

3. HK 部署:
   ssh root@47.79.20.2 "
     cd /opt/lightingMetal/customer-portal
     find .output -delete
     tar -xzf /tmp/nuxt-fix.tar.gz
     docker restart nuxt-app
   "
```

## SSH 凭据

- HK: root@47.79.20.2, sshpass密码 Zhangliang@520
- 华东: root@106.14.210.31

## 语言

- 源语言: zh (中文)
- 目标: en (英语), ru (俄语), ar (阿拉伯语), es (西班牙语)
- 法语 fr 仅 25 页, 未完成

## 行业

- power (电力与能源), mining (矿业), agriculture (农业)
- industrial (工业), municipal (市政)

## 品牌

- 钟离金 #D4A843, 岩黑 #14110F, 丹霞橙 #C87D4A
- Nuxt 3 + TailwindCSS, ISR 缓存需 docker restart 清除
