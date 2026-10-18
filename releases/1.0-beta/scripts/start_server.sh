#!/usr/bin/env bash
# 启动 talk2gemi Mac 中继服务
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

if [[ ! -d ".venv" ]]; then
    echo "❌ 虚拟环境不存在，请先创建 talk2gemi/.venv"
    exit 1
fi

PORT=8765
echo "🧹 检查并清理端口 ${PORT}..."
lsof -ti:${PORT} | xargs kill -9 2>/dev/null || true

echo "🚀 启动 talk2gemi Relay Server..."
exec .venv/bin/python server/relay_server.py
