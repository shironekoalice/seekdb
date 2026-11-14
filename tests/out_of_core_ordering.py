#!/usr/bin/env python3
"""Sort unprojected wide VARCHAR keys while live data exceeds cgroup memory."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from container_lab import Lab, docker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--memory-mib", type=int, default=128)
    parser.add_argument("--data-mib", type=int, default=256)
    args = parser.parse_args()
    assert args.data_mib > args.memory_mib >= 64
    args.output.mkdir(parents=True, exist_ok=False)
    payload = 65536
    count = args.data_mib * 1048576 // payload
    report = {"passed": False, "scope": "local cgroup out-of-core ORDER BY check",
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "image_id": docker("image", "inspect", "-f", "{{.Id}}", args.image),
              "logical_payload_bytes": count * payload,
              "memory_limit_bytes": args.memory_mib * 1048576}
    started = time.monotonic()
    try:
        with Lab(args.image, args.output / "stderr.log", f"{args.memory_mib}m",
                 8 * 1048576) as lab:
            try:
                (lab.cgroup / "memory.swap.max").write_text("0")
                assert (lab.cgroup / "memory.swap.max").read_text().strip() == "0"
                report["actual_swap_max"] = 0
                lab.session.deadline = time.monotonic() + 600
                report["phase"] = "load"
                lab.sql("CREATE TABLE t(id BIGINT PRIMARY KEY,body VARCHAR(65536));")
                for first in range(0, count, 16):
                    rows = [f"({i},'{str(count-i).zfill(8)}{'x'*(payload-8)}')"
                            for i in range(first, min(count, first+16))]
                    lab.sql("INSERT INTO t VALUES" + ",".join(rows) + ";")
                report["load_seconds"] = time.monotonic() - started
                expected = [[i] for i in range(count-1, -1, -1)]
                report["phase"] = "wide_order"
                query_start = time.monotonic()
                assert lab.sql(f"SELECT id FROM t ORDER BY body LIMIT {count};")["rows"] == expected
                report["query_seconds"] = time.monotonic() - query_start
                report["phase"] = "transaction"
                lab.sql("BEGIN;")
                lab.sql("UPDATE t SET body='' WHERE id=0;")
                assert lab.sql("SELECT id FROM t ORDER BY body LIMIT 2;")["rows"] == [[0], [count-1]]
                lab.sql("ROLLBACK;")
                lab.stop()
                report["phase"] = "restart"
                lab.start()
                lab.session.deadline = time.monotonic() + 600
                assert lab.sql("SELECT id FROM t ORDER BY body LIMIT 10;")["rows"] == expected[:10]
                assert lab.sql(f"SELECT id FROM t ORDER BY body DESC LIMIT {count};")["rows"] == list(reversed(expected))
                lab.stop()
                report["metrics"] = lab.metrics()
                assert report["metrics"]["memory_limit_bytes"] == report["memory_limit_bytes"]
                assert report["metrics"]["memory_swap_max"] == "0"
                assert report["metrics"]["host_swap_io_delta"]["pswpout"] == 0
                report["phase"] = "complete"
                report["passed"] = True
            finally:
                report["observed_memory_events"] = (lab.cgroup / "memory.events").read_text()
                report["observed_memory_peak"] = int((lab.cgroup / "memory.peak").read_text())
    except Exception:
        report["error"] = traceback.format_exc()
    report["seconds"] = time.monotonic() - started
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
