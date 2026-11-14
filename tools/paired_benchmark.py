#!/usr/bin/env python3
"""Alternate fresh-container rounds to reduce order bias in LOCAL comparisons."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import subprocess
from types import SimpleNamespace

from benchmark import one_round, workload
from compare import compare
from container_lab import docker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-image", required=True)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--dimensions", type=int, default=128)
    parser.add_argument("--queries", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--first", choices=["baseline", "candidate"], default="baseline")
    args = parser.parse_args()
    assert args.rows >= 100 and args.dimensions > 0 and args.queries >= 2
    args.output.mkdir(parents=True, exist_ok=False)
    data = workload(args.rows, args.dimensions, args.queries, args.seed)
    encoded = json.dumps(data, separators=(",", ":")).encode()
    source = args.output/"workload.json"
    source.write_bytes(encoded)
    expected = json.loads(subprocess.check_output([str(args.oracle.resolve()), str(source)]))
    (args.output/"oracle.json").write_text(json.dumps(expected))
    common = {
        "schema": 1, "scope": "local cgroup-v2 comparison; not official score",
        "passed": False,
        "workload": {"rows": args.rows, "dimensions": args.dimensions, "queries": args.queries,
                     "seed": args.seed, "mode": "exact", "k": 10, "batch": 100,
                     "sha256": hashlib.sha256(encoded).hexdigest()},
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "cpu_model": next((line.split(":", 1)[1].strip()
                                           for line in Path("/proc/cpuinfo").read_text().splitlines()
                                           if line.startswith("model name")), ""),
                        "quota_cpus": 2, "memory": "2g", "swap_limit_requested": True,
                        "swap_limit_supported": docker("info", "-f", "{{.SwapLimit}}"),
                        "docker": docker("version", "-f", "{{.Server.Version}}")},
        "measurement": {
            "resources": "cgroup v2 io.stat and memory.peak, page cache included",
            "io_devices": "sum of local cgroup devices; no dedicated contest data device",
            "T": "client wall time of all write batches or query series, response parsing included",
            "BM": "startup + load + index + warmup + requests + shutdown + sync -f",
            "cache": "fresh named volumes; host page cache is NOT globally reset",
            "overhead": "sleep supervisor and docker exec/sync included in cgroup"},
        "rounds": []}
    reports = {}
    order = []
    for role, image in (("baseline", args.baseline_image), ("candidate", args.candidate_image)):
        report = copy.deepcopy(common)
        report.update(image=image, image_id=docker("image", "inspect", "-f", "{{.Id}}", image))
        reports[role] = report
        (args.output/role).mkdir()
    def save():
        for role, report in reports.items():
            (args.output/role/"result.json").write_text(json.dumps(report, indent=2)+"\n")
        (args.output/"order.json").write_text(json.dumps(order, indent=2)+"\n")
    try:
        first = [args.first, "candidate" if args.first == "baseline" else "baseline"]
        for number in range(1, 4):
            for role in (first if number % 2 else first[::-1]):
                run_args = SimpleNamespace(image=reports[role]["image"], output=args.output/role,
                                           dimensions=args.dimensions, mode="exact")
                result = one_round(run_args, data, expected, number)
                reports[role]["rounds"].append(result)
                order.append({"round": number, "role": role,
                              "write_s": result["write"]["T_seconds"],
                              "query_s": result["query"]["T_seconds"]})
                save()
                print(json.dumps(order[-1]), flush=True)
        for report in reports.values():
            report["passed"] = True
        save()
        result = compare(reports["baseline"], reports["candidate"])
        (args.output/"comparison.json").write_text(json.dumps(result, indent=2)+"\n")
        print(json.dumps(result), flush=True)
    except BaseException:
        save()
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
