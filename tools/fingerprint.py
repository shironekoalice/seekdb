#!/usr/bin/env python3
"""Content identities independent of docs commits or local absolute paths."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    paths = [ROOT/"CMakeLists.txt", ROOT/"Dockerfile"]
    for directory in ("src", "third_party"):
        paths.extend(p for p in (ROOT/directory).rglob("*") if p.is_file())
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(paths)}
    return {"code_sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
            "files": files}


if __name__ == "__main__":
    print(json.dumps(fingerprint(), indent=2))
