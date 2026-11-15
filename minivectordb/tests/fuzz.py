#!/usr/bin/env python3
"""随机操作 + 模型比对 + 崩溃注入。

维护一个 Python 参考模型，随机执行 INSERT/UPDATE/DELETE/事务/重启/强杀，
每一步都与数据库实际状态比对，用来找语义与持久化漏洞。
"""
import json, os, random, signal, subprocess, sys, time

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 400
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 1
DATA = "/tmp/mvdb-fuzz"

class CrashError(Exception):
    pass

class Db:
    def __init__(self):
        self.p = None
        self.start()
    def start(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=open("/tmp/fuzz-stderr.log","a"), text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        self.i = 0
        assert json.loads(self.p.stdout.readline()) == {"type": "ready"}
    def q(self, sql, mode="exact"):
        self.i += 1
        self.p.stdin.write(json.dumps({"id": self.i, "op": "execute", "sql": sql, "search_mode": mode}) + "\n")
        self.p.stdin.flush()
        line = self.p.stdout.readline()
        if not line:
            raise CrashError("no response")
        return json.loads(line)
    def shutdown(self):
        self.p.stdin.write(json.dumps({"id": 1 << 30, "op": "shutdown"}) + "\n"); self.p.stdin.flush()
        self.p.stdout.readline(); self.p.wait()
    def kill(self):
        os.kill(self.p.pid, signal.SIGKILL); self.p.wait()

def sql_str(s):
    return "'" + s.replace("'", "''") + "'"

def main():
    random.seed(SEED)
    subprocess.run(["rm", "-rf", DATA])
    db = Db()
    model = {}          # id -> (name, score)
    committed = dict(model)
    txn = None          # 事务内的暂存改动
    assert db.q("CREATE TABLE f (id BIGINT PRIMARY KEY, name VARCHAR(8), score BIGINT);").get("ok")

    def state():
        r = db.q("SELECT id, name, score FROM f ORDER BY id;")
        assert r.get("ok"), r
        return {row[0]: (row[1], row[2]) for row in r["rows"]}

    def check(tag):
        got = state()
        want = committed
        if got != want:
            print(f"MISMATCH after {tag}\n  db   ={got}\n  model={want}")
            return False
        return True

    step = -1
    op = "init"
    for step in range(STEPS):
        op = random.choices(
            ["insert", "update", "delete", "query", "txn", "restart", "crash"],
            weights=[38, 20, 12, 12, 8, 6, 4])[0]
        base = model if txn is not None else committed

        if op == "insert":
            pk = random.randint(1, 60)
            row = (f"n{pk % 7}", random.randint(-1000, 1000))
            r = db.q(f"INSERT INTO f VALUES ({pk},{sql_str(row[0])},{row[1]});")
            if pk in base:
                assert not r.get("ok") and r["error"]["code"] == "duplicate_key", r
            else:
                assert r.get("ok") and r.get("affected_rows") == 1, r
                base[pk] = row
                if txn is None:
                    committed[pk] = row

        elif op == "update":
            pk = random.randint(1, 60)
            row = (f"u{pk % 5}", random.randint(-1000, 1000))
            r = db.q(f"UPDATE f SET name={sql_str(row[0])}, score={row[1]} WHERE id={pk};")
            assert r.get("ok"), r
            if pk in base:
                changed = 1 if base[pk] != row else 0
                assert r.get("affected_rows") == changed, (r, base[pk], row)
                base[pk] = row
                if txn is None:
                    committed[pk] = row
            else:
                assert r.get("affected_rows") == 0, r

        elif op == "delete":
            pk = random.randint(1, 60)
            r = db.q(f"DELETE FROM f WHERE id={pk};")
            assert r.get("ok"), r
            if pk in base:
                assert r.get("affected_rows") == 1, r
                del base[pk]
                if txn is None:
                    committed.pop(pk, None)
            else:
                assert r.get("affected_rows") == 0, r

        elif op == "query":
            lim = random.randint(1, 10)
            r = db.q(f"SELECT id, score FROM f WHERE score >= -100 ORDER BY score DESC, id LIMIT {lim};")
            assert r.get("ok"), r
            want = sorted(((pk, v[1]) for pk, v in base.items() if v[1] >= -100),
                          key=lambda t: (-t[1], t[0]))[:lim]
            assert r["rows"] == [list(t) for t in want], (r["rows"], want)

        elif op == "txn":
            assert db.q("BEGIN;").get("ok")
            txn = dict(base)
            for _ in range(random.randint(1, 4)):
                pk = random.randint(1, 60)
                if random.random() < 0.5 and pk not in txn:
                    row = (f"t{pk % 3}", random.randint(-50, 50))
                    r = db.q(f"INSERT INTO f VALUES ({pk},{sql_str(row[0])},{row[1]});")
                    if r.get("ok"):
                        txn[pk] = row
                else:
                    row = (f"t{pk % 3}", random.randint(-50, 50))
                    if db.q(f"UPDATE f SET name={sql_str(row[0])}, score={row[1]} WHERE id={pk};").get("ok"):
                        if pk in txn:
                            txn[pk] = row
            if random.random() < 0.5:
                assert db.q("COMMIT;").get("ok")
                committed = dict(txn)
            else:
                assert db.q("ROLLBACK;").get("ok")
            txn = None
            if not check("txn"):
                return 1

        elif op == "restart":
            db.shutdown()
            db.start()
            if txn is not None:      # 重启回滚未提交事务
                txn = None
            if not check("restart"):
                return 1

        elif op == "crash":
            db.kill()
            db.start()
            if txn is not None:
                txn = None
            if not check("crash"):
                return 1

    if not check("final"):
        return 1
    db.shutdown()
    print(f"OK: {STEPS} steps, {len(committed)} rows, seed={SEED}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        import traceback
        print(f"FAILED at step {locals().get('step')} op={locals().get('op')}: {exc!r}")
        traceback.print_exc(limit=3)
        sys.exit(2)
