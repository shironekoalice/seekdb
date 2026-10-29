#!/usr/bin/env python3
"""打包提交物：源码 + 前端 vendor + 文档 + 测试数据（不含 node_modules / data / 浏览器）。"""
from __future__ import annotations

import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist"
SKIP_DIRS = {"node_modules", ".browsers", ".bindings", "data", "models", "dist", "__pycache__"}
SKIP_SUFFIX = {".pyc", ".DS_Store"}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    name = f"minikb-submission-{time.strftime('%Y%m%d')}.zip"
    target = OUT / name
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(ROOT.rglob("*")):
            rel = path.relative_to(ROOT)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.is_dir() or path.suffix in SKIP_SUFFIX:
                continue
            zf.write(path, rel.as_posix())
    size = target.stat().st_size / 2**20
    with zipfile.ZipFile(target) as zf:
        print(f"→ {target}  ({size:.1f} MiB, {len(zf.namelist())} files)")


if __name__ == "__main__":
    main()
