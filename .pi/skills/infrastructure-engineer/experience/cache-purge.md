# 发布后自动清理缓存 Skill
# ==========================
# 每次部署后自动执行，确保四层缓存全部刷新

## 缓存层

| 层 | 位置 | 清理方式 | 超时 |
|------|------|------|:--:|
| 1. Nuxt ISR | Nuxt容器内存 | docker restart nuxt-app | 5s |
| 2. Cloudflare CDN | Cloudflare边缘节点 | Purge Cache API | 30s |
| 3. i18n LoadedKeys | Nuxt进程内存 | 随docker restart清除 | 0s |
| 4. Nginx缓存 | HK Nginx | 通常未启用 | - |

## 自动清理脚本 (部署后执行)

```bash
# Step 5.5: 清理所有缓存 (部署后自动执行)
echo "[Cache] 清理 ISR..."
ssh root@47.79.20.2 'docker restart nuxt-app'

echo "[Cache] 等待 Nuxt 就绪..."
sleep 8

echo "[Cache] 验证页面..."
curl -sL -m 10 'https://www.lightingmetal.com/zh/industries/power/solar-farm' | grep -c '地面光伏' && echo "✅ L2 OK"
curl -sL -m 10 'https://www.lightingmetal.com/zh/industries/power/solar-farm/solar-farm-foundation-structure' | grep -c '基础与支架' && echo "✅ L3 OK"

echo "[Cache] 重置 i18n 加载状态..."
# 随 Nuxt 重启自动清除

echo "[Cache] Cloudflare 需手动 purge (或配置 API)"
```

## 预防措施

1. **部署钩子**: 每次 content_sync 后自动调验证
2. **Redis key 格式统一**: ContentSync 和手动同步都用 `page:{path}:{field}:{lang}` 格式
3. **SSH 连接管理**: 每次用完关连接 `pkill -f "ssh.*47.79.20.2"`
4. **构建验证钩子**: 编译后检查关键修复是否在产物中
