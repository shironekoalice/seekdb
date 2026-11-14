from pathlib import Path
import json
import subprocess
import sys
import time

RAW = Path(__file__).resolve().parent
sys.path.insert(0, "/tmp/mvdb-f02-clean-source-20261114/protocol_check")
from _transport import Session

CONFIG = json.loads((Path(sys.path[0]) / "config.json").read_text())
SQL = [
    "CREATE TABLE statement(id BIGINT PRIMARY KEY,body VARCHAR(8));",
    "INSERT INTO statement VALUES(0,'a');",
    "INSERT INTO statement VALUES(18956,'b');",
    "INSERT INTO statement VALUES(26218,'c');",
]
OLD = "minivectordb:f02-clean-3f784f1"
NEW = "minivectordb:f02-namespace-fix"
report = {"config": CONFIG, "minimal_sql": SQL, "events": [],
          "formal_input_known": False, "official_submission_performed": False}


def save():
    (RAW / "minimized-repro.json").write_text(json.dumps(report, indent=2) + "\n")


def call(label, action):
    row = {"phase": label}
    report["events"].append(row)
    save()
    before = time.monotonic()
    try:
        response = action()
        row["response"] = response
        row["status"] = "PASS"
        return response
    except Exception as exc:
        row.update(status="FAIL", exception=type(exc).__name__, message=str(exc))
        raise
    finally:
        row["seconds"] = time.monotonic() - before
        save()


def execute(session, text, label):
    result = call(label, lambda: session.request(text))
    assert result["ok"] is True, result
    return result


session = Session(CONFIG, RAW / "minimized-old-and-upgrade.stderr.log", image=OLD)
try:
    call("old.start", session.start)
    for index, text in enumerate(SQL):
        try:
            execute(session, text, f"old.sql.{index + 1}")
        except TimeoutError:
            assert index == 3
            report["minimal_timeout_reproduced"] = True
            break
    else:
        raise AssertionError("Old submitted image did not reproduce the expected timeout")
    call("old.kill", session.crash)
    session.image = NEW
    call("fixed.open_same_old_data", session.start)
    rows = execute(session, "SELECT id,body FROM statement ORDER BY id;", "fixed.old_data")
    assert rows["rows"] == [[0, "a"], [18956, "b"]], rows
    execute(session, SQL[3], "fixed.third_insert_on_old_data")
    rows = execute(session, "SELECT id,body FROM statement ORDER BY id;", "fixed.old_data_plus_third")
    assert rows["rows"] == [[0, "a"], [18956, "b"], [26218, "c"]], rows
    call("fixed.upgraded_shutdown", session.shutdown)
    report["same_old_data_recovery_passed"] = True
finally:
    session.close()

session = Session(CONFIG, RAW / "minimized-fixed-fresh.stderr.log", image=NEW)
try:
    call("fixed.fresh_start", session.start)
    for index, text in enumerate(SQL):
        execute(session, text, f"fixed.fresh_sql.{index + 1}")
    rows = execute(session, "SELECT id,body FROM statement ORDER BY id;", "fixed.fresh_rows")
    assert rows["rows"] == [[0, "a"], [18956, "b"], [26218, "c"]], rows
    call("fixed.fresh_shutdown", session.shutdown)
    report["fixed_minimal_sequence_passed"] = True
finally:
    session.close()
report["image_ids"] = {
    image: subprocess.check_output(["docker", "image", "inspect", "-f", "{{.Id}}", image],
                                   text=True).strip() for image in (OLD, NEW)}
save()
print(json.dumps(report, indent=2))
