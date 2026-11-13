#!/usr/bin/env python3
"""Independent full-sort oracle across scan/index/transaction query paths."""
import argparse
import copy
from functools import cmp_to_key
import json
import math
from pathlib import Path
import random
import traceback

from contract import Checks, f32, sql_quote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.binary:
        args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    checks = Checks(args)
    report = {"scope": "independent local query-path oracle", "passed": False,
              "seed": 20261114, "phases": [], "exact_queries": 0}
    rng = random.Random(report["seed"])
    fields = {"label": 0, "v": 1, "g": 2, "id": 3}
    qa = tuple(f32(x) for x in [-0.7, 0.3, 0.9, -1.1, 0.01])
    qb = tuple(f32(x) for x in [1.4, -0.6, 0.2, 0.01, -0.5])

    def expression(spec):
        return spec if isinstance(spec, str) else f"L2_DISTANCE(v,{sql_quote(json.dumps(spec))})"

    def evaluate(row, spec):
        if isinstance(spec, str):
            return row[fields[spec]]
        return math.sqrt(sum((a-b)**2 for a, b in zip(row[1], spec)))

    # Two different distance expressions, secondary orders, DESC and payload
    # projections prevent a path specialized to one benchmark SQL from passing.
    cases = [
        (["id", qa], [(qa, False)], "", lambda r: True, 10, True),
        (["id", qb, "label", "v"], [(qa, False)], "", lambda r: True, 7, False),
        (["id", qa], [(qa, True)], "", lambda r: True, 12, False),
        (["label", "id", qa], [(qa, False), ("g", True)], "", lambda r: True, 11, True),
        (["id", qa, qb], [(qa, False), (qb, True)], "", lambda r: True, 10, False),
        (["id", qa], [("label", True), (qa, False)], "", lambda r: True, 13, False),
        (["id", qa], [(qa, False)], "g=1", lambda r: r[2] == 1, 10, True),
        (["id", qa], [(qa, False)], "g<2", lambda r: r[2] < 2, 10, False),
        (["id", qa], [(qa, True)], "g>1", lambda r: r[2] > 1, 4, False),
        (["id", qb], [(qa, False)], "g<=2 AND id>=0",
         lambda r: r[2] <= 2 and r[3] >= 0, 8, False),
        (["id", "label", qa], [(qa, False)], "label>='中'",
         lambda r: r[0] >= "中", 20, False),
        (["id", qa], [(qa, False)], "label='longer-than-declared-length'",
         lambda r: False, 10, False),
        (["id", qa], [(qa, False)], "id=9223372036854775807",
         lambda r: r[3] == 9223372036854775807, 10, True),
        (["id", qa], [(qa, False)], "id=-9223372036854775808",
         lambda r: r[3] == -9223372036854775808, 10, False),
        (["id", "label", "v"], [("g", False)], "g>=0", lambda r: r[2] >= 0, None, False),
        (["id", qa], [(qa, False)], "", lambda r: True, 0, True),
        (["id", qa], [(qa, False)], "", lambda r: True, None, False),
    ]
    keys = [-9223372036854775808, 9223372036854775807,
            -9007199254740993, 9007199254740993, *range(-3, 32)]
    model = {}
    for i, key in enumerate(keys):
        vector = [f32(rng.uniform(-2, 2)) for _ in range(5)]
        if i < 4:
            vector = [1.0, 0.0, 0.0, 0.0, 0.0]
        if i == 4:
            vector = [f32(3e38), f32(1e-38), -0.0, 0.0, 0.0]
        model[key] = [["a", "z", "中", "😀"][i % 4], vector, i % 4, key]

    def insert_sql(row):
        label, vector, group, key = row
        return f"INSERT INTO paths VALUES ({sql_quote(label)},{sql_quote(json.dumps(vector))},{group},{key});"

    def phase(session, name):
        for projection, order, where, predicate, limit, alias in cases:
            projected = [expression(e) for e in projection]
            if alias:
                projected = [e + (f" AS d{i}" if not isinstance(projection[i], str) else "")
                             for i, e in enumerate(projected)]
            orders = []
            for spec, desc in order:
                token = expression(spec)
                if alias and spec in projection and not isinstance(spec, str):
                    token = f"d{projection.index(spec)}"
                orders.append(token + (" DESC" if desc else " ASC"))
            sql = "SELECT " + ",".join(projected) + " FROM paths"
            if where:
                sql += " WHERE " + where
            sql += " ORDER BY " + ",".join(orders)
            if limit is not None:
                sql += " LIMIT " + str(limit)
            sql += ";"

            def cmp(a, b):
                for spec, desc in order:
                    x, y = evaluate(a, spec), evaluate(b, spec)
                    value = (x > y) - (x < y)
                    if value:
                        return -value if desc else value
                return (a[3] > b[3]) - (a[3] < b[3])

            expected_rows = sorted((r for r in model.values() if predicate(r)), key=cmp_to_key(cmp))
            if limit is not None:
                expected_rows = expected_rows[:limit]
            expected = [[evaluate(r, e) for e in projection] for r in expected_rows]
            actual = checks.ok(session, sql)["rows"]
            assert len(actual) == len(expected), (name, sql, actual, expected)
            for a, b in zip(actual, expected):
                for i, (x, y) in enumerate(zip(a, b)):
                    if not isinstance(projection[i], str):
                        assert math.isclose(x, y, abs_tol=1e-5, rel_tol=1e-5), (name, sql, x, y)
                    else:
                        assert x == y, (name, sql, x, y)
            report["exact_queries"] += 1
        report["phases"].append({"name": name, "rows": len(model), "queries": len(cases), "passed": True})

    try:
        with checks.session() as s:
            checks.ok(s, "CREATE TABLE paths(label VARCHAR(12),v VECTOR(5),g BIGINT,id BIGINT PRIMARY KEY);")
            # A selected fast path must not hide invalid expressions on empty
            # inputs, absent keys or LIMIT 0.
            for sql in (
                "SELECT id FROM paths WHERE absent=1;",
                "SELECT id FROM paths WHERE id='x' LIMIT 0;",
                "SELECT id FROM paths WHERE id=0 AND g='x';",
                "SELECT L2_DISTANCE(v,'[1]') FROM paths LIMIT 0;",
                "SELECT id FROM paths ORDER BY absent LIMIT 0;",
                "SELECT id FROM paths WHERE v='[0,0,0,0,0]';",
            ):
                checks.bad(s, sql)
            for row in model.values():
                checks.ok(s, insert_sql(row))
            phase(s, "no-index")
            checks.ok(s, "CREATE VECTOR INDEX paths_vi ON paths(v);")
            phase(s, "index")
            checks.ok(s, "UPDATE paths SET v='[0,0,0,0,0]',g=3,label='changed' WHERE id=7;")
            model[7] = ["changed", [0.0]*5, 3, 7]
            key = -9223372036854775808
            checks.ok(s, f"DELETE FROM paths WHERE id={key};")
            model[key] = ["new", list(qb), 1, key]
            checks.ok(s, insert_sql(model[key]))
            phase(s, "stale-entries-delete-reinsert")
            saved = copy.deepcopy(model)
            checks.ok(s, "BEGIN;")
            checks.ok(s, "UPDATE paths SET v='[9,8,7,6,5]',label='pending' WHERE id=7;")
            model[7][0:2] = ["pending", [9.0, 8.0, 7.0, 6.0, 5.0]]
            checks.ok(s, f"DELETE FROM paths WHERE id={key};")
            del model[key]
            model[7000] = ["中", list(qa), 1, 7000]
            checks.ok(s, insert_sql(model[7000]))
            phase(s, "transaction-pending")
            checks.ok(s, "ROLLBACK;")
            model = saved
            phase(s, "rollback")
            checks.ok(s, "BEGIN;")
            checks.ok(s, "UPDATE paths SET g=1 WHERE id=7;")
            model[7][2] = 1
            checks.ok(s, "COMMIT;")
            phase(s, "commit")
            s.shutdown()
            s.start()
            phase(s, "restart")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (args.output/"result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
