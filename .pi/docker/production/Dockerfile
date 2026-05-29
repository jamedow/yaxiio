FROM yaxiio-sandbox:lightingmetal
LABEL org.opencontainers.image.title="Yaxiio Production"
LABEL org.opencontainers.image.version="1.0.0-prod"

# Redis 7 (替代系统自带的 5.0)
RUN dnf install -y --nodocs redis 2>/dev/null || true

# Gateway 依赖
RUN python3.12 -m pip install --no-cache-dir websockets aiohttp

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
EXPOSE 3003 3398 3399

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD redis-cli -a "Yaxiio2026" ping || exit 1

ENTRYPOINT ["/entrypoint.sh"]
