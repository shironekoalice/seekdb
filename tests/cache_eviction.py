#!/usr/bin/env python3
"""Independent multi-table query oracle with eviction and temporary-map churn."""
import argparse
import json
import math
from pathlib import Path
import traceback

from contract import Checks, f32


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--churn", type=int, default=8200)
    args = parser.parse_args()
    if args.binary:
        args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    checks = Checks(args)
    report = {"passed": False, "scope": "local independent multi-map eviction/state oracle",
              "initial_rows": 24000, "rollback_cycles": args.churn, "phases": [], "queries": 0}
    models = [{k: [t*1000000+k, k % 7, float(k)+t*0.25] for k in range(12000)}
              for t in range(2)]

    def phase(session, name):
        for j in range(16):
            t = j % 2
            query = f32([0, 23.25, 4095.125, 8191.875, 11999, 6000.5, -100, 20000][j // 2])
            group = None if j % 4 < 2 else j % 7
            where = "" if group is None else f" WHERE g={group}"
            expected = sorted(
                ((abs(value[2]-query), key, value[0])
                 for key, value in models[t].items() if group is None or value[1] == group)
            )[:10]
            sql = (f"SELECT id,n,L2_DISTANCE(v,'[{query}]') AS d FROM c{t}{where}"
                   " ORDER BY d LIMIT 10;")
            actual = checks.ok(session, sql)["rows"]
            assert [r[:2] for r in actual] == [[key, value] for d, key, value in expected], (name, sql, actual, expected)
            assert all(math.isclose(r[2], d, abs_tol=1e-5, rel_tol=1e-5)
                       for r, (d, key, value) in zip(actual, expected))
            report["queries"] += 1
        for t in range(2):
            actual = checks.ok(session, f"SELECT id,n,g FROM c{t} ORDER BY id;")["rows"]
            assert actual == [[key, value[0], value[1]] for key, value in sorted(models[t].items())]
        report["phases"].append({"name": name, "passed": True})

    try:
        with checks.session() as s:
            for t in range(2):
                checks.ok(s, f"CREATE TABLE c{t}(id BIGINT PRIMARY KEY,n BIGINT,g BIGINT,v VECTOR(1));")
                for start in range(0, 12000, 100):
                    values = ",".join(f"({k},{models[t][k][0]},{k%7},'[{models[t][k][2]}]')"
                                      for k in range(start, start+100))
                    checks.ok(s, f"INSERT INTO c{t} VALUES {values};")
                checks.ok(s, f"CREATE VECTOR INDEX vi{t} ON c{t}(v);")
            checks.ok(s, "CREATE TABLE scratch(id BIGINT PRIMARY KEY,n BIGINT);")
            phase(s, "alternating-tables-over-node-capacity")
            # Each INSERT constructs a new statement map and pending map.
            # 8200 cycles exceed the candidate cache's 16384 slot-owner period;
            # expected state does not depend on any cache implementation.
            for i in range(args.churn):
                checks.ok(s, "BEGIN;")
                checks.ok(s, f"INSERT INTO scratch VALUES (-1,{i});")
                checks.ok(s, "ROLLBACK;")
            assert checks.ok(s, "SELECT * FROM scratch;")["rows"] == []
            phase(s, "temporary-map-churn")
            for t in range(2):
                checks.ok(s, "BEGIN;")
                checks.ok(s, f"UPDATE c{t} SET n=-7,g=2,v='[-0.5]' WHERE id=8192;")
                models[t][8192] = [-7, 2, -0.5]
                checks.ok(s, f"DELETE FROM c{t} WHERE id=17;")
                checks.ok(s, f"INSERT INTO c{t} VALUES (17,-17,1,'[0.125]');")
                models[t][17] = [-17, 1, 0.125]
                checks.ok(s, "COMMIT;")
            phase(s, "updated-tombstone-reinsert")
            s.crash()
            s.start()
            phase(s, "kill-recovery")
            s.shutdown()
            s.start()
            phase(s, "repeated-recovery")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
