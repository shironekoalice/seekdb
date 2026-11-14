#!/usr/bin/env python3
"""Audit a built image's real entrypoint, pipes, mounts, users and restart."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "protocol_check"))
from _transport import ProtocolError, Session


def docker(*args, check=True):
    return subprocess.run(["docker", *args], check=check, capture_output=True, timeout=30)


def fragmented_send(session, request):
    """Split UTF-8 and JSON tokens across pipe writes; use CRLF framing."""
    assert not session.buffer and not session.selector.select(0)
    data = (json.dumps(request, ensure_ascii=False, allow_nan=False) + "\r\n").encode()
    deadline = time.monotonic() + session.remaining(session.config["request_timeout"])
    position = 0
    with selectors.DefaultSelector() as writer:
        writer.register(session.proc.stdin, selectors.EVENT_WRITE)
        while position < len(data):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not writer.select(remaining):
                raise TimeoutError("fragmented request write timeout")
            length = 1 if position < 7 else 4093
            position += os.write(session.proc.stdin.fileno(), data[position:position + length])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / "protocol_check/config.json").read_text())
    report = {"image": args.image, "config": config, "checks": [], "events": [],
              "scope": "Local environment diagnosis; formal host and hidden SQL unknown."}

    def event(phase, function):
        before = time.monotonic()
        row = {"phase": phase}
        try:
            result = function()
            row["status"] = "PASS"
            return result
        except Exception as exc:
            row.update(status="FAIL", exception=type(exc).__name__, message=str(exc))
            raise
        finally:
            row["elapsed_seconds"] = time.monotonic() - before
            report["events"].append(row)
            (args.output / "events.json").write_text(json.dumps(report["events"], indent=2) + "\n")

    def sql(session, text, phase, expected=None):
        response = event(phase, lambda: session.request(text))
        assert response["ok"] is True, response
        if expected is not None:
            assert response.get("rows") == expected, response
        return response

    try:
        image = json.loads(docker("image", "inspect", args.image).stdout)[0]
        report["image_metadata"] = {
            "id": image["Id"], "architecture": image["Architecture"], "os": image["Os"],
            "entrypoint": image["Config"]["Entrypoint"], "cmd": image["Config"]["Cmd"],
            "user": image["Config"]["User"], "working_dir": image["Config"]["WorkingDir"]}
        assert image["Architecture"] == "amd64" and image["Os"] == "linux"
        binary = docker("run", "--rm", "--network=none", "--read-only",
                        "--entrypoint", "/bin/cat", args.image, "/app/minivectordb").stdout
        assert binary[:4] == b"\x7fELF" and struct.unpack("<H", binary[18:20])[0] == 62
        report["binary_sha256"] = hashlib.sha256(binary).hexdigest()
        libraries = docker("run", "--rm", "--network=none", "--read-only",
                           "--entrypoint", "/usr/bin/ldd", args.image, "/app/minivectordb")
        assert b"not found" not in libraries.stdout
        (args.output / "runtime-ldd.txt").write_bytes(libraries.stdout + libraries.stderr)
        report["checks"].append("linux_amd64_elf_and_runtime_libraries")

        session = Session(config, args.output / "default.stderr.log", image=args.image)
        try:
            event("default.start_without_any_input", session.start)
            inspection = json.loads(docker("inspect", session.name).stdout)[0]
            host = inspection["HostConfig"]
            report["actual_container"] = {
                "image_id": inspection["Image"], "path": inspection["Path"],
                "args": inspection["Args"], "readonly_root": host["ReadonlyRootfs"],
                "network_mode": host["NetworkMode"], "nano_cpus": host["NanoCpus"],
                "memory": host["Memory"], "pids_limit": host["PidsLimit"],
                "cap_drop": host["CapDrop"], "security_opt": host["SecurityOpt"],
                "open_stdin": inspection["Config"]["OpenStdin"],
                "tty": inspection["Config"]["Tty"],
                "mounts": [{k: m[k] for k in ("Type", "Destination", "RW")} for m in inspection["Mounts"]]}
            assert host["ReadonlyRootfs"] and host["NetworkMode"] == "none"
            assert host["Memory"] == 2 * 1024 ** 3 and host["NanoCpus"] == 2 * 10 ** 9
            assert host["PidsLimit"] == 128 and not inspection["Config"]["Tty"]
            assert inspection["Config"]["OpenStdin"]
            sql(session, "CREATE TABLE docs(id BIGINT PRIMARY KEY,body VARCHAR(200000),v VECTOR(2));",
                "default.create")
            body = "记忆🙂;\n" * 16384
            original_send = session.send
            session.send = lambda request: fragmented_send(session, request)
            try:
                sql(session, f"INSERT INTO docs VALUES(1,'{body}','[1,0]');",
                    "default.fragmented_large_utf8_crlf_request")
            finally:
                session.send = original_send
            sql(session, "SELECT id,body FROM docs WHERE id=1;",
                "default.response_larger_than_pipe_buffer", [[1, body]])
            sql(session, "CREATE VECTOR INDEX vi ON docs(v);", "default.index")
            sql(session, "BEGIN;", "default.begin_rollback")
            sql(session, "UPDATE docs SET body='rolled',v='[0,1]' WHERE id=1;", "default.update_rollback")
            sql(session, "ROLLBACK;", "default.rollback")
            sql(session, "SELECT id,body FROM docs WHERE id=1;", "default.rollback_visibility", [[1, body]])
            sql(session, "BEGIN;", "default.begin_commit")
            sql(session, "UPDATE docs SET body='committed',v='[0,1]' WHERE id=1;", "default.update_commit")
            sql(session, "COMMIT;", "default.commit")
            event("default.shutdown", session.shutdown)
            event("default.restart_without_input", session.start)
            sql(session, "SELECT id,body FROM docs ORDER BY id;", "default.recovered_data", [[1, "committed"]])
            sql(session, "SELECT id,L2_DISTANCE(v,'[0,1]') AS d FROM docs ORDER BY d LIMIT 1;",
                "default.recovered_vector", [[1, 0.0]])
            sql(session, "BEGIN;", "default.begin_before_kill")
            sql(session, "UPDATE docs SET body='uncommitted' WHERE id=1;", "default.update_before_kill")
            event("default.kill", session.crash)
            event("default.crash_recovery_without_input", session.start)
            sql(session, "SELECT id,body FROM docs ORDER BY id;", "default.crash_recovery_data", [[1, "committed"]])
            event("default.final_shutdown", session.shutdown)
        finally:
            session.close()
        report["checks"].append("default_entrypoint_fragmented_pipes_transactions_and_recovery")

        for label, uid, writable in (
                ("bind_root", None, True),
                ("bind_nobody_writable", "65534:65534", True),
                ("bind_nobody_denied_negative_control", "65534:65534", False)):
            with tempfile.TemporaryDirectory(prefix="mvdb-env-data-") as directory:
                Path(directory).chmod(0o777 if writable else 0o555)
                name = "mvdb-env-" + uuid.uuid4().hex
                argv = ["docker", "run", "--rm", "-i", "--name", name,
                        "--network=none", "--read-only", "--cpus=2", "--memory=2g",
                        "--memory-swap=2g", "--pids-limit=128", "--cap-drop=ALL",
                        "--security-opt=no-new-privileges", "--mount", f"type=bind,src={directory},dst=/data"]
                if uid:
                    argv += ["--user", uid]
                argv.append(args.image)
                session = Session(config, args.output / (label + ".stderr.log"), command=argv)
                try:
                    if writable:
                        event(label + ".start", session.start)
                        sql(session, "CREATE TABLE t(id BIGINT PRIMARY KEY);", label + ".write")
                        sql(session, "INSERT INTO t VALUES(7);", label + ".insert")
                        sql(session, "SELECT id FROM t;", label + ".read", [[7]])
                        if uid:
                            assert (Path(directory) / "journal.mvdb").stat().st_uid == 65534
                        event(label + ".shutdown", session.shutdown)
                    else:
                        before = time.monotonic()
                        try:
                            session.start()
                        except ProtocolError as exc:
                            assert "exited before response" in str(exc), str(exc)
                            report["permission_negative_control"] = {
                                "observed": str(exc), "elapsed_seconds": time.monotonic() - before,
                                "timed_out": False}
                        else:
                            raise AssertionError("Unwritable data directory unexpectedly started")
                finally:
                    session.close()
                    docker("rm", "-f", name, check=False)
                    Path(directory).chmod(0o777)
                report["checks"].append(label)
        report["passed"] = True
    except Exception as exc:
        report.update(passed=False, error=type(exc).__name__ + ": " + str(exc))
    finally:
        report["test_tool_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (args.output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
