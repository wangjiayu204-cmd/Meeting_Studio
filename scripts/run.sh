#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"
# 使用当前已激活环境；默认本机访问，不与 vLLM 8000 端口冲突。
exec python -m uvicorn app.main:create_app --factory \
  --host "${PM_HOST:-127.0.0.1}" --port "${PM_PORT:-8080}" --workers 1
