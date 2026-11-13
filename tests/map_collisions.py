#!/usr/bin/env python3
"""SQL state oracle with deliberately long disk-map collision chains."""
import argparse
import copy
import json
from pathlib import Path
import traceback

from contract import Checks


def colliding_keys(count):
    # This models only input placement, never expected database state.
    mask = (1 << 64) - 1
    keys = []
    for key in range(1000000):
        value = key
        value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
        value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
        value ^= value >> 31
        if value % 8192 == 0:
            keys.append(key)
            if len(keys) == count:
                return keys
    raise AssertionError("insufficient collision keys")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path)
    target.add_argument("--image")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.binary:
        args.binary = args.binary.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    checks = Checks(args)
    keys = colliding_keys(24)
    model = {key: i for i, key in enumerate(keys)}
    report = {"passed": False, "scope": "local independent SQL state with adversarial hash placement",
              "keys": keys, "phases": []}

    def verify(session, name):
        actual = checks.ok(session, "SELECT id,n FROM chain ORDER BY id;")["rows"]
        assert actual == [[key, value] for key, value in sorted(model.items())]
        # Flat-index version checks traverse chains independently of row scans.
        actual = checks.ok(session, "SELECT id FROM chain ORDER BY L2_DISTANCE(v,'[0]') LIMIT 100;")["rows"]
        assert actual == [[key] for key in sorted(model, key=lambda k: (abs(model[k]), k))]
        for key in model:
            checks.bad(session, f"INSERT INTO chain VALUES ({key},999,'[999]');")
        report["phases"].append({"name": name, "rows": len(model), "passed": True})

    try:
        with checks.session() as s:
            checks.ok(s, "CREATE TABLE chain(id BIGINT PRIMARY KEY,n BIGINT,v VECTOR(1));")
            checks.ok(s, "CREATE VECTOR INDEX chain_vi ON chain(v);")
            values = ",".join(f"({k},{v},'[{v}]')" for k, v in model.items())
            checks.ok(s, "INSERT INTO chain VALUES " + values + ";")
            verify(s, "same-bucket-batch")
            saved = copy.deepcopy(model)
            checks.ok(s, "BEGIN;")
            for i, key in enumerate(keys):
                if i % 3 == 0:
                    checks.ok(s, f"DELETE FROM chain WHERE id={key};")
                    del model[key]
                else:
                    checks.ok(s, f"UPDATE chain SET n={-i},v='[{-i}]' WHERE id={key};")
                    model[key] = -i
            for i, key in enumerate(keys[::3]):
                checks.ok(s, f"INSERT INTO chain VALUES ({key},{100+i},'[{100+i}]');")
                model[key] = 100+i
            # Failing a batch must not leak its first row into the pending map.
            checks.bad(s, f"INSERT INTO chain VALUES (-1,1,'[1]'),({keys[-1]},1,'[1]');")
            verify(s, "pending-update-delete-reinsert-failed-batch")
            checks.ok(s, "ROLLBACK;")
            model = saved
            verify(s, "rollback")
            checks.ok(s, "BEGIN;")
            for key in keys[1::2]:
                checks.ok(s, f"DELETE FROM chain WHERE id={key};")
                del model[key]
            checks.ok(s, "COMMIT;")
            verify(s, "committed-tombstones")
            s.crash()
            s.start()
            verify(s, "crash-recovery")
            s.shutdown()
            s.start()
            verify(s, "repeated-recovery")
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    (args.output/"result.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
