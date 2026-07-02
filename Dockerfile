# 使用Python 3.11作为基础镜像
FROM python:3.11-slim-bookworm AS base

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    DOCKER_ENV=true \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# 设置工作目录
WORKDIR /app

# ==================== Frontend Builder Stage ====================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend

# 复制前端依赖文件
COPY frontend/package*.json ./

# 安装前端依赖
RUN npm ci

# 复制前端源码并构建
COPY frontend/ ./
RUN npm run build

# ==================== Python Builder Stage ====================
FROM base AS builder

# 安装基础依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"

# 复制requirements.txt并安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件（排除 frontend 目录）
COPY . .

# 复制前端构建产物到 static 目录
COPY --from=frontend-builder /static ./static

# 项目已完全开源，无需编译二进制模块

# Runtime stage: only keep what is needed to run the app
FROM base AS runtime

# 设置标签信息
LABEL maintainer="johenking" \
      version="1.0.0" \
      description="Xianyu AI Manager - product-scoped AI reply and operations" \
      repository="https://github.com/johenking/xianyu-ai-manager" \
      license="AGPL-3.0" \
      author="Xianyu AI Manager contributors" \
      build-date="" \
      vcs-ref=""

ENV NODE_PATH=/usr/lib/node_modules

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nodejs \
        tzdata \
        curl \
        ca-certificates \
        libjpeg-dev \
        libpng-dev \
        libfreetype6-dev \
        fonts-dejavu-core \
        fonts-liberation \
        libgl1 \
        libglib2.0-0 \
        && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 设置时区
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 验证Node.js安装；PyExecJS运行时需要可用的JS引擎
RUN node --version

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"

RUN python -m playwright install --with-deps chromium && \
    printf '%s\n' \
      '#!/usr/bin/env sh' \
      'set -e' \
      'chrome="$(find /ms-playwright -path "*/chrome-linux/chrome" -type f | head -n 1)"' \
      'if [ -z "$chrome" ]; then' \
      '  echo "Playwright Chromium executable not found" >&2' \
      '  exit 1' \
      'fi' \
      'exec "$chrome" "$@"' \
      > /usr/local/bin/chromium && \
    chmod +x /usr/local/bin/chromium && \
    ln -sf /usr/local/bin/chromium /usr/bin/chromium

# 创建必要的目录并设置权限
RUN mkdir -p /app/logs /app/data /app/backups /app/static/uploads/images && \
    chmod 777 /app/logs /app/data /app/backups /app/static/uploads /app/static/uploads/images

# 配置系统限制，防止core文件生成
RUN echo "ulimit -c 0" >> /etc/profile

# 注意: 为了简化权限问题，使用root用户运行
# 在生产环境中，建议配置适当的用户映射

# 暴露端口
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD sh -c 'curl -f "http://localhost:${PORT:-8080}/health" || exit 1'

RUN chmod +x /app/entrypoint.sh

# 启动命令
CMD ["/app/entrypoint.sh"]
