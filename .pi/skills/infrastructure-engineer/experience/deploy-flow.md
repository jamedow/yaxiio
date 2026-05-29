# LightingMetal 前端发布 Agent Skill
# =================================
# 角色: LightingMetal 独立站发布工程师
# 挂载: infrastructure-engineer Skill 的子能力

## 发布流程

当收到 "deploy" 或 "发布" 任务时, 按以下步骤执行:

### Step 0: 清理旧 SSH 连接 (防止达到上限)
```bash
sshpass -p 'Zhangliang@520' ssh -o StrictHostKeyChecking=no root@47.79.20.2 "exit"
pkill -f "ssh.*47.79.20.2" 2>/dev/null
```

### Step 1: 构建 (华东 106.14.210.31)
```bash
cd /app/lightingmetal/customer-portal
npx nuxi build 2>&1 | tail -5
tar -czf /tmp/nuxt-deploy-$(date +%H%M).tar.gz .output i18n
```
超时: 300s

### Step 1.5: 构建验证 (钩子)
确认关键修复已编译进产物:
```bash
# 验证 i18n 路由包含 pageKey 修复
grep -c 'const pageKey' .output/server/chunks/routes/_i18n/_lang/_...path_.get.mjs
# 返回值必须 > 0, 否则构建未包含修复

# 验证 .output 时间戳是最近的
ls -la .output/nitro.json
```

### Step 2: 传输到 HK
```bash
sshpass -p 'Zhangliang@520' scp -o StrictHostKeyChecking=no \
  /tmp/nuxt-deploy-*.tar.gz root@47.79.20.2:/tmp/
```

### Step 3: HK 部署
```bash
sshpass -p 'Zhangliang@520' ssh -o StrictHostKeyChecking=no root@47.79.20.2 \
  "docker stop nuxt-app && \
   rm -rf /opt/lightingMetal/customer-portal/.output && \
   tar -xzf /tmp/nuxt-deploy-*.tar.gz -C /opt/lightingMetal/customer-portal/ && \
   docker start nuxt-app && \
   echo DEPLOY_OK"
```

### Step 5: 验收
等待 8 秒后, curl 验证关键页面:
```bash
curl -sL -m 10 'https://www.lightingmetal.com/zh/industries/power/solar-farm/solar-farm-foundation-structure' | grep -c '基础与支架'
```
返回值 > 0 表示部署成功。

### Step 6: 关闭 SSH 连接
```bash
pkill -f "ssh.*47.79.20.2" 2>/dev/null
```
防止 SSH 连接数达到上限。

## 快速重启 (不清缓存, 仅重启)
```bash
sshpass -p 'Zhangliang@520' ssh root@47.79.20.2 'docker restart nuxt-app'
```

## 紧急回滚
```bash
sshpass -p 'Zhangliang@520' ssh root@47.79.20.2 \
  "cd /opt/lightingMetal/customer-portal && \
   tar -xzf /tmp/nuxt-backup-*.tar.gz && \
   docker restart nuxt-app"
```
