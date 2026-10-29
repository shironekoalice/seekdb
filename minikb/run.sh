#!/usr/bin/env bash
# 启动 mini-kb（seekdb 嵌入式 + 浏览器端向量化）
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v node >/dev/null 2>&1 && [ -x ../.tools/node/bin/node ]; then
  export PATH="$(cd ../.tools/node && pwd)/bin:$PATH"
fi
command -v node >/dev/null 2>&1 || { echo "需要 Node.js >= 20"; exit 1; }

export SEEKDB_BINDINGS_CACHE_DIR="${SEEKDB_BINDINGS_CACHE_DIR:-$PWD/.bindings}"

if [ ! -d node_modules ]; then
  echo "→ 安装依赖…"
  npm install --legacy-peer-deps --no-audit --no-fund \
    --registry=https://registry.npmmirror.com seekdb express
fi

PORT="${PORT:-8787}"
echo "→ http://127.0.0.1:${PORT}"
exec node server.mjs
