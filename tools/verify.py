#!/usr/bin/env python3
"""Run the unchanged official driver and/or independent local contract checks."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import hashlib
from fingerprint import fingerprint

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    parser.add_argument("--suite", choices=["public", "extended", "all"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    target_args = ["--image", args.image] if args.image else [
        "--command", json.dumps([str(args.binary.resolve()), "--data-dir", "{data_dir}"])]
    results = []
    if args.suite in ("public", "all"):
        result = subprocess.run([sys.executable, str(ROOT/"protocol_check/run.py"),
                                 *target_args, "--output", str(args.output/"public")])
        results.append(result.returncode == 0)
    if args.suite in ("extended", "all"):
        result = subprocess.run([sys.executable, str(ROOT/"tests/contract.py"),
                                 *(["--image", args.image] if args.image else ["--binary", str(args.binary.resolve())]),
                                 "--output", str(args.output/"extended")])
        results.append(result.returncode == 0)
    report = {"passed": all(results), "suite": args.suite,
              "scope": "local validation; no official hidden-case or score claim",
              "source": fingerprint()}
    if args.binary:
        report["binary_sha256"] = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    else:
        report["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", "-f", "{{.Id}}", args.image], text=True).strip()
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
