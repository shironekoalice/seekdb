#!/usr/bin/env python3
"""交互式 SQL 客户端：把每行 SQL 发成 JSONL 请求，结果按表格打印。

用法:
    python3 tools/cli.py target/release/mvdb [数据目录]
命令:
    :mode exact|ann   切换检索模式
    :restart          正常关闭并重启（复用同一数据目录）
    :quit             退出
"""
import json, os, subprocess, sys

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
DATA = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mvdb-cli"

class Db:
    def __init__(self):
        self.proc = None
        self.seq = 0
        self.mode = "exact"
        self.start()
    def start(self):
        self.proc = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, bufsize=1, env=dict(os.environ, MVDB_DATA=DATA))
        ready = self.proc.stdout.readline().strip()
        print(ready)
    def sql(self, text):
        self.seq += 1
        req = {"id": self.seq, "op": "execute", "sql": text, "search_mode": self.mode}
        self.proc.stdin.write(json.dumps(req) + "\n"); self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())
    def restart(self):
        self.proc.stdin.write(json.dumps({"id": 999999, "op": "shutdown"}) + "\n"); self.proc.stdin.flush()
        self.proc.stdout.readline(); self.proc.wait(); self.start()
    def quit(self):
        self.proc.stdin.write(json.dumps({"id": 999999, "op": "shutdown"}) + "\n"); self.proc.stdin.flush()
        self.proc.stdout.readline(); self.proc.wait()

def show(resp):
    if not resp.get("ok"):
        err = resp.get("error", {})
        print(f"  ERROR [{err.get('code')}] {err.get('message')}")
        return
    if "rows" in resp:
        cols = resp.get("columns", [])
        rows = resp["rows"]
        widths = [len(str(c)) for c in cols]
        for r in rows:
            for i, v in enumerate(r):
                widths[i] = max(widths[i], len("NULL" if v is None else str(v)))
        line = "  | " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)) + " |"
        print(line)
        print("  |" + "|".join("-" * (w + 2) for w in widths) + "|")
        for r in rows:
            print("  | " + " | ".join(("NULL" if v is None else str(v)).ljust(widths[i]) for i, v in enumerate(r)) + " |")
        print(f"  ({len(rows)} rows)")
    else:
        print(f"  OK, affected_rows={resp.get('affected_rows', 0)}")

def main():
    db = Db()
    print(f"MVDB cli | 数据目录 {DATA} | 模式 {db.mode} | 输入 :quit 退出")
    while True:
        try:
            line = input("sql> ").strip()
        except (EOFError, KeyboardInterrupt):
            print(); break
        if not line:
            continue
        if line in (":quit", ":q", "quit", "exit"):
            break
        if line.startswith(":mode"):
            db.mode = line.split()[1]
            print(f"  模式 -> {db.mode}")
            continue
        if line == ":restart":
            db.restart(); print("  已重启（数据保留）"); continue
        show(db.sql(line))
    db.quit()

if __name__ == "__main__":
    main()
