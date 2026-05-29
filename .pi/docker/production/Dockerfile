FROM yaxiio-sandbox:lightingmetal
LABEL org.opencontainers.image.title="Yaxiio Production"
LABEL org.opencontainers.image.version="1.0.0-prod"
RUN python3.12 -m pip install --no-cache-dir websockets aiohttp
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Shanghai
EXPOSE 3003 3398 3399
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD redis-cli -a "Yaxiio2026" ping && curl -f http://localhost:3399/health || exit 1
ENTRYPOINT ["/entrypoint.sh"]
