#!/usr/bin/env python3
"""Confirm local tests reject two deliberately broken isolated engine copies."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from fingerprint import ROOT


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--binary", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    variants = [
        ("missing-filter", "if (s.limit == 0 || !matches(row, schema, s.where)) return;",
         "if (s.limit == 0) return;", "ordering"),
        ("missing-commit-marker", "append(log_, Json{}, 2);", "/* deliberate durability mutant */", "transaction"),
    ]
    for name, before, after, check in variants:
        target = args.output/name
        target.mkdir()
        control = subprocess.run([sys.executable, str(ROOT/"tests/contract.py"),
                                  "--binary", str(args.binary.resolve()), "--check", check,
                                  "--output", str(target/"control")], capture_output=True, text=True)
        assert control.returncode == 0, control.stdout+control.stderr
        shutil.copytree(ROOT/"src", target/"src")
        path = target/"src/storage.cpp"
        original = path.read_text()
        assert original.count(before) == 1, "mutation site changed; review this test"
        path.write_text(original.replace(before, after))
        binary = target/"mutant"
        build = subprocess.run(["g++", "-std=c++20", "-O1", "-I"+str(ROOT/"third_party"),
                                "-I"+str(target/"src"), *[str(target/"src"/f) for f in ("main.cpp", "sql.cpp", "storage.cpp")],
                                "-o", str(binary)], capture_output=True, text=True)
        (target/"build.log").write_text(build.stdout+build.stderr)
        assert build.returncode == 0, "mutant must compile before testing"
        result = subprocess.run([sys.executable, str(ROOT/"tests/contract.py"), "--binary", str(binary.resolve()),
                                 "--check", check, "--output", str(target/"rejected")], capture_output=True, text=True)
        (target/"test.log").write_text(result.stdout+result.stderr)
        report = json.loads((target/"rejected/result.json").read_text())
        rejected = result.returncode == 1 and report["passed"] is False
        records.append({"name": name, "control_passed": True, "mutant_rejected": rejected, "check": check})
        print(json.dumps(records[-1]), flush=True)
    passed = all(r["mutant_rejected"] for r in records)
    (args.output/"result.json").write_text(json.dumps({"passed": passed, "mutations": records}, indent=2)+"\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
