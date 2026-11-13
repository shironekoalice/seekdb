#!/usr/bin/env python3
"""Native vector workload for gprof; timing is diagnostic, not an E score."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from benchmark import workload, query_sql
from container_lab import ROOT, Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT/"protocol_check/config.json").read_text())
    config["case_timeout"] = 1200
    data = workload(10000, 128, 1000, 20261114)
    statements = [query_sql(q) for q in data["queries"]]
    session = Session(config, args.output/"session.log",
                      command=[str(args.binary.resolve()), "--data-dir", "{data_dir}"])
    def ok(sql):
        result = session.request(sql)
        assert result["ok"], (sql[:100], result)
        return result
    try:
        session.start()
        ok("CREATE TABLE docs(id BIGINT PRIMARY KEY,group_id BIGINT,v VECTOR(128));")
        for start in range(0, len(data["rows"]), 100):
            values = ",".join(f"({i},{g},'{json.dumps(v)}')" for i, g, v in data["rows"][start:start+100])
            ok("INSERT INTO docs VALUES "+values+";")
        ok("CREATE VECTOR INDEX vi ON docs(v);")
        start = time.perf_counter()
        for sql in statements:
            assert len(ok(sql)["rows"]) == 10
        duration = time.perf_counter()-start
        session.shutdown()
    finally:
        session.close()
    report = {"passed": True, "scope": "diagnostic gprof workload; not independent correctness validation",
              "rows": 10000, "dimensions": 128, "queries": 1000, "seed": 20261114,
              "wall_seconds": duration, "binary": str(args.binary.resolve()),
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "expected_vector_row_visits": 10000000}
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
