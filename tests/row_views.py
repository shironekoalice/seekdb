#!/usr/bin/env python3
"""Independent row/TopK oracle under disabled, tiny and warm row caches."""
import argparse
from contextlib import contextmanager
import copy
import json
import math
from pathlib import Path
import traceback

from contract import Checks, f32, sql_quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.binary = args.binary.resolve()
    args.image = None
    args.output.mkdir(parents=True, exist_ok=False)
    checks = Checks(args)
    report = {"passed": False, "scope": "independent state and full-sort oracle",
              "cache_budgets": [0, 1024, 65536, 1048576], "queries": 0, "phases": []}

    def phase(session, models, name):
        for table, model in enumerate(models):
            for query in (-3.25, 7.75):
                for group in (None, 0, 1):
                    for descending in (False, True):
                        for limit in (1, 11, 101):
                            selected = [row for row in model.values()
                                        if group is None or row[2] == group]
                            selected.sort(key=lambda row: row[3])
                            selected.sort(key=lambda row: abs(row[1][0]-query),
                                          reverse=descending)
                            expected = [[row[3], row[0], row[1], abs(row[1][0]-query)]
                                        for row in selected[:limit]]
                            where = "" if group is None else f" WHERE g={group}"
                            direction = " DESC" if descending else ""
                            sql = (f"SELECT id,body,v,L2_DISTANCE(v,'[{query}]') AS d "
                                   f"FROM t{table}{where} ORDER BY d{direction} LIMIT {limit};")
                            actual = checks.ok(session, sql)["rows"]
                            assert len(actual) == len(expected), (name, sql)
                            for got, want in zip(actual, expected):
                                assert got[:3] == want[:3], (name, sql, got[0], want[0])
                                assert math.isclose(got[3], want[3], abs_tol=1e-5, rel_tol=1e-5)
                            report["queries"] += 1
            actual = checks.ok(session, f"SELECT * FROM t{table} ORDER BY id;")["rows"]
            assert actual == [model[key] for key in sorted(model)], (name, table)
        report["phases"].append({"name": name, "passed": True})

    try:
        for budget in report["cache_budgets"]:
            models = [{key: [("中😀" + str(table) + ":" + str(key)) * (1 + key % 97),
                             [f32((key % 13)-5.5)], key % 3, key]
                       for key in range(-7, 90)} for table in range(2)]
            with checks.session() as session:
                session.shutdown()
                session.command = ["env", f"MVDB_CACHE_BYTES={budget}", *session.command]
                session.start()
                for table, model in enumerate(models):
                    checks.ok(session, f"CREATE TABLE t{table}(body VARCHAR(4096),v VECTOR(1),"
                                       "g BIGINT,id BIGINT PRIMARY KEY);")
                    values = ",".join(f"({sql_quote(body)},'{json.dumps(vector)}',{group},{key})"
                                      for body, vector, group, key in model.values())
                    checks.ok(session, f"INSERT INTO t{table} VALUES {values};")
                phase(session, models, f"{budget}:before-index")
                for table in range(2):
                    checks.ok(session, f"CREATE VECTOR INDEX vi{table} ON t{table}(v);")
                phase(session, models, f"{budget}:indexed-warm")
                saved = copy.deepcopy(models)
                checks.ok(session, "BEGIN;")
                for table, model in enumerate(models):
                    checks.ok(session, f"UPDATE t{table} SET body='changed',v='[9.5]' WHERE g=1;")
                    for row in model.values():
                        if row[2] == 1:
                            row[0], row[1] = "changed", [9.5]
                    checks.ok(session, f"DELETE FROM t{table} WHERE id>=60;")
                    models[table] = {key: row for key, row in model.items() if key < 60}
                phase(session, models, f"{budget}:transaction-overlay")
                checks.ok(session, "ROLLBACK;")
                models = saved
                phase(session, models, f"{budget}:rollback")
                # Scan modifications borrow original rows, then stage owned copies.
                for table, model in enumerate(models):
                    checks.ok(session, f"UPDATE t{table} SET body='committed' WHERE g=2;")
                    for row in model.values():
                        if row[2] == 2:
                            row[0] = "committed"
                phase(session, models, f"{budget}:scan-update-commit")
                session.crash()
                session.start()
                phase(session, models, f"{budget}:kill-recovery")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
