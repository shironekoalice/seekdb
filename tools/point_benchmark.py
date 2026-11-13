#!/usr/bin/env python3
"""Local PK CRUD workload with a Python state oracle; not contest scoring."""
import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import random
import statistics
import time
import traceback

from container_lab import Lab, ROOT, Session, docker


@contextmanager
def target(args, log):
    if args.image:
        with Lab(args.image, log) as lab:
            yield lab
    else:
        config = json.loads((ROOT/"protocol_check/config.json").read_text())
        config["case_timeout"] = 1200
        session = Session(config, log, command=[str(args.binary), "--data-dir", "{data_dir}"])

        class Native:
            def sql(self, sql):
                result = session.request(sql)
                assert result["ok"], (sql, result)
                return result

            def stop(self):
                session.shutdown()

            def metrics(self):
                return {"scope": "native wall time only; no platform B/M"}

        try:
            session.start()
            yield Native()
        finally:
            session.close()


def one_round(args, number, keys):
    model = {key: [key % 10, f"row-{key}", [float(key % 3), 0.0], key]
             for key in range(1, args.rows+1)}
    record = {"round": number, "passed": False, "stages": {}}
    with target(args, args.output/f"round-{number}.log") as lab:
        lab.sql("CREATE TABLE points(g BIGINT,label VARCHAR(64),v VECTOR(2),id BIGINT PRIMARY KEY);")
        for start in range(1, args.rows+1, 100):
            values = ",".join(f"({g},'{label}','{json.dumps(v)}',{key})"
                              for g, label, v, key in (model[k] for k in range(start, min(start+100, args.rows+1))))
            lab.sql("INSERT INTO points VALUES "+values+";")
        lab.sql("CREATE VECTOR INDEX points_vi ON points(v);")
        # Pre-render SQL and independent expected results outside the timed loop.
        # Misses and a second AND predicate exercise filtering and absent keys.
        reads = [(f"SELECT id,label FROM points WHERE g>=0 AND id={key};",
                  [[key, model[key][1]]] if key in model else []) for key in keys]
        for sql, expected in reads[:10]:
            assert lab.sql(sql)["rows"] == expected
        start = time.perf_counter()
        answers = [lab.sql(sql)["rows"] for sql, _ in reads]
        record["stages"]["select"] = {"T_seconds": time.perf_counter()-start, "requests": len(reads)}
        assert answers == [expected for _, expected in reads]
        for operation in ("update", "delete"):
            statements, expected = [], []
            for key in keys:
                present = key in model
                expected.append(int(present))
                if operation == "update":
                    statements.append(f"UPDATE points SET g=99,label='updated' WHERE id={key} AND g>=0;")
                    if present:
                        model[key][0:2] = [99, "updated"]
                else:
                    statements.append(f"DELETE FROM points WHERE id={key} AND g>=0;")
                    model.pop(key, None)
            start = time.perf_counter()
            actual = [lab.sql(sql)["affected_rows"] for sql in statements]
            record["stages"][operation] = {"T_seconds": time.perf_counter()-start,
                                           "requests": len(statements)}
            assert actual == expected, (operation, actual, expected)
        reinserts = sorted(set(key for key in keys if key <= args.rows))
        statements = [f"INSERT INTO points VALUES (7,'reinsert','[0,1]',{key});" for key in reinserts]
        start = time.perf_counter()
        actual = [lab.sql(sql)["affected_rows"] for sql in statements]
        record["stages"]["insert"] = {"T_seconds": time.perf_counter()-start,
                                      "requests": len(statements)}
        assert actual == [1]*len(statements)
        for key in reinserts:
            model[key] = [7, "reinsert", [0.0, 1.0], key]
        lab.stop()
        record["resources_whole_round"] = lab.metrics()
        assert record["resources_whole_round"].get("host_swap_io_delta", {}).get("pswpout", 0) == 0
        # State validation runs in a fresh process after the resource snapshot.
        if args.image:
            lab.start()
            for lower in range(0, args.rows, 200):
                expected = [model[k] for k in sorted(model) if lower < k <= lower+200]
                assert lab.sql(f"SELECT * FROM points WHERE id>{lower} AND id<={lower+200} ORDER BY id;")["rows"] == expected
            lab.stop()
            record["persisted_rows_verified"] = len(model)
        else:
            record["persisted_rows_verified"] = None
    record["passed"] = True
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--binary", type=Path)
    targets.add_argument("--image")
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--operations", type=int, default=300)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261114)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assert args.rows >= 100 and args.operations > 0 and args.rounds > 0
    if args.binary:
        args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(args.seed)
    keys = [rng.randrange(1, args.rows+1) + (args.rows if i % 2 else 0)
            for i in range(args.operations)]
    report = {"scope": "local point CRUD; independent Python state, not official E workload",
              "passed": False, "image": args.image, "binary": str(args.binary) if args.binary else None,
              "image_id": docker("image", "inspect", "-f", "{{.Id}}", args.image) if args.image else None,
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest() if args.binary else None,
              "workload": {"rows": args.rows, "operations_per_stage": args.operations, "seed": args.seed,
                           "keys_sha256": hashlib.sha256(json.dumps(keys).encode()).hexdigest(),
                           "hit_pattern": "alternating present/absent; repeated keys allowed",
                           "durability": "each update/delete/insert autocommits"},
              "rounds": []}
    (args.output/"keys.json").write_text(json.dumps(keys)+"\n")
    try:
        for number in range(1, args.rounds+1):
            result = one_round(args, number, keys)
            report["rounds"].append(result)
            print(json.dumps(result), flush=True)
        report["medians_T_seconds"] = {stage: statistics.median(r["stages"][stage]["T_seconds"] for r in report["rounds"])
                                        for stage in ("select", "update", "delete", "insert")}
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        print(report["error"], flush=True)
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
