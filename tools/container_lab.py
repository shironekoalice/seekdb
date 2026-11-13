"""Local cgroup-v2 measurements. Run on the Linux host of the Docker daemon."""
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"protocol_check"))
from _transport import Session


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


class Lab:
    """Keep a small supervisor alive so post-shutdown writeback remains measured."""
    def __init__(self, image, output, memory="2g", cache_bytes=None):
        self.image, self.output = image, Path(output)
        self.name = "mvdb-lab-"+uuid.uuid4().hex
        self.volume = self.name+"-data"
        self.memory, self.cache_bytes = memory, cache_bytes
        self.session = None

    def __enter__(self):
        docker("volume", "create", self.volume)
        try:
            docker("run", "-d", "--name", self.name, "--network=none", "--cpus=2",
                   "--memory="+self.memory, "--memory-swap="+self.memory, "--pids-limit=128",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges", "--read-only",
                   "--mount", f"type=volume,src={self.volume},dst=/data",
                   "--entrypoint", "/usr/bin/sleep", self.image, "infinity")
            pid = docker("inspect", "-f", "{{.State.Pid}}", self.name)
            lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
            relative = next(line[3:] for line in lines if line.startswith("0::"))
            self.cgroup = Path("/sys/fs/cgroup")/relative.lstrip("/")
            self.io_start = self.io()
            self.swap_start = self.swap_io()
            self.start()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def start(self):
        config = json.loads((ROOT/"protocol_check/config.json").read_text())
        config["case_timeout"] = 3600
        command = ["docker", "exec", "-i"]
        if self.cache_bytes is not None:
            command += ["-e", f"MVDB_CACHE_BYTES={self.cache_bytes}"]
        command += [self.name, "/app/minivectordb", "--data-dir", "/data"]
        self.session = Session(config, self.output, command=command)
        self.session.start()

    def stop(self):
        if self.session is not None:
            self.session.shutdown()
            self.session.close()
            self.session = None

    def sql(self, sql, mode="exact"):
        result = self.session.request(sql, mode)
        if result["ok"] is not True:
            raise AssertionError((sql[:200], result))
        return result

    def io(self):
        text = (self.cgroup/"io.stat").read_text()
        result = {}
        for line in text.splitlines():
            device, *fields = line.split()
            result[device] = {k: int(v) for k, v in (field.split("=") for field in fields)}
        return result

    @staticmethod
    def swap_io():
        return {key: int(value) for key, value in
                (line.split() for line in Path("/proc/vmstat").read_text().splitlines())
                if key in ("pswpin", "pswpout")}

    def metrics(self):
        # All data files, including derived indexes and temp files, are flushed
        # inside the still-alive cgroup. Supervisor/sync overhead is disclosed.
        docker("exec", self.name, "sync", "-f", "/data")
        current = self.io()
        delta = sum(values.get(k, 0)-self.io_start.get(dev, {}).get(k, 0)
                    for dev, values in current.items() for k in ("rbytes", "wbytes"))
        events = dict(line.split() for line in (self.cgroup/"memory.events").read_text().splitlines())
        assert int(events.get("oom_kill", 0)) == 0, events
        return {"B_bytes": delta,
                "M_bytes": int((self.cgroup/"memory.peak").read_text()),
                "io_start": self.io_start, "io_end": current, "memory_events": events,
                "memory_limit_bytes": int((self.cgroup/"memory.max").read_text()),
                "memory_swap_max": ((self.cgroup/"memory.swap.max").read_text().strip()
                                    if (self.cgroup/"memory.swap.max").exists() else "unavailable"),
                "host_swap_io_delta": {k: v-self.swap_start[k] for k, v in self.swap_io().items()}}

    def __exit__(self, *exc):
        if self.session:
            self.session.close()
            self.session = None
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", self.volume], capture_output=True)
