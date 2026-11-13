#!/usr/bin/env python3
"""Read-only local material audit. Does not push or submit anything."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from evidence import validate
from fingerprint import fingerprint, ROOT

UPSTREAM = "f4d4e26653af823a7601668802f80e374ad6643c"


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--require-clean", action="store_true")
    args = p.parse_args()
    checks = {}
    for path in ("Dockerfile", "CMakeLists.txt", "src/main.cpp", "README.md", "agent/conversation.json"):
        checks[path] = (ROOT/path).is_file() and (ROOT/path).stat().st_size > 0
    checks["public_package_unchanged"] = not git("diff", UPSTREAM, "--", "mysqltest", "protocol_check", "STDIO_PROTOCOL.md")
    checks["branch"] = git("branch", "--show-current") == "2026-competition-round2"
    clean = not git("status", "--porcelain")
    checks["clean"] = clean if args.require_clean else True
    checks["json_dependency"] = hashlib.sha256((ROOT/"third_party/nlohmann/json.hpp").read_bytes()).hexdigest() == "9bea4c8066ef4a1c206b2be5a36302f8926f7fdc6087af5d20b417d0cf103ea6"
    sources = [line for line in (ROOT/"Dockerfile").read_text().splitlines() if line.startswith("FROM ")]
    checks["image_pinned"] = len(sources) == 2 and all(re.search(r"@sha256:[0-9a-f]{64}", s) for s in sources)
    lock = (ROOT/"third_party/apt-build.lock").read_bytes()
    checks["dependency_lock_lf"] = b"\r" not in lock and all(
        re.fullmatch(rb"[a-zA-Z0-9+.:~-]+=[a-zA-Z0-9+.:~_-]+", line) for line in lock.splitlines())
    evidence = validate(ROOT/"agent/conversation.json")
    report = {"passed": all(checks.values()), "checks": checks, "working_tree_clean": clean,
              "commit": git("rev-parse", "HEAD"), "source": fingerprint(), "evidence": evidence,
              "scope": "local material checks; hidden cases, registry access and official evaluator unverified"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
