# lightingmetal.com 部署流程

## 铁律
- ❌ **绝不碰线上 nginx**
- ❌ **绝不 scp 到 HK**（慢且不可靠）
- ❌ **绝不用 OSS 中转**（要钱且慢）
- ✅ **用 106.14.210.31:3004 HTTP 传输**

## 部署步骤

### 1. 本地构建
```bash
cd /app/lightingmetal/customer-portal
rm -rf .nuxt .output
npx nuxi build
tar -czf /tmp/lightingmetal-$(date +%Y%m%d-%H%M%S).tar.gz .output i18n
```
产物命名：`lightingmetal-{时间戳}.tar.gz`，约 12MB。

### 2. 上传到 yaxiio 3004 端口
```bash
docker exec yaxiio bash -c '
pm2 stop agent-board 2>/dev/null
cd /tmp
nohup python3 -m http.server 3004 > /dev/null 2>&1 &
'
```
板子端口 3004 复用，传完恢复：
```bash
docker exec yaxiio bash -c '
pkill -f "http.server 3004" 2>/dev/null
pm2 start agent-board
'
```

### 3. HK 服务器下载并部署
```bash
cat /tmp/lightingmetal-*.tar.gz | ssh root@47.79.20.2 '
  curl -s -o /tmp/deploy.tar.gz http://106.14.210.31:3004/文件名.tar.gz
  cd /opt/lightingMetal/customer-portal
  rm -rf .output
  tar -xzf /tmp/deploy.tar.gz
  docker restart nuxt-app
'
sleep 5  # 等 Nuxt 启动
```
**下载约 60 秒**（12MB @ 200KB/s），不用催。

### 4. 验证
```bash
curl -sk -o /dev/null -w "%{http_code}" https://www.lightingmetal.com/en
# 应返回 200
```

## 数据变更流程（改 MongoDB/Redis）

### 改 MongoDB → 同步到 Redis
```bash
# 1. 写 MongoDB
docker exec mongodb mongosh --eval '...'

# 2. 从 yaxiio 推送到 HK Redis（精确字段，不全量）
docker exec yaxiio python3 -c "
import redis; from pymongo import MongoClient
mc = MongoClient('mongodb://172.17.0.1:27017')
r = redis.Redis(host='47.79.20.2', port=6379, decode_responses=True)
# 读取 MongoDB → 推送 Redis
r.set(key, value)
"

# 3. 触达增量 sync（更新 updatedAt，等 CMS 的 10 分钟定时同步）
docker exec mongodb mongosh --eval 'db.page_content.updateMany({...}, {$set:{updatedAt:new Date()}})'
```

### 只改 Redis（临时热修复）
```bash
ssh root@47.79.20.2 'docker exec redis-cross-refs redis-cli SET key value'
docker restart nuxt-app  # 清缓存
```

## 禁忌操作
- 不要在 yaxiio 容器里 `git checkout` 后再 `npx nuxi build`——server 文件（i18n handler、sitemap、redis.ts）有未提交的修复，会被回滚掉
- 不要改 `useIndustryTheme.ts` 加图片字段——图片 URL 应该在页面代码里直接拼
- 删 MongoDB 静态页文档时，同步删对应 Redis `page:*` 键
- SSH 用完后确保连接关闭，避免 HK 连接数耗尽

## 回滚
HK 服务器 `/tmp/` 下有历史 tar.gz，`ls -t /tmp/lightingmetal-*.tar.gz | head -3` 查看最近三个版本。
