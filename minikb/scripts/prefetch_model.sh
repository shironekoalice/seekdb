#!/usr/bin/env bash
# 预取 embedding 模型到 data/hf_cache，让应用在没有外网/网络抖动时也能启动。
# 用法：先 `node server.mjs`，再执行本脚本（走本机 /api/hf 代理，带磁盘缓存）。
set -euo pipefail
BASE="${1:-http://127.0.0.1:8787}"
MODEL="Xenova/bge-small-zh-v1.5"
for f in config.json tokenizer.json tokenizer_config.json onnx/model_quantized.onnx onnx/model_fp16.onnx onnx/model.onnx; do
  printf '%-28s' "$f"
  curl -sS -m 1800 -o /dev/null -w '%{http_code}  %{size_download} bytes\n' "$BASE/api/hf/$MODEL/resolve/main/$f"
done
echo "缓存目录：data/hf_cache"
