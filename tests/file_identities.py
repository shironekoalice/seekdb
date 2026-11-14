#!/usr/bin/env python3
"""Independent state oracle for long SQL names and internal file identities."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
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
    parser.add_argument("--previous-binary", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    assert not args.previous_binary or args.binary
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / "protocol_check/config.json").read_text())
    command = [str(args.binary), "--data-dir", "{data_dir}"] if args.binary else None
    session = Session(config, args.output / "database.log", command=command, image=args.image)
    report = {"passed": False, "scope": "local long-name and derived-file oracle",
              "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "phases": []}
    if args.binary:
        report["binary_sha256"] = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    else:
        report["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", "-f", "{{.Id}}", args.image], text=True).strip()
    started = time.monotonic()
    # Deliberately reverse lexical/creation order, share long prefixes, and
    # include names similar to all internal scratch/derived files.
    names = ["z" * 260, "a" * 248, "b" * 1024, "c" * 4096,
             "shared_" + "x" * 300 + "a", "shared_" + "x" * 300 + "b",
             "table_0", "pending_0", "statement", "transaction", "query_order"]
    model = {}

    def ok(sql):
        result = session.request(sql)
        assert result["ok"], (sql[:180], result)
        return result

    def check(phase):
        for name, rows in model.items():
            expected = [[key, value[0], value[1]] for key, value in sorted(rows.items())]
            assert ok(f"SELECT id,body,v FROM {name} ORDER BY id;")["rows"] == expected
            ranked = sorted(rows, key=lambda k: (rows[k][1][0] ** 2 + rows[k][1][1] ** 2, k))
            assert ok(f"SELECT id FROM {name} ORDER BY L2_DISTANCE(v,'[0,0]') LIMIT 3;")["rows"] == [[k] for k in ranked[:3]]
        report["phases"].append({"name": phase, "tables": len(model), "passed": True})

    try:
        session.start()
        for i, name in enumerate(names):
            ok(f"CREATE TABLE {name}(id BIGINT PRIMARY KEY,body VARCHAR(20),v VECTOR(2));")
            ok(f"INSERT INTO {name} VALUES(0,'base','[{i},1]'),(18956,'tie','[{i},1]');")
            ok(f"CREATE VECTOR INDEX vi_{i} ON {name}(v);")
            model[name] = {0: ["base", [i, 1]], 18956: ["tie", [i, 1]]}
        check("created-in-nonlexical-order")
        ok("BEGIN;")
        for name in names:
            ok(f"UPDATE {name} SET body='pending',v='[0,0]' WHERE id=0;")
            ok(f"DELETE FROM {name} WHERE id=18956;")
            ok(f"INSERT INTO {name} VALUES(26218,'new','[9,1]');")
            assert ok(f"SELECT body FROM {name} WHERE id=0;")["rows"] == [["pending"]]
        ok("ROLLBACK;")
        check("multi-table-rollback")
        ok("BEGIN;")
        for name in names:
            ok(f"UPDATE {name} SET body='commit',v='[0,0]' WHERE id=0;")
            ok(f"DELETE FROM {name} WHERE id=18956;")
            ok(f"INSERT INTO {name} VALUES(26218,'new','[9,1]');")
            model[name] = {0: ["commit", [0, 0]], 26218: ["new", [9, 1]]}
        ok("COMMIT;")
        session.crash()
        session.start()
        check("committed-kill-recovery")
        for _ in range(2):
            session.shutdown()
            session.start()
            check("repeated-restart")
        # Roll back a new table, reuse its name and pending slots, then commit.
        extra = "rolled_" + "r" * 300
        ok("BEGIN;")
        ok(f"CREATE TABLE {extra}(id BIGINT PRIMARY KEY);")
        ok(f"INSERT INTO {extra} VALUES(1);")
        ok("ROLLBACK;")
        ok(f"CREATE TABLE {extra}(id BIGINT PRIMARY KEY);")
        ok(f"INSERT INTO {extra} VALUES(2);")
        session.crash()
        session.start()
        assert ok(f"SELECT id FROM {extra};")["rows"] == [[2]]
        check("rollback-recreate-and-slot-reuse")
        if args.binary:
            ok("CREATE TABLE scratch_probe(id BIGINT PRIMARY KEY,body VARCHAR(4096));")
            ok(f"INSERT INTO scratch_probe VALUES(1,'{'x'*4096}');")
            session.shutdown()
            session.command = ["env", "MVDB_FAILPOINT=sort_after_key_write", *command]
            session.start()
            session.request("SELECT id FROM scratch_probe ORDER BY body LIMIT 1;", wait=False)
            assert session.proc.wait(timeout=config["request_timeout"]) == 86
            scratch = Path(session.temp.name) / "query_order.tmp"
            assert scratch.exists() and scratch.stat().st_size == 4096
            session.stop()
            session.command = command
            session.start()
            assert not scratch.exists()
            assert ok("SELECT id FROM scratch_probe ORDER BY body LIMIT 1;")["rows"] == [[1]]
            assert not scratch.exists()
            report["sort_scratch_crash_recovery"] = True
        session.shutdown()
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        session.close()
    if report["passed"] and args.previous_binary:
        previous = Session(config, args.output / "upgrade.log",
                           command=[str(args.previous_binary), "--data-dir", "{data_dir}"])
        upgrade = {"previous_binary_sha256": hashlib.sha256(args.previous_binary.read_bytes()).hexdigest()}
        try:
            previous.start()
            for sql in [
                "CREATE TABLE legacy_short(id BIGINT PRIMARY KEY,v VECTOR(2));",
                "INSERT INTO legacy_short VALUES(3,'[0,1]'),(7,'[1,0]');",
                "CREATE VECTOR INDEX legacy_vi ON legacy_short(v);",
            ]:
                assert previous.request(sql)["ok"]
            name = "legacy_" + "l" * 260
            try:
                previous.request(f"CREATE TABLE {name}(id BIGINT PRIMARY KEY);")
                raise AssertionError("previous implementation unexpectedly accepted the long filename")
            except RuntimeError as exc:
                upgrade["previous_error"] = str(exc)
            previous.stop()
            previous.command = command
            previous.start()
            assert previous.request("SELECT id FROM legacy_short ORDER BY id;")["rows"] == [[3], [7]]
            assert previous.request("SELECT id FROM legacy_short ORDER BY L2_DISTANCE(v,'[1,0]') LIMIT 1;")["rows"] == [[7]]
            assert previous.request(f"SELECT id FROM {name};")["rows"] == []
            assert previous.request(f"INSERT INTO {name} VALUES(7);")["ok"]
            previous.crash()
            previous.start()
            assert previous.request(f"SELECT id FROM {name};")["rows"] == [[7]]
            previous.shutdown()
            upgrade["same_directory_recovery_passed"] = True
        except Exception:
            report["passed"] = False
            upgrade["error"] = traceback.format_exc()
        finally:
            previous.close()
        report["old_failed_directory_upgrade"] = upgrade
    report["table_name_lengths"] = [len(name) for name in names]
    report["seconds"] = time.monotonic() - started
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
