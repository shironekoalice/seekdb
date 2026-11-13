#!/usr/bin/env python3
"""Install only this project's three skills into the named workspace."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("minivectordb-engineering", "minivectordb-evaluation", "minivectordb-learning-evidence")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = args.workspace.resolve()/".agents"/"skills"
    manifest = {}
    for name in NAMES:
        source = ROOT/".agents"/"skills"/name
        target = destination/name
        if not args.check and source.resolve() != target.resolve():
            shutil.copytree(source, target, dirs_exist_ok=True)
        for path in source.rglob("*"):
            if path.is_file():
                relative = path.relative_to(source)
                installed = target/relative
                assert installed.is_file() and path.read_bytes() == installed.read_bytes(), installed
                manifest[name+"/"+relative.as_posix()] = hashlib.sha256(installed.read_bytes()).hexdigest()
    print(json.dumps({"installed": str(destination), "verified": manifest}, indent=2))


if __name__ == "__main__":
    main()
