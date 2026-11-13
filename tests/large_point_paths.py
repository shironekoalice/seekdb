#!/usr/bin/env python3
"""Local many-row, out-of-core point operations; not the hidden formal case."""
import argparse
import hashlib
import json
import math
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
    parser.add_argument("--rows", type=int, default=200000)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--memory-mib", type=int, default=128)
    parser.add_argument("--enforce-no-swap", action="store_true",
                        help="set and verify memory.swap.max=0 for this test's own container")
    args = parser.parse_args()
    assert args.rows > 16 and args.payload_bytes >= 16
    assert args.rows * args.payload_bytes > args.memory_mib * 1048576
    args.output.mkdir(parents=True, exist_ok=False)
    report = {
        "passed": False,
        "scope": "local scalability diagnostic, not reproduction of an identified hidden case",
        "image": args.image,
        "image_id": docker("image", "inspect", "-f", "{{.Id}}", args.image),
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "workload": {
            "rows": args.rows,
            "payload_bytes_per_row": args.payload_bytes,
            "logical_body_bytes": args.rows * args.payload_bytes,
            "memory_limit_bytes": args.memory_mib * 1048576,
            "request_timeout_seconds": 60,
            "startup_timeout_seconds": 120,
            "batch_rows": 128,
        },
        "requests": [],
    }
    started = time.monotonic()
    def body(key):
        return str(key).zfill(12) + "x" * (args.payload_bytes - 12)
    try:
        with Lab(args.image, args.output / "stderr.log", f"{args.memory_mib}m") as lab:
            if args.enforce_no_swap:
                swap_file = lab.cgroup / "memory.swap.max"
                # Lab derives this cgroup from this newly created container PID.
                # Do not alter host swap configuration or other cgroups.
                swap_file.write_text("0")
                assert swap_file.read_text().strip() == "0"
                report["container_swap_max_enforced"] = 0
            def request(label, sql, expected=None, rows=None):
                before = lab.io()
                at = time.monotonic()
                response = lab.sql(sql)
                elapsed = time.monotonic() - at
                after = lab.io()
                read_bytes = sum(v.get("rbytes", 0) - before.get(dev, {}).get("rbytes", 0)
                                 for dev, v in after.items())
                item = {"label": label, "sql": sql, "seconds": elapsed,
                        "read_bytes": read_bytes}
                report["requests"].append(item)
                if rows is not None:
                    assert response["rows"] == rows, (label, response)
                if expected is not None:
                    assert response["affected_rows"] == expected, (label, response)
                print(f"{label}: {elapsed:.6f}s read={read_bytes}", flush=True)
                return response
            lab.sql("CREATE TABLE t(id BIGINT PRIMARY KEY,body VARCHAR("
                    + str(args.payload_bytes) + "),g BIGINT);")
            load_started = time.monotonic()
            digest = hashlib.sha256()
            for start in range(0, args.rows, 128):
                values = [f"({key},'{body(key)}',{key % 7})"
                          for key in range(start, min(args.rows, start + 128))]
                sql = "INSERT INTO t VALUES " + ",".join(values) + ";"
                digest.update(sql.encode())
                assert lab.sql(sql)["affected_rows"] == len(values)
                if start % 16384 == 0:
                    print(f"loaded {start}/{args.rows}", flush=True)
            report["load_seconds"] = time.monotonic() - load_started
            report["insert_sql_sha256"] = digest.hexdigest()
            last = args.rows - 1
            request("select-first", "SELECT id,g FROM t WHERE id=0;", rows=[[0, 0]])
            request("select-last", f"SELECT id,g FROM t WHERE id={last};",
                    rows=[[last, last % 7]])
            request("select-missing", f"SELECT id FROM t WHERE id={args.rows};", rows=[])
            request("select-other-predicate", f"SELECT id FROM t WHERE id={last} AND g=8;",
                    rows=[])
            request("update-hit", "UPDATE t SET body='durable' WHERE id=5;", expected=1)
            request("update-missing", f"UPDATE t SET body='absent' WHERE id={args.rows};",
                    expected=0)
            request("delete-hit", "DELETE FROM t WHERE id=6;", expected=1)
            request("delete-missing", f"DELETE FROM t WHERE id={args.rows};", expected=0)
            lab.sql("BEGIN;")
            request("update-pending", "UPDATE t SET body='temporary' WHERE id=5;", expected=1)
            request("read-pending", "SELECT body FROM t WHERE id=5;", rows=[["temporary"]])
            lab.sql("ROLLBACK;")
            request("read-rollback", "SELECT body FROM t WHERE id=5;", rows=[["durable"]])
            lab.stop()
            at = time.monotonic()
            lab.start()
            report["recovery_seconds"] = time.monotonic() - at
            request("read-restarted", "SELECT body FROM t WHERE id=5;", rows=[["durable"]])
            request("read-deleted-restarted", "SELECT id FROM t WHERE id=6;", rows=[])
            request("read-last-body-restarted", f"SELECT body FROM t WHERE id={last};",
                    rows=[[body(last)]])
            # A bounded independent range control checks untouched records.
            # It deliberately contains no equality on the primary key.
            request("range-control", f"SELECT id,g FROM t WHERE id>={last-3} ORDER BY id;",
                    rows=[[key, key % 7] for key in range(last - 3, last + 1)])
            lab.stop()
            report["metrics"] = lab.metrics()
            assert report["metrics"]["memory_limit_bytes"] == args.memory_mib * 1048576
            assert report["metrics"]["host_swap_io_delta"]["pswpout"] == 0
            report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    report["seconds"] = time.monotonic() - started
    report["max_request_seconds"] = max(
        (r["seconds"] for r in report["requests"]), default=math.nan)
    if not report["requests"]:
        report["max_request_seconds"] = None
    (args.output / "result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("passed", "load_seconds", "max_request_seconds", "seconds")
                      if k in report}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
