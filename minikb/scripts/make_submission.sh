#!/usr/bin/env bash
# 打包提交物（用 Python 的 zipfile 写入 UTF-8 文件名，避免中文名在解压时乱码）
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/make_submission.py
