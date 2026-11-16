#!/usr/bin/env python3
"""逐条验证题面 §2.2 的 SQL 与向量检索语义。"""
import json, os, subprocess, sys

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
DATA = "/tmp/mvdb-spec"

class Db:
    def __init__(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        self.i = 0
        assert json.loads(self.p.stdout.readline()) == {"type": "ready"}
    def q(self, sql, mode="exact"):
        self.i += 1
        self.p.stdin.write(json.dumps({"id": self.i, "op": "execute", "sql": sql, "search_mode": mode}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())
    def restart(self):
        self.p.stdin.write(json.dumps({"id": 1 << 30, "op": "shutdown"}) + "\n"); self.p.stdin.flush()
        self.p.stdout.readline(); self.p.wait()
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        assert json.loads(self.p.stdout.readline()) == {"type": "ready"}
    def close(self):
        self.p.stdin.write(json.dumps({"id": 1 << 30, "op": "shutdown"}) + "\n"); self.p.stdin.flush()
        self.p.stdout.readline(); self.p.wait()

CASES = []
def case(name):
    def deco(fn):
        CASES.append((name, fn)); return fn
    return deco

def rows(r): return r.get("rows")
def ok(r): return r.get("ok") is True
def err(r, code): return r.get("ok") is False and r["error"]["code"] == code

@case("L2_DISTANCE 返回欧氏距离（非平方）")
def _(db):
    db.q("CREATE TABLE a (id BIGINT PRIMARY KEY, e VECTOR(2));")
    db.q("INSERT INTO a VALUES (1,'[3,4]');")
    r = db.q("SELECT L2_DISTANCE(e,'[0,0]') AS d FROM a;")
    assert rows(r) == [[5.0]], rows(r)

@case("过滤后 TopK 在过滤集合内计算")
def _(db):
    db.q("CREATE TABLE b (id BIGINT PRIMARY KEY, c VARCHAR(4), e VECTOR(1));")
    db.q("INSERT INTO b VALUES (1,'x','[0]'),(2,'y','[1]'),(3,'x','[9]'),(4,'x','[2]');")
    r = db.q("SELECT id FROM b WHERE c='x' ORDER BY L2_DISTANCE(e,'[0]') LIMIT 2;")
    assert rows(r) == [[1], [4]], rows(r)   # 全表前二会是 1,2，过滤后应为 1,4

@case("结果不足 K 条返回全部")
def _(db):
    db.q("CREATE TABLE c (id BIGINT PRIMARY KEY, e VECTOR(1));")
    db.q("INSERT INTO c VALUES (1,'[0]'),(2,'[5]');")
    r = db.q("SELECT id FROM c ORDER BY L2_DISTANCE(e,'[0]') LIMIT 10;")
    assert rows(r) == [[1], [2]], rows(r)

@case("距离并列按主键升序")
def _(db):
    db.q("CREATE TABLE d (id BIGINT PRIMARY KEY, e VECTOR(1));")
    db.q("INSERT INTO d VALUES (8,'[1]'),(3,'[-1]'),(5,'[1]');")
    r = db.q("SELECT id FROM d ORDER BY L2_DISTANCE(e,'[0]') LIMIT 3;")
    assert rows(r) == [[3], [5], [8]], rows(r)

@case("普通 ORDER BY 并列按主键升序")
def _(db):
    db.q("CREATE TABLE e (id BIGINT PRIMARY KEY, s BIGINT);")
    db.q("INSERT INTO e VALUES (9,1),(2,1),(5,1);")
    assert rows(db.q("SELECT id FROM e ORDER BY s;")) == [[2], [5], [9]], rows(db.q("SELECT id FROM e ORDER BY s;"))

@case("建索引后 exact 仍然精确")
def _(db):
    db.q("CREATE TABLE f (id BIGINT PRIMARY KEY, e VECTOR(2));")
    db.q("INSERT INTO f VALUES (1,'[0,0]'),(2,'[3,4]'),(3,'[1,1]');")
    db.q("CREATE VECTOR INDEX fv ON f(e);")
    r = db.q("SELECT id FROM f ORDER BY L2_DISTANCE(e,'[0,0]') LIMIT 2;", "exact")
    assert rows(r) == [[1], [3]], rows(r)

@case("ann 模式：满足过滤、无重复、结果正确")
def _(db):
    db.q("CREATE TABLE g (id BIGINT PRIMARY KEY, c VARCHAR(4), e VECTOR(2));")
    db.q("INSERT INTO g VALUES (1,'x','[0,0]'),(2,'y','[0,0]'),(3,'x','[1,0]'),(4,'x','[5,5]');")
    db.q("CREATE VECTOR INDEX gv ON g(e);")
    for mode in ("exact", "ann"):
        r = db.q("SELECT id FROM g WHERE c='x' ORDER BY L2_DISTANCE(e,'[0,0]') LIMIT 2;", mode)
        ids = [x[0] for x in rows(r)]
        assert ids == [1, 3], (mode, rows(r))
        assert len(ids) == len(set(ids))

@case("索引后增删改反映到检索")
def _(db):
    db.q("CREATE TABLE h (id BIGINT PRIMARY KEY, e VECTOR(1));")
    db.q("INSERT INTO h VALUES (1,'[0]'),(2,'[1]');")
    db.q("CREATE VECTOR INDEX hv ON h(e);")
    db.q("UPDATE h SET e='[100]' WHERE id=1;")
    db.q("DELETE FROM h WHERE id=2;")
    db.q("INSERT INTO h VALUES (3,'[2]');")
    r = db.q("SELECT id FROM h ORDER BY L2_DISTANCE(e,'[0]') LIMIT 3;")
    assert rows(r) == [[3], [1]], rows(r)

@case("ROLLBACK 后向量检索恢复")
def _(db):
    db.q("CREATE TABLE i (id BIGINT PRIMARY KEY, e VECTOR(1));")
    db.q("INSERT INTO i VALUES (1,'[0]'),(2,'[1]');")
    db.q("BEGIN;")
    db.q("UPDATE i SET e='[50]' WHERE id=1;")
    assert rows(db.q("SELECT id FROM i ORDER BY L2_DISTANCE(e,'[0]') LIMIT 1;")) == [[2]]
    db.q("ROLLBACK;")
    assert rows(db.q("SELECT id FROM i ORDER BY L2_DISTANCE(e,'[0]') LIMIT 1;")) == [[1]]

@case("事务内可见自身修改（表数据与向量一致）")
def _(db):
    db.q("CREATE TABLE j (id BIGINT PRIMARY KEY, s BIGINT, e VECTOR(1));")
    db.q("INSERT INTO j VALUES (1,1,'[0]');")
    db.q("BEGIN;")
    db.q("UPDATE j SET s=9, e='[7]' WHERE id=1;")
    assert rows(db.q("SELECT s FROM j WHERE id=1;")) == [[9]]
    assert rows(db.q("SELECT id FROM j ORDER BY L2_DISTANCE(e,'[7]') LIMIT 1;")) == [[1]]
    db.q("COMMIT;")
    db.restart()
    assert rows(db.q("SELECT s FROM j WHERE id=1;")) == [[9]]

@case("重启后索引重建并可用")
def _(db):
    db.q("CREATE TABLE k (id BIGINT PRIMARY KEY, e VECTOR(1));")
    db.q("INSERT INTO k VALUES (1,'[0]'),(2,'[9]');")
    db.q("CREATE VECTOR INDEX kv ON k(e);")
    db.restart()
    r = db.q("SELECT id FROM k ORDER BY L2_DISTANCE(e,'[0]') LIMIT 1;", "ann")
    assert rows(r) == [[1]], rows(r)

def main():
    fail = 0
    for name, fn in CASES:
        subprocess.run(["rm", "-rf", DATA])
        db = Db()
        try:
            fn(db); print(f"PASS  {name}")
        except AssertionError as e:
            fail += 1; print(f"FAIL  {name}: {e}")
        finally:
            try: db.close()
            except Exception: pass
    print(f"\n{len(CASES)-fail}/{len(CASES)} passed")
    return 1 if fail else 0

if __name__ == "__main__":
    sys.exit(main())
