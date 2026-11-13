#!/usr/bin/env python3
"""Independent PK equality CRUD oracle, eager errors and merged visibility."""
import argparse
import copy
import json
import math
from pathlib import Path
import random
import traceback
from contract import Checks, sql_quote


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
    report = {"scope": "Python PK CRUD oracle; not formal cases", "passed": False,
              "seed": 20261114, "phases": [], "point_queries": 0, "scan_controls": 0,
              "errors_checked": 0, "random_operations": 0}
    rng = random.Random(report["seed"])
    keys = [-9223372036854775808, 9223372036854775807,
            -9007199254740993, 9007199254740993, *range(-4, 20)]
    model = {key: [["中", "😀", "x"][i % 3], i % 4, [float(i % 3), -1.0], key]
             for i, key in enumerate(keys)}

    def insert(session, row):
        label, group, vector, key = row
        result = checks.ok(session, f"INSERT INTO p VALUES ({sql_quote(label)},{group},{sql_quote(json.dumps(vector))},{key});")
        assert result["affected_rows"] == 1

    def phase(session, name):
        for key in [*keys, 777]:
            for tail, predicate in (
                ("", lambda r: True),
                (" AND g=2", lambda r: r[1] == 2),
                (f" AND id={key+1 if key < 9223372036854775807 else key-1}", lambda r: False),
                (" AND label='longer-than-declared-length'", lambda r: False),
            ):
                row = model.get(key)
                expected = [row] if row is not None and predicate(row) else []
                sql = f"SELECT * FROM p WHERE g>=0 AND id={key}{tail} ORDER BY label DESC;"
                actual = checks.ok(session, sql)["rows"]
                assert actual == expected, (name, sql, actual, expected)
                report["point_queries"] += 1
                # Equivalent range predicates force the original scan path.
                scan_tail = tail
                if tail.startswith(" AND id="):
                    other = tail.removeprefix(" AND id=")
                    scan_tail = f" AND id>={other} AND id<={other}"
                scan = f"SELECT * FROM p WHERE g>=0 AND id>={key} AND id<={key}{scan_tail} ORDER BY label DESC;"
                assert "id=" not in scan, scan
                assert checks.ok(session, scan)["rows"] == expected, (name, scan)
                report["scan_controls"] += 1
            expected = model.get(key)
            result = checks.ok(session,
                f"SELECT id,L2_DISTANCE(v,'[0,0]') AS d FROM p WHERE id={key} ORDER BY d DESC LIMIT 3;")["rows"]
            assert len(result) == int(expected is not None), (name, key, result)
            if expected is not None:
                assert result[0][0] == key
                assert math.isclose(result[0][1], math.sqrt(sum(x*x for x in expected[2])), abs_tol=1e-5, rel_tol=1e-5)
            assert checks.ok(session, f"SELECT id FROM p WHERE id={key} LIMIT 0;")["rows"] == []
            report["point_queries"] += 2
        actual = checks.ok(session, "SELECT * FROM p ORDER BY id;")["rows"]
        assert actual == [model[key] for key in sorted(model)], (name, actual, model)
        report["phases"].append({"name": name, "rows": len(model), "passed": True})

    def errors(session):
        for where in ("id=777", "id=1 AND id=2"):
            for sql in (
                f"SELECT absent FROM p WHERE {where};",
                f"SELECT id FROM p WHERE {where} AND absent=1;",
                f"SELECT id FROM p WHERE {where} AND g='bad' LIMIT 0;",
                f"SELECT id FROM p WHERE {where} AND id=9223372036854775808;",
                f"SELECT id FROM p WHERE {where} AND v='[0,0]';",
                f"SELECT id FROM p WHERE {where} ORDER BY absent LIMIT 0;",
                f"SELECT L2_DISTANCE(v,'[0]') FROM p WHERE {where} LIMIT 0;",
                f"UPDATE p SET g='bad' WHERE {where};",
                f"UPDATE p SET absent=1 WHERE {where};",
                f"UPDATE p SET label='too-long' WHERE {where};",
                f"UPDATE p SET v='[0]' WHERE {where};",
                f"UPDATE p SET id=1 WHERE {where};",
                f"UPDATE p SET g=1,g=2 WHERE {where};",
                f"UPDATE p SET g=1 WHERE {where} AND absent=1;",
                f"DELETE FROM p WHERE {where} AND g='bad';",
            ):
                checks.bad(session, sql)
                report["errors_checked"] += 1

    try:
        with checks.session() as s:
            checks.ok(s, "CREATE TABLE p(label VARCHAR(4),g BIGINT,v VECTOR(2),id BIGINT PRIMARY KEY);")
            errors(s)
            for row in model.values():
                insert(s, row)
            phase(s, "no-index")
            checks.ok(s, "CREATE VECTOR INDEX pvi ON p(v);")
            phase(s, "indexed")
            saved = copy.deepcopy(model)
            checks.ok(s, "BEGIN;")
            for step in range(120):
                key = rng.choice([*keys, 777])
                operation = rng.choice(["update", "delete", "insert"])
                condition = step % 3 != 0
                tail = "" if condition else " AND g=999"
                if operation == "update":
                    expected = int(key in model and condition)
                    actual = checks.ok(s, f"UPDATE p SET g=7,v='[2,3]' WHERE id={key}{tail};")
                    assert actual["affected_rows"] == expected
                    if expected:
                        model[key][1:3] = [7, [2.0, 3.0]]
                elif operation == "delete":
                    expected = int(key in model and condition)
                    assert checks.ok(s, f"DELETE FROM p WHERE id={key}{tail};")["affected_rows"] == expected
                    if expected:
                        del model[key]
                elif key not in model:
                    model[key] = ["new", 1, [1.0, 2.0], key]
                    insert(s, model[key])
                else:
                    checks.bad(s, f"INSERT INTO p VALUES ('dup',0,'[0,0]',{key});")
                    report["errors_checked"] += 1
                report["random_operations"] += 1
            errors(s)
            phase(s, "pending-random-and-failed-statements")
            checks.ok(s, "ROLLBACK;")
            model = saved
            phase(s, "rollback")
            checks.ok(s, "BEGIN;")
            checks.ok(s, "UPDATE p SET g=8,v='[4,5]' WHERE id=0;")
            model[0][1:3] = [8, [4.0, 5.0]]
            checks.ok(s, "DELETE FROM p WHERE id=1;")
            del model[1]
            checks.ok(s, "UPDATE p SET g=6 WHERE id=0 AND id=1;")
            checks.ok(s, "DELETE FROM p WHERE id=0 AND label='never';")
            insert(s, ["back", 3, [6.0, 7.0], 1])
            model[1] = ["back", 3, [6.0, 7.0], 1]
            checks.bad(s, "INSERT INTO p VALUES ('ok',1,'[0,0]',777),('dup',1,'[0,0]',0);")
            assert checks.ok(s, "SELECT id FROM p WHERE id=777;")["rows"] == []
            checks.ok(s, "COMMIT;")
            phase(s, "committed-delete-reinsert")
            # Acknowledged commit survives kill; uncommitted PK writes do not.
            checks.ok(s, "BEGIN;")
            checks.ok(s, "UPDATE p SET g=100 WHERE id=0;")
            checks.ok(s, "DELETE FROM p WHERE id=1;")
            s.crash()
            s.start()
            phase(s, "kill-recovery")
            s.shutdown()
            s.start()
            phase(s, "repeated-recovery")
            # Newly created tables have only a pending map before COMMIT.
            checks.ok(s, "BEGIN;")
            checks.ok(s, "CREATE TABLE fresh(n BIGINT,id BIGINT PRIMARY KEY);")
            checks.ok(s, "INSERT INTO fresh VALUES (1,0);")
            assert checks.ok(s, "UPDATE fresh SET n=2 WHERE id=0;")["affected_rows"] == 1
            assert checks.ok(s, "SELECT n FROM fresh WHERE id=0;")["rows"] == [[2]]
            assert checks.ok(s, "DELETE FROM fresh WHERE id=0;")["affected_rows"] == 1
            assert checks.ok(s, "SELECT n FROM fresh WHERE id=0;")["rows"] == []
            checks.ok(s, "INSERT INTO fresh VALUES (3,0);")
            checks.ok(s, "ROLLBACK;")
            checks.bad(s, "SELECT n FROM fresh WHERE id=0;")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report["scan_control_kind"] = "range-only PK conditions; no PK equality in any control"
    (args.output/"result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
