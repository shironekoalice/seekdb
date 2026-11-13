#!/usr/bin/env python3
"""Independent semantic, randomized and process-crash checks over public JSONL."""
import argparse
from contextlib import contextmanager
import copy
import json
import math
from pathlib import Path
import random
import struct
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"protocol_check"))
from _transport import Session


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def sql_quote(text):
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


class Checks:
    def __init__(self, args):
        self.args, self.sequence, self.results = args, 0, []
        self.config = json.loads((ROOT/"protocol_check/config.json").read_text())
        self.config["case_timeout"] = 1200

    @contextmanager
    def session(self):
        self.sequence += 1
        command = [str(self.args.binary), "--data-dir", "{data_dir}"] if self.args.binary else None
        session = Session(self.config, self.args.output/f"session-{self.sequence}.log",
                          command=command, image=self.args.image)
        try:
            session.start()
            yield session
            session.shutdown()
        finally:
            session.close()

    @staticmethod
    def ok(s, sql, mode="exact"):
        result = s.request(sql, mode)
        assert result["ok"], (sql, result)
        return result

    @staticmethod
    def bad(s, sql):
        result = s.request(sql)
        assert result["ok"] is False and result.get("error", {}).get("code"), (sql, result)

    def check(self, name, method):
        start = time.monotonic()
        try:
            detail = method()
            row = {"name": name, "passed": True, "detail": detail}
        except Exception:
            row = {"name": name, "passed": False, "error": traceback.format_exc()}
        row["seconds"] = round(time.monotonic()-start, 3)
        self.results.append(row)
        print(name, "PASS" if row["passed"] else "FAIL", flush=True)
        if not row["passed"]:
            print(row["error"], flush=True)
        self.save()

    def save(self):
        (self.args.output/"result.json").write_text(json.dumps({
            "scope": "independent local tests; not hidden formal cases",
            "passed": all(r["passed"] for r in self.results), "checks": self.results,
        }, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    def types(self):
        with self.session() as s:
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY, text VARCHAR(3), v VECTOR(2));")
            self.ok(s, "INSERT INTO t VALUES (-9223372036854775808,'中😀a','[16777217,0]'),(9223372036854775807,'中😀b','[0,0]');")
            rows = self.ok(s, "SELECT id,text,v FROM t ORDER BY id;")["rows"]
            assert rows == [[-9223372036854775808, "中😀a", [16777216, 0]], [9223372036854775807, "中😀b", [0, 0]]]
            for value in ("9223372036854775808", "-9223372036854775809", "1.2", "'1'"):
                self.bad(s, f"INSERT INTO t VALUES ({value},'ok','[0,0]');")
            for vector in ("[NaN,0]", "[Infinity,0]", "[1e40,0]", "[0]", "[0,0,0]", "[true,0]", '["0",0]'):
                self.bad(s, f"INSERT INTO t VALUES (2,'ok',{sql_quote(vector)});")
            self.bad(s, "INSERT INTO t VALUES (2,'中文😀a','[0,0]');")
            self.bad(s, "SELECT absent FROM t WHERE id=123;")
            self.bad(s, "SELECT id FROM t WHERE absent=123;")
            self.bad(s, "SELECT id,L2_DISTANCE(v,'[1]') AS d FROM t;")
            self.bad(s, "ALTER TABLE t ADD x BIGINT;")
            self.bad(s, "UPDATE t SET id=4 WHERE id=1;")
            self.bad(s, "CREATE TABLE t2 (id BIGINT PRIMARY KEY, a VECTOR(2), b VECTOR(2));")
            assert self.ok(s, "SELECT id FROM t WHERE text='longer-than-three';")["rows"] == []
            assert self.ok(s, "UPDATE t SET text='ok' WHERE text='longer-than-three';")["affected_rows"] == 0
            assert self.ok(s, "DELETE FROM t WHERE text='longer-than-three';")["affected_rows"] == 0
            self.bad(s, "CREATE TABLE `bad/name` (id BIGINT PRIMARY KEY);")
            assert len(self.ok(s, "SELECT id FROM t;")["rows"]) == 2
        return "BIGINT boundaries, Unicode length, finite float32, types, invalid syntax"

    def transaction(self):
        with self.session() as s:
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY, text VARCHAR(8), v VECTOR(2));")
            self.ok(s, "INSERT INTO t VALUES (1,'a','[0,0]'),(2,'b','[1,0]');")
            self.ok(s, "CREATE VECTOR INDEX vi ON t(v);")
            self.ok(s, "BEGIN;")
            self.ok(s, "UPDATE t SET v='[9,0]' WHERE id=1;")
            self.bad(s, "INSERT INTO t VALUES (3,'c','[0,0]'),(2,'dup','[0,0]');")
            self.bad(s, "UPDATE t SET text='too-long-string' WHERE id>=1;")
            self.ok(s, "DELETE FROM t WHERE id=2;")
            self.ok(s, "INSERT INTO t VALUES (2,'new','[2,0]');")
            query = "SELECT id,L2_DISTANCE(v,'[0,0]') AS d FROM t ORDER BY d LIMIT 5;"
            assert self.ok(s, query, "ann")["rows"] == [[2, 2.0], [1, 9.0]]
            self.ok(s, "ROLLBACK;")
            assert self.ok(s, query)["rows"] == [[1, 0.0], [2, 1.0]]
            self.ok(s, "BEGIN;")
            self.ok(s, "UPDATE t SET text='commit' WHERE id=1;")
            self.bad(s, "INSERT INTO t VALUES (3,'c','[0,0]'),(3,'dup','[0,0]');")
            self.ok(s, "COMMIT;")
            s.crash(); s.start()
            assert self.ok(s, "SELECT id,text FROM t ORDER BY id;")["rows"] == [[1, "commit"], [2, "b"]]
            self.ok(s, "BEGIN;")
            self.ok(s, "DELETE FROM t;")
            s.crash(); s.start()
            assert len(self.ok(s, "SELECT id FROM t;")["rows"]) == 2
        return "statement rollback, read-your-writes, vector visibility, acknowledged commit and uncommitted crash"

    def random_model(self):
        rng = random.Random(20261113)
        with self.session() as s:
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY, group_id BIGINT, v VECTOR(4));")
            model, saved = {}, None
            self.ok(s, "CREATE VECTOR INDEX vi ON t(v);")
            for step in range(240):
                action = rng.choice(["put", "put", "update", "delete", "query", "begin", "end", "restart"])
                key = rng.randrange(60)
                if action == "begin" and saved is None:
                    self.ok(s, "BEGIN;"); saved = copy.deepcopy(model)
                elif action == "end" and saved is not None:
                    if rng.randrange(2):
                        self.ok(s, "COMMIT;")
                    else:
                        self.ok(s, "ROLLBACK;"); model = saved
                    saved = None
                elif action == "restart":
                    s.shutdown(); s.start()
                    if saved is not None: model, saved = saved, None
                elif action == "put":
                    group = rng.randrange(4)
                    vector = [f32(rng.uniform(-3, 3)) for _ in range(4)]
                    sql = f"INSERT INTO t VALUES ({key},{group},'{json.dumps(vector)}');"
                    if key in model: self.bad(s, sql)
                    else:
                        self.ok(s, sql); model[key] = (group, vector)
                elif action == "delete":
                    result = self.ok(s, f"DELETE FROM t WHERE id={key};")
                    assert result["affected_rows"] == int(key in model)
                    model.pop(key, None)
                elif action == "update":
                    v = [f32(rng.uniform(-3, 3)) for _ in range(4)]
                    self.ok(s, f"UPDATE t SET v='{json.dumps(v)}' WHERE id={key};")
                    if key in model: model[key] = (model[key][0], v)
                if step % 5 == 0:
                    actual = self.ok(s, "SELECT id,group_id,v FROM t ORDER BY id;")["rows"]
                    expected = [[k, g, v] for k, (g, v) in sorted(model.items())]
                    assert actual == expected, (step, actual, expected)
                    group = rng.randrange(4)
                    q = [f32(rng.uniform(-2, 2)) for _ in range(4)]
                    expected = sorted([(math.sqrt(sum((a-b)**2 for a, b in zip(v, q))), k)
                                       for k, (g, v) in model.items() if g == group])[:10]
                    sql = f"SELECT id,L2_DISTANCE(v,'{json.dumps(q)}') AS d FROM t WHERE group_id={group} ORDER BY d LIMIT 10;"
                    actual = self.ok(s, sql, "ann" if step % 2 else "exact")["rows"]
                    assert [r[0] for r in actual] == [k for d, k in expected]
                    assert all(math.isclose(r[1], d, rel_tol=1e-5, abs_tol=1e-5)
                               for r, (d, k) in zip(actual, expected))
        return "240 seeded operations, 48 state comparisons and 48 independent filtered TopK comparisons"

    def ordering(self):
        with self.session() as s:
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY, g BIGINT, text VARCHAR(16), v VECTOR(2));")
            self.ok(s, "INSERT INTO t VALUES (3,1,'中','[1,0]'),(1,1,'😀','[1,0]'),(2,2,'a;''b','[0,0]'),(4,1,'z','[1,0]');")
            assert self.ok(s, "SELECT id FROM t ORDER BY g DESC LIMIT 3;")["rows"] == [[2], [1], [3]]
            assert self.ok(s, "SELECT id FROM t ORDER BY text;")["rows"] == [[2], [4], [3], [1]]
            query = "SELECT id,L2_DISTANCE(v,'[0,0]') AS d FROM t WHERE g=1 ORDER BY d LIMIT 2;"
            assert self.ok(s, query)["rows"] == [[1, 1.0], [3, 1.0]]
            assert self.ok(s, "SELECT * FROM t LIMIT 0;")["rows"] == []
            self.ok(s, "CREATE VECTOR INDEX vi ON t(v);")
            self.ok(s, "UPDATE t SET g=2 WHERE id=1;")
            assert self.ok(s, query, "ann")["rows"] == [[3, 1.0], [4, 1.0]]
            self.ok(s, "DELETE FROM t WHERE id=3;")
            s.shutdown(); s.start()
            assert self.ok(s, query, "ann")["rows"] == [[4, 1.0]]
        return "filter-before-TopK, ASC/DESC ties, Unicode order, quotes, deleted/stale index entries"

    def ddl(self):
        with self.session() as s:
            self.ok(s, "BEGIN;")
            self.ok(s, "CREATE TABLE t (v VECTOR(1), id BIGINT PRIMARY KEY);")
            self.ok(s, "INSERT INTO t (id,v) VALUES (7,'[1]');")
            self.ok(s, "CREATE VECTOR INDEX vi ON t(v);")
            assert self.ok(s, "SELECT * FROM t;")["rows"] == [[[1.0], 7]]
            self.ok(s, "ROLLBACK;")
            self.bad(s, "SELECT * FROM t;")
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY);")
            self.ok(s, "CREATE TABLE other (id BIGINT PRIMARY KEY);")
            self.ok(s, "BEGIN;")
            self.ok(s, "INSERT INTO t VALUES (1);")
            self.ok(s, "INSERT INTO other VALUES (2);")
            self.ok(s, "COMMIT;")
            s.shutdown(); s.start()
            assert self.ok(s, "SELECT * FROM t;")["rows"] == [[1]]
            assert self.ok(s, "SELECT * FROM other;")["rows"] == [[2]]
        return "transactional schema, non-leading PK, column lists, multi-table atomic commit"

    def fault(self, point):
        assert self.args.binary, "fault hooks are local-only instrumentation"
        with self.session() as s:
            torn = point == "commit_partial_append"
            self.ok(s, "CREATE TABLE t (id BIGINT PRIMARY KEY"+(",body VARCHAR(131072)" if torn else "")+");")
            self.ok(s, "INSERT INTO t VALUES "+("(1,'initial');" if torn else "(1);"))
            s.shutdown()
            normal = s.command[:]
            s.command = ["env", "MVDB_FAILPOINT="+point, *normal]
            if point == "recovery_after_record":
                try:
                    s.start()
                    raise AssertionError("recovery failpoint did not terminate")
                except AssertionError:
                    raise
                except Exception:
                    assert s.proc.wait(timeout=5) == 86
                s.stop()
            else:
                s.start()
                self.ok(s, "BEGIN;")
                # >64 KiB payload forces the first copy chunk to stop inside a
                # WAL frame, rather than merely before an absent commit marker.
                values = "(2,'"+"x"*70000+"'),(3,'end');" if torn else "(2),(3);"
                self.ok(s, "INSERT INTO t VALUES "+values)
                try:
                    self.ok(s, "COMMIT;")
                    raise AssertionError("commit failpoint did not terminate")
                except AssertionError:
                    raise
                except Exception:
                    assert s.proc.wait(timeout=5) == 86
                s.stop()
            s.command = normal; s.start()
            actual = self.ok(s, "SELECT id FROM t ORDER BY id;")["rows"]
            expected = [[1], [2], [3]] if point in ("commit_after_sync", "commit_after_apply") else [[1]]
            assert actual == expected, (point, actual)
            s.shutdown(); s.start()
            assert self.ok(s, "SELECT id FROM t ORDER BY id;")["rows"] == expected
        return "exit 86 observed; recovered state correct twice: "+point


def main():
    p = argparse.ArgumentParser(description=__doc__)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--check", action="append", choices=["types", "transaction", "random_model", "ordering", "ddl"])
    args = p.parse_args()
    if args.binary: args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    tests = Checks(args)
    for name in (args.check or ("types", "transaction", "random_model", "ordering", "ddl")):
        tests.check(name, getattr(tests, name))
    if args.binary and not args.check:
        for point in ("commit_before_append", "commit_partial_append", "commit_before_marker",
                      "commit_after_sync", "commit_after_apply", "recovery_after_record"):
            tests.check(point, lambda point=point: tests.fault(point))
    return 0 if all(r["passed"] for r in tests.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
