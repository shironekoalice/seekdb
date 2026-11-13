#!/usr/bin/env python3
"""Prove live logical data exceeds the memory cap, then test rollback and recovery."""
import argparse
import json
import math
from pathlib import Path
import time
import traceback
from container_lab import Lab, docker


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--memory-mib", type=int, default=128)
    p.add_argument("--data-mib", type=int, default=256)
    args = p.parse_args()
    assert args.data_mib > args.memory_mib >= 64
    args.output.mkdir(parents=True, exist_ok=False)
    payload_size = 65536
    count = math.ceil(args.data_mib*1048576/payload_size)
    report = {"passed": False, "scope": "local live-data-over-cgroup-memory test",
              "logical_payload_bytes": count*payload_size,
              "memory_limit_bytes": args.memory_mib*1048576,
              "image_id": docker("image", "inspect", "-f", "{{.Id}}", args.image)}
    started = time.monotonic()
    try:
        with Lab(args.image, args.output/"stderr.log", f"{args.memory_mib}m", 8*1048576) as lab:
            lab.sql("CREATE TABLE t(id BIGINT PRIMARY KEY,body VARCHAR(65536),v VECTOR(2));")
            for start in range(0, count, 16):
                rows = [f"({i},'{str(i).zfill(8)+'x'*(payload_size-8)}','[{i%101},0]')"
                        for i in range(start, min(count, start+16))]
                lab.sql("INSERT INTO t VALUES "+",".join(rows)+";")
                if start % 512 == 0: print(f"written {start}/{count} rows", flush=True)
            lab.sql("CREATE VECTOR INDEX vi ON t(v);")
            lab.sql("BEGIN;")
            lab.sql("UPDATE t SET body='temporary' WHERE id=0;")
            lab.sql("DELETE FROM t WHERE id=1;")
            lab.sql("ROLLBACK;")
            lab.sql("UPDATE t SET body='committed' WHERE id=2;")
            lab.stop()
            recovery = time.monotonic()
            lab.start()
            report["recovery_seconds"] = time.monotonic()-recovery
            for key in (0, 1, 2, count-1):
                body = "committed" if key == 2 else str(key).zfill(8)+"x"*(payload_size-8)
                assert lab.sql(f"SELECT body FROM t WHERE id={key};")["rows"] == [[body]]
            expected = [[i, 0.0] for i in range(0, count, 101)][:10]
            assert lab.sql("SELECT id,L2_DISTANCE(v,'[0,0]') AS d FROM t ORDER BY d LIMIT 10;")["rows"] == expected
            assert len(lab.sql("SELECT id FROM t;")["rows"]) == count
            lab.stop()
            report["metrics"] = lab.metrics()
            assert report["metrics"]["memory_limit_bytes"] == args.memory_mib*1048576
            assert report["metrics"]["host_swap_io_delta"]["pswpout"] == 0, "host swap activity: memory result inconclusive"
            report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    report["seconds"] = time.monotonic()-started
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
