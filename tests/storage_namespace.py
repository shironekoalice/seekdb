#!/usr/bin/env python3
"""Independent SQL regression for internal scratch/table filename collisions."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "protocol_check"))
from _transport import Session


def colliding_keys(count):
    """Generate inputs for the documented map; SQL expectations use a dict."""
    mask = (1 << 64) - 1
    result = []
    key = 0
    while len(result) < count:
        x = key
        x = ((x ^ (x >> 30)) * 0xbf58476d1ce4e5b9) & mask
        x = ((x ^ (x >> 27)) * 0x94d049bb133111eb) & mask
        if (x ^ (x >> 31)) % 8192 == 0:
            result.append(key)
        key += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--image")
    target.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / "protocol_check/config.json").read_text())
    keys = colliding_keys(4)
    report = {"keys": keys, "config": config, "events": [], "passed": False,
              "scope": "Known local counterexample; not proof of the hidden formal failing input."}
    command = [str(args.binary), "--data-dir", "{data_dir}"] if args.binary else None
    session = Session(config, args.output / "database.stderr.log", image=args.image, command=command)
    if args.image:
        report["image_id"] = subprocess.check_output(
            ["docker", "image", "inspect", "-f", "{{.Id}}", args.image], text=True).strip()
    else:
        report["binary_sha256"] = hashlib.sha256(args.binary.read_bytes()).hexdigest()

    def persist():
        (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def call(phase, function, sql=None):
        before = time.monotonic()
        row = {"phase": phase, "sql": sql}
        report["events"].append(row)
        persist()
        try:
            result = function()
            row["status"] = "PASS"
            if isinstance(result, dict):
                row["response"] = result
            return result
        except Exception as exc:
            row.update(status="FAIL", exception=type(exc).__name__, message=str(exc))
            raise
        finally:
            row["elapsed_seconds"] = time.monotonic() - before
            persist()

    def sql(text, expected=None, error=False):
        response = call("execute", lambda: session.request(text), text)
        assert response["ok"] is (not error), response
        if expected is not None:
            assert response["rows"] == expected, response
        return response

    def check(name, model):
        expected = [[key, value[0]] for key, value in sorted(model.items())]
        sql(f"SELECT id,body FROM {name} ORDER BY id;", expected)
        if name != "ordinary_control":
            ranked = sorted(model, key=lambda key: (sum(v * v for v in model[key][1]), key))
            sql(f"SELECT id FROM {name} ORDER BY L2_DISTANCE(v,'[0,0]') LIMIT 10;",
                [[key] for key in ranked])

    try:
        call("start_without_input", session.start)
        models = {}
        for name in ("ordinary_control", "statement", "transaction", "journal", "lock", "statement_keys"):
            sql(f"CREATE TABLE {name}(id BIGINT PRIMARY KEY,body VARCHAR(40),v VECTOR(2));")
            model = {}
            for number, key in enumerate(keys[:3]):
                sql(f"INSERT INTO {name} VALUES({key},'row{number}','[{number},1]');")
                model[key] = ("row" + str(number), [number, 1])
                if name == "statement" and number == 1:
                    if args.image:
                        physical = "/data/table-1.pk"
                        present = subprocess.run(
                            ["docker", "exec", session.name, "test", "-f", physical],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
                        if present.returncode:
                            physical = "/data/statement.pk"
                        raw = subprocess.check_output(
                            ["docker", "exec", session.name, "cat", physical], timeout=10)
                    else:
                        physical = Path(session.temp.name) / "table-1.pk"
                        if not physical.exists():
                            physical = Path(session.temp.name) / "statement.pk"
                        raw = physical.read_bytes()
                    report["pk_diagnostic_path"] = str(physical)
                    nodes = []
                    for offset in range(65536, len(raw) - 23, 24):
                        key_at_node, value, next_at_node = struct.unpack_from("<qQQ", raw, offset)
                        nodes.append({"offset": offset, "key": key_at_node, "value": value,
                                      "next": next_at_node, "self_cycle": next_at_node == offset})
                    report["statement_pk_after_second_insert"] = nodes
                    (args.output / "statement-after-two-inserts.pk").write_bytes(raw)
                    persist()
            check(name, model)
            sql(f"CREATE VECTOR INDEX vi_{name} ON {name}(v);")
            sql(f"UPDATE {name} SET body='updated',v='[3,1]' WHERE id={keys[1]};")
            model[keys[1]] = ("updated", [3, 1])
            sql(f"DELETE FROM {name} WHERE id={keys[0]};")
            del model[keys[0]]
            sql("BEGIN;")
            sql(f"INSERT INTO {name} VALUES({keys[0]},'rolled','[9,9]');")
            sql(f"UPDATE {name} SET body='rolled' WHERE id={keys[2]};")
            sql("ROLLBACK;")
            check(name, model)
            sql("BEGIN;")
            sql(f"INSERT INTO {name} VALUES({keys[0]},'committed','[0,0]');")
            sql(f"INSERT INTO {name} VALUES({keys[3]},'must_not_appear','[8,8]'),"
                f"({keys[1]},'duplicate','[8,8]');", error=True)
            sql("COMMIT;")
            model[keys[0]] = ("committed", [0, 0])
            models[name] = model
            check(name, model)
        # Every INSERT uses scratch state; confirm it did not damage another table.
        for name, model in models.items():
            check(name, model)
        call("shutdown", session.shutdown)
        call("restart", session.start)
        for name, model in models.items():
            check(name, model)
        sql("BEGIN;")
        for name in models:
            sql(f"DELETE FROM {name};")
        call("kill_uncommitted_transaction", session.crash)
        call("recover", session.start)
        for name, model in models.items():
            check(name, model)
        call("final_shutdown", session.shutdown)
        report["tables_verified"] = len(models)
        report["passed"] = True
    except Exception as exc:
        report["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        session.close()
        report["test_tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        persist()
    print(json.dumps({k: v for k, v in report.items() if k != "events"}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
