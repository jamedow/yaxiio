# lightingmetal.com 数据变更工作流

## 数据链路

```
CMS 后台 / 迁移脚本 / 手动
        │
        ▼
   MongoDB (106.14.210.31)
   page_content 集合
        │
        ▼  ContentSyncTask（每 10 分钟增量 / 每日 3:00 全量）
        │  或手动精确推送
        ▼
   HK Redis (47.79.20.2:6379, 容器 redis-cross-refs)
   key 格式: page:{path}:{field}:{lang}
        │
        ▼  i18n API / loadPageI18n
   Nuxt 前端 (customer-portal)
```

## 工作流一：新增/修改页面内容（翻译、图片等）

### 触发场景
- CMS 后台编辑页面
- 翻译引擎批量翻译
- 手动补充内容（如 heroImg）

### 步骤
```bash
# 1. 写 MongoDB
docker exec mongodb mongosh --quiet --eval "
  db = db.getSiblingDB('lightingmetal')
  db.page_content.updateOne(
    {path: '/{lang}/{page}', pageType: 'static'|'process'|'industry'},
    {\$set: {'content.{field}': '{value}', updatedAt: new Date()}}
  )
"

# 2. 精确推送到 HK Redis（不等 10 分钟）
docker exec yaxiio python3 -c "
  import redis; from pymongo import MongoClient
  mc = MongoClient('mongodb://172.17.0.1:27017')
  col = mc.lightingmetal.page_content
  r = redis.Redis(host='47.79.20.2', port=6379, decode_responses=True)
  doc = col.find_one({'path': '/{lang}/{page}'}, {'content': 1})
  for k, v in doc['content'].items():
      r.set('page:{page}:{k}:{lang}', v)
  r.close(); mc.close()
"

# 3. 重启 Nuxt 清 i18n 缓存
ssh root@47.79.20.2 'docker restart nuxt-app && sleep 5'
```

### 注意
- `updatedAt: new Date()` 确保增量同步也能捡到
- 精确推送只推目标页面，不推全量
- Redis key 格式：`page:{path}:{field}:{lang}`，path 不含 `/` 用 `:` 代替

---

## 工作流二：新增字段（之前不存在的 key）

### 触发场景
- 模板加了新字段（如 heroImg）
- 页面需要新的 i18n key

### 步骤
```bash
# 1. MongoDB 写入新字段（5 语言 × 5 行业 = 25 条）
# 2. 精确推送到 Redis
# 3. 代码里引用新字段：t('power.heroImg')
# 4. 构建 + 部署（走部署流程）
```

### 注意
- 先推 Redis，再部署代码。代码上线时 Redis 已有数据

---

## 工作流三：删除数据（清理错误文档）

### 触发场景
- CMS 误建静态页（如 /en/power）
- 测试数据清理

### 步骤
```bash
# 1. MongoDB 删除
docker exec mongodb mongosh --eval "
  db.page_content.deleteOne({path: '/{lang}/{page}'})
"

# 2. Redis 清理（精确匹配，不删其他 page:* 键）
ssh root@47.79.20.2 "
  docker exec redis-cross-refs redis-cli KEYS 'page:{page}:*:{lang}' | \
  xargs -r docker exec -i redis-cross-refs redis-cli DEL
"

# 3. 重启 Nuxt
ssh root@47.79.20.2 'docker restart nuxt-app && sleep 5'
```

### 注意
- Redis 用 KEYS + xargs DEL，批处理，不要全量 KEYS 再逐个 DEL
- 删 MongoDB 后必须删 Redis——Nuxt 从 Redis 读数据，不删会继续渲染

---

## 工作流四：仅 Redis 热修（不改 MongoDB）

### 触发场景
- 紧急修复线上数据
- MongoDB 不可用时的临时方案

### 步骤
```bash
ssh root@47.79.20.2 "
  docker exec redis-cross-refs redis-cli SET 'page:{page}:{field}:{lang}' '{value}'
"
ssh root@47.79.20.2 'docker restart nuxt-app'
```

### 注意
- ⚠️ 这只是热修，下次 MongoDB 同步会覆盖。必须同步改 MongoDB

---

## 工作流五：批量操作（多页面/多语言/多字段）

### 触发场景
- 翻译引擎批量翻译
- 批量配图（如 5 行业 × 5 语言 heroImg）

### 步骤
```bash
# 1. Python 脚本批量写 MongoDB + Redis
docker exec yaxiio python3 << 'PYEOF'
from pymongo import MongoClient
import redis

mc = MongoClient('mongodb://172.17.0.1:27017')
col = mc.lightingmetal.page_content
r = redis.Redis(host='47.79.20.2', port=6379, decode_responses=True)

for lang in ['en','zh','ru','ar','es']:
    for page in ['power','agriculture']:
        # MongoDB
        col.update_one(
            {'path': f'/{lang}/{page}'},
            {'$set': {'content.heroImg': f'https://.../{page}.webp'}}
        )
        # Redis
        r.set(f'page:{page}:heroImg:{lang}', f'https://.../{page}.webp')

mc.close(); r.close()
print('Done')
PYEOF

# 2. 重启
ssh root@47.79.20.2 'docker restart nuxt-app && sleep 5'
```

---

## 禁忌
| 操作 | 为什么不行 |
|------|-----------|
| 只改 Redis 不改 MongoDB | 下次同步会覆盖 |
| 只改 MongoDB 不推 Redis | 要等 10 分钟增量同步 |
| 全量 `KEYS *` 再 DELETE | 15000+ key，极慢 |
| 删 MongoDB 不删 Redis | 页面继续渲染已删内容 |
| 推完不重启 Nuxt | i18n 有内存缓存 |
