#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images

export API_HOST="${API_HOST:-0.0.0.0}"
export API_PORT="${PORT:-${API_PORT:-8080}}"

# 容器内为 headed Chromium 提供真实 DISPLAY。
# 不使用 xvfb-run：它作为 PID 1 时收不到 Xvfb 的 SIGUSR1 就绪信号会在启动
# python 之前死锁，且不向 python 转发 SIGTERM（容器只能被强杀）。
# 改为后台 Xvfb + exec python，保持 python 为 PID 1、优雅停机语义不变。
export DISPLAY=":99"
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
Xvfb "$DISPLAY" -screen 0 1440x960x24 -nolisten tcp &

# 最多等 5 秒让 X socket 就绪；超时不阻塞启动（浏览器路径会归类 browser_error）
for _ in $(seq 1 50); do
    if [ -S /tmp/.X11-unix/X99 ]; then
        break
    fi
    sleep 0.1
done

exec python Start.py
