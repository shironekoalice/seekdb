#!/usr/bin/env python3
"""Independent final-result, SQL-token and wide-key admission counterexamples."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "protocol_check"))
from _transport import Session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="append", choices=[
        "final_topk", "response_boundary", "identifier_literals", "declared_size",
        "wide_key_ordering"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / "protocol_check/config.json").read_text())
    checks = []
    report = {"scope": "local admission counterexamples; not official hidden cases",
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "checks": checks}

    def run(name, method):
        start = time.monotonic()
        command = [str(args.binary.resolve()), "--data-dir", "{data_dir}"] if args.binary else None
        session = Session(config, args.output / (name + ".log"),
                          command=command, image=args.image)
        try:
            session.start()
            detail = method(session)
            session.shutdown()
            row = {"name": name, "passed": True, "detail": detail}
        except Exception:
            row = {"name": name, "passed": False, "error": traceback.format_exc()}
        finally:
            session.close()
        row["seconds"] = time.monotonic() - start
        checks.append(row)
        print(name, "PASS" if row["passed"] else "FAIL", flush=True)
        report["passed"] = all(c["passed"] for c in checks)
        (args.output / "result.json").write_text(json.dumps(
            report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def ok(session, sql):
        response = session.request(sql)
        assert response["ok"], (sql[:160], response)
        return response

    def final_topk(s):
        ok(s, "CREATE TABLE t(id BIGINT PRIMARY KEY,g BIGINT,body VARCHAR(8388608));")
        body = "x" * (8 * 1048576)
        for key in (0, 1):
            ok(s, f"INSERT INTO t VALUES({key},9,'{body}');")
        ok(s, "INSERT INTO t VALUES(2,0,'a'),(3,0,'b');")
        actual = ok(s, "SELECT body FROM t ORDER BY g LIMIT 2;")["rows"]
        assert actual == [["a"], ["b"]], actual
        # A final oversize result must fail without breaking the transaction.
        ok(s, "BEGIN;")
        ok(s, "UPDATE t SET g=8 WHERE id=0;")
        bad = s.request("SELECT body FROM t WHERE id<2 ORDER BY id;")
        assert bad["ok"] is False and bad["error"]["code"], bad
        ok(s, "COMMIT;")
        assert ok(s, "SELECT g FROM t WHERE id=0;")["rows"] == [[8]]
        return "Final two rows are 11 bytes although the initial two candidates exceed 16 MiB; oversize query leaves transaction usable."

    def response_boundary(s):
        size = 15 * 1048576 + 32768
        ok(s, f"CREATE TABLE t(id BIGINT PRIMARY KEY,body VARCHAR({size}));")
        ok(s, f"INSERT INTO t VALUES(1,'{'x' * size}');")
        actual = ok(s, "SELECT body FROM t WHERE id=1;")
        assert actual["rows"] == [["x" * size]]
        wire = len(json.dumps(actual, ensure_ascii=False, separators=(",", ":")).encode()) + 1
        assert 15 * 1048576 < wire < config["max_response_bytes"], wire
        return {"actual_response_bytes": wire, "public_max_response_bytes": config["max_response_bytes"]}

    def identifier_literals(s):
        ok(s, "CREATE TABLE t(id BIGINT PRIMARY KEY,g BIGINT,v VECTOR(1));")
        for sql in [
            "INSERT INTO t VALUES(1,`2`,'[0]');",
            "INSERT INTO t VALUES(1,2,`[0]`);",
            "SELECT id FROM t WHERE g=`2`;",
            "SELECT id,L2_DISTANCE(v,`[0]`) AS d FROM t;",
        ]:
            bad = s.request(sql)
            assert bad["ok"] is False and bad["error"]["code"], (sql, bad)
        ok(s, "INSERT INTO t VALUES(1,2,'[0]');")
        # Quoted identifiers must remain identifiers even when named keywords.
        ok(s, "CREATE TABLE names(id BIGINT PRIMARY KEY,`l2_distance` BIGINT);")
        ok(s, "INSERT INTO names VALUES(1,7);")
        assert ok(s, "SELECT `l2_distance` FROM names;")["rows"] == [[7]]
        return "Reject identifier tokens used as scalar/vector literals; preserve keyword identifiers."

    def declared_size(s):
        ok(s, "CREATE TABLE t(id BIGINT PRIMARY KEY,body VARCHAR(100000001));")
        ok(s, "INSERT INTO t VALUES(1,'small');")
        assert ok(s, "SELECT body FROM t;")["rows"] == [["small"]]
        return "No published 100000000 limit exists for VARCHAR declarations; small stored value remains valid."

    def wide_key_ordering(s):
        ok(s, "CREATE TABLE t(id BIGINT PRIMARY KEY,label VARCHAR(20000));")
        labels = {1: "x" * 16383 + "😀", 2: "x" * 16383 + "中",
                  3: "x" * 16383, 4: "z" * 17000, 5: "", 6: "x" * 16383 + "中"}
        for key, label in labels.items():
            ok(s, f"INSERT INTO t VALUES({key},'{label}');")
        for descending in (False, True):
            expected = sorted(labels, key=lambda k: k)
            expected.sort(key=lambda k: labels[k], reverse=descending)
            direction = "DESC" if descending else "ASC"
            assert ok(s, f"SELECT id FROM t ORDER BY label {direction};")["rows"] == [[k] for k in expected]
        ok(s, "BEGIN;")
        ok(s, "UPDATE t SET label='a' WHERE id=1;")
        assert ok(s, "SELECT id FROM t ORDER BY label LIMIT 2;")["rows"] == [[5], [1]]
        ok(s, "ROLLBACK;")
        s.crash()
        s.start()
        assert ok(s, "SELECT id FROM t ORDER BY label LIMIT 2;")["rows"] == [[5], [3]]
        return "Wide keys cross 8KiB blocks, Unicode suffixes, prefix order, ASC/DESC ties, transaction and kill recovery."

    selected = args.check or ["final_topk", "response_boundary", "identifier_literals",
                              "declared_size", "wide_key_ordering"]
    methods = locals()
    for name in selected:
        run(name, methods[name])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
