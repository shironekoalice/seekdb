#!/usr/bin/env python3
"""Repeatable local benchmark; this is not the undisclosed official benchmark."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import struct
import subprocess
import time
import traceback
from container_lab import Lab, ROOT, docker


def workload(rows, dimensions, queries, seed):
    rng = random.Random(seed)
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    vector = lambda: [f32(rng.uniform(-1, 1)) for _ in range(dimensions)]
    return {"rows": [[i+1, i % 10, vector()] for i in range(rows)],
            "queries": [{"v": vector(), "group": None if i % 2 == 0 else rng.randrange(10)}
                        for i in range(queries)], "k": 10}


def load(lab, data):
    for start in range(0, len(data["rows"]), 100):
        values = ",".join(f"({i},{g},'{json.dumps(v)}')" for i, g, v in data["rows"][start:start+100])
        lab.sql("BEGIN;")
        lab.sql("INSERT INTO docs VALUES "+values+";")
        lab.sql("COMMIT;")


def query_sql(q):
    where = "" if q["group"] is None else f" WHERE group_id={q['group']}"
    return f"SELECT id,L2_DISTANCE(v,'{json.dumps(q['v'])}') AS d FROM docs{where} ORDER BY d LIMIT 10;"


def verify_rows(lab, data):
    for start in range(0, len(data["rows"]), 200):
        actual = lab.sql(f"SELECT * FROM docs WHERE id>{start} AND id<={start+200} ORDER BY id;")["rows"]
        assert actual == data["rows"][start:start+200], f"row mismatch after {start}"
    assert lab.sql(f"SELECT id FROM docs WHERE id>{len(data['rows'])};")["rows"] == []


def one_round(args, data, expected, number):
    record = {"round": number, "passed": False}
    for stage in ("write", "query"):
        with Lab(args.image, args.output/f"round-{number}-{stage}.log") as lab:
            lab.sql(f"CREATE TABLE docs(id BIGINT PRIMARY KEY,group_id BIGINT,v VECTOR({args.dimensions}));")
            if stage == "write":
                start = time.perf_counter()
                load(lab, data)
                duration = time.perf_counter()-start
            else:
                load(lab, data)
                lab.sql("CREATE VECTOR INDEX vi ON docs(v);")
                statements = [query_sql(q) for q in data["queries"]]
                for sql in statements[:min(10, len(statements))]:
                    lab.sql(sql, args.mode)
                start = time.perf_counter()
                answers = [lab.sql(sql, args.mode)["rows"] for sql in statements]
                duration = time.perf_counter()-start
            lab.stop()
            metrics = lab.metrics()
            metrics["T_seconds"] = duration
            record[stage] = metrics
            # Verification is deliberately after the resource snapshot.
            if stage == "write":
                lab.start()
                verify_rows(lab, data)
                lab.stop()
                metrics["persisted_rows_verified"] = len(data["rows"])
            else:
                recalls = {"filtered": [], "unfiltered": []}
                for q, answer, truth in zip(data["queries"], answers, expected):
                    ids = [r[0] for r in answer]
                    gold = [r[0] for r in truth]
                    assert len(ids) == len(set(ids)) == len(gold)
                    if args.mode == "exact":
                        assert ids == gold, (ids, gold)
                    for key, distance in answer:
                        assert 1 <= key <= len(data["rows"])
                        _, group, vector = data["rows"][key-1]
                        assert q["group"] is None or q["group"] == group
                        actual = math.sqrt(sum((a-b)**2 for a, b in zip(vector, q["v"])))
                        assert math.isclose(distance, actual, abs_tol=1e-5, rel_tol=1e-5)
                    assert answer == sorted(answer, key=lambda r: (r[1], r[0]))
                    recalls["filtered" if q["group"] is not None else "unfiltered"].append(
                        len(set(ids)&set(gold))/len(gold) if gold else 1.0)
                metrics["recall"] = {k: statistics.mean(v) for k, v in recalls.items()}
                assert min(metrics["recall"].values()) >= .95, metrics["recall"]
    record["passed"] = True
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--dimensions", type=int, default=128)
    parser.add_argument("--queries", type=int, default=1000)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261113)
    parser.add_argument("--mode", choices=["exact", "ann"], default="exact")
    args = parser.parse_args()
    assert args.rows >= 100 and args.queries >= 2 and args.rounds > 0 and args.dimensions > 0
    args.output.mkdir(parents=True, exist_ok=False)
    data = workload(args.rows, args.dimensions, args.queries, args.seed)
    source = args.output/"workload.json"
    encoded = json.dumps(data, separators=(",", ":")).encode()
    source.write_bytes(encoded)
    expected = json.loads(subprocess.check_output([str(args.oracle.resolve()), str(source)]))
    (args.output/"oracle.json").write_text(json.dumps(expected), encoding="utf-8")
    report = {
        "schema": 1, "scope": "local cgroup-v2 comparison; not official score",
        "passed": False, "image": args.image, "image_id": docker("image", "inspect", "-f", "{{.Id}}", args.image),
        "workload": {"rows": args.rows, "dimensions": args.dimensions, "queries": args.queries,
                     "seed": args.seed, "mode": args.mode, "k": 10, "batch": 100,
                     "sha256": hashlib.sha256(encoded).hexdigest()},
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "cpu_model": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), ""),
                        "quota_cpus": 2, "memory": "2g", "swap_limit_requested": True,
                        "swap_limit_supported": docker("info", "-f", "{{.SwapLimit}}"),
                        "docker": docker("version", "-f", "{{.Server.Version}}")},
        "measurement": {"resources": "cgroup v2 io.stat and memory.peak, page cache included",
                        "io_devices": "sum of local cgroup devices; no dedicated contest data device",
                        "T": "client wall time of all write batches or query series, response parsing included",
                        "BM": "startup + load + index + warmup + requests + shutdown + sync -f",
                        "cache": "fresh named volumes; host page cache is NOT globally reset",
                        "overhead": "sleep supervisor and docker exec/sync included in cgroup"},
        "rounds": []}
    try:
        for number in range(1, args.rounds+1):
            result = one_round(args, data, expected, number)
            report["rounds"].append(result)
            print(json.dumps({"round": number, "write_s": result["write"]["T_seconds"],
                              "query_s": result["query"]["T_seconds"], "recall": result["query"]["recall"]}), flush=True)
            (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
