# 部署指南

## 生产镜像

```bash
cd .pi/docker/production
docker build -t yaxiio:prod .
```

## 启动

```bash
docker run -d --name yaxiio \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /opt/yaxiio:/opt/yaxiio \
  -v /opt/lightingMetal:/opt/lightingMetal \
  -v yaxiio-data:/data \
  -p 3398:3398 -p 3399:3399 \
  -e DEEPSEEK_API_KEY="sk-xxx" \
  --restart unless-stopped \
  yaxiio:prod
```

## 验证

```bash
# 健康检查
curl http://localhost:3399/health

# 系统指标
curl http://localhost:3399/metrics

# 查看日志
docker logs yaxiio
docker exec yaxiio pm2 logs
```

## 启动链路

```
entrypoint.sh
  → redis-server (127.0.0.1:6379)
  → PM2 → pi_guardian_v3.py
           → Commander.run() (yaxiio.py)
  → Gateway (WS:3398 + HTTP:3399)
```

## 沙箱镜像

```bash
cd .pi/docker/sandbox
# 需要先构建 yaxiio-sandbox:lightingmetal
docker build -t yaxiio-sandbox:lightingmetal .
```
