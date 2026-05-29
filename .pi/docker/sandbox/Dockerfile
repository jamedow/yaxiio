# ═══════════════════════════════════════════════════════════════
# Yaxiio Sandbox — LightingMetal 全栈调试部署环境
# 基于 lightingmetal-dev-image:v2 (AlmaLinux 8, JDK17, Node20)
# ═══════════════════════════════════════════════════════════════
FROM lightingmetal-dev-image:v2

LABEL org.opencontainers.image.title="Yaxiio Sandbox - LightingMetal"
LABEL org.opencontainers.image.description="全栈调试部署：JDK17 + Node20 + Python3.12 + Maven + Docker CLI + SSH"
LABEL org.opencontainers.image.version="1.0.0"

# ── 系统工具 ──
RUN dnf install -y --nodocs --setopt=install_weak_deps=False \
    python3.12 python3.12-pip python3.12-devel \
    redis \
    sshpass vim htop net-tools bind-utils jq \
    && dnf clean all && rm -rf /var/cache/dnf

# ── mongosh (via npm) ──
RUN npm install -g mongosh 2>&1 | tail -3

# ── Python 依赖 (清华镜像) ──
RUN python3.12 -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && python3.12 -m pip install --no-cache-dir redis openai pymongo flask requests pypinyin

# ── Docker CLI（阿里云镜像，操作宿主机 Docker）──
RUN echo -e '[docker-ce-stable]\nname=Docker CE Stable\nbaseurl=https://mirrors.aliyun.com/docker-ce/linux/centos/8/x86_64/stable/\ngpgcheck=0\nenabled=1' \
      > /etc/yum.repos.d/docker-ce.repo \
    && dnf install -y --nodocs docker-ce-cli docker-buildx-plugin \
    && dnf clean all && rm -rf /var/cache/dnf

# ── 工作目录 ──
RUN mkdir -p /workspace /app/lightingmetal /app/.pi
WORKDIR /workspace

ENV PYTHONUNBUFFERED=1
ENV LANG=en_US.UTF-8
ENV TZ=Asia/Shanghai

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]
