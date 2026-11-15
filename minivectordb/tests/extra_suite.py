#!/usr/bin/env python3
"""按赛题规格自建的「影子正式用例」：覆盖公开题包没测到的边界。"""
import json, os, subprocess, sys

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
DATA = "/tmp/mvdb-extra"

class Db:
    def __init__(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1,
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
                                  stderr=subprocess.PIPE, text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        self.p.stdout.readline()
    def close(self):
        self.p.stdin.write(json.dumps({"id": 1 << 30, "op": "shutdown"}) + "\n"); self.p.stdin.flush()
        self.p.stdout.readline(); self.p.wait()

CASES = []
def case(name):
    def deco(fn):
        CASES.append((name, fn)); return fn
    return deco

def ok(r):        return r.get("ok") is True
def err(r, code): return r.get("ok") is False and r.get("error", {}).get("code") == code
def rows(r):      return r.get("rows")
def affected(r):  return r.get("affected_rows")

@case("bigint 边界值")
def _(db):
    db.q("CREATE TABLE b (id BIGINT PRIMARY KEY, v BIGINT);")
    assert ok(db.q("INSERT INTO b VALUES (9223372036854775807, -9223372036854775808);"))
    r = db.q("SELECT id, v FROM b;")
    assert rows(r) == [[9223372036854775807, -9223372036854775808]], rows(r)

@case("bigint 溢出报错")
def _(db):
    db.q("CREATE TABLE b2 (id BIGINT PRIMARY KEY);")
    r = db.q("INSERT INTO b2 VALUES (9223372036854775808);")
    assert not ok(r), r

@case("varchar 字符数按 Unicode 计")
def _(db):
    db.q("CREATE TABLE v (id BIGINT PRIMARY KEY, s VARCHAR(3));")
    assert ok(db.q("INSERT INTO v VALUES (1,'中文啊');")), "3 个中文字符应允许"
    r = db.q("INSERT INTO v VALUES (2,'四个字符');")
    assert not ok(r), "4 个字符应超长"

@case("字符串比较按码点序")
def _(db):
    db.q("CREATE TABLE s (id BIGINT PRIMARY KEY, t VARCHAR(4));")
    db.q("INSERT INTO s VALUES (1,'b'),(2,'a'),(3,'B');")
    r = db.q("SELECT id FROM s ORDER BY t;")
    assert rows(r) == [[3], [2], [1]], rows(r)   # 'B'(66) < 'a'(97) < 'b'(98)

@case("空结果仍返回列名")
def _(db):
    db.q("CREATE TABLE e (id BIGINT PRIMARY KEY);")
    r = db.q("SELECT id FROM e;")
    assert r.get("columns") == ["id"] and rows(r) == [], r

@case("limit 0 与 limit 超行数")
def _(db):
    db.q("CREATE TABLE l (id BIGINT PRIMARY KEY);")
    db.q("INSERT INTO l VALUES (1),(2),(3);")
    assert rows(db.q("SELECT id FROM l ORDER BY id LIMIT 0;")) == []
    assert rows(db.q("SELECT id FROM l ORDER BY id LIMIT 99;")) == [[1], [2], [3]]

@case("order by desc 并列按主键升序")
def _(db):
    db.q("CREATE TABLE o (id BIGINT PRIMARY KEY, v BIGINT);")
    db.q("INSERT INTO o VALUES (3,7),(1,7),(2,9);")
    assert rows(db.q("SELECT id FROM o ORDER BY v DESC;")) == [[2], [1], [3]], rows(db.q("SELECT id FROM o ORDER BY v DESC;"))

@case("vector 维度不符")
def _(db):
    db.q("CREATE TABLE vd (id BIGINT PRIMARY KEY, e VECTOR(3));")
    r = db.q("INSERT INTO vd VALUES (1,'[1,2]');")
    assert err(r, "dimension_mismatch"), r

@case("vector NaN/Inf 拒绝")
def _(db):
    db.q("CREATE TABLE vn (id BIGINT PRIMARY KEY, e VECTOR(2));")
    for bad in ["'[NaN,1]'", "'[1,Infinity]'", "'[1e400,1]'"]:
        r = db.q(f"INSERT INTO vn VALUES (1,{bad});")
        assert not ok(r), (bad, r)

@case("过滤后 TopK（不能先取 K 再过滤）")
def _(db):
    db.q("CREATE TABLE f (id BIGINT PRIMARY KEY, c VARCHAR(4), e VECTOR(2));")
    db.q("INSERT INTO f VALUES (1,'x','[0,0]'),(2,'y','[1,0]'),(3,'x','[9,9]');")
    r = db.q("SELECT id FROM f WHERE c = 'x' ORDER BY L2_DISTANCE(e,'[0,0]') LIMIT 2;")
    assert rows(r) == [[1], [3]], rows(r)

@case("未提交事务不跨重启")
def _(db):
    db.q("CREATE TABLE r (id BIGINT PRIMARY KEY);")
    db.q("INSERT INTO r VALUES (1);")
    db.q("BEGIN;")
    db.q("INSERT INTO r VALUES (2);")
    db.restart()
    assert rows(db.q("SELECT id FROM r ORDER BY id;")) == [[1]]

@case("事务内失败语句不影响事务继续")
def _(db):
    db.q("CREATE TABLE tf (id BIGINT PRIMARY KEY);")
    db.q("INSERT INTO tf VALUES (1);")
    db.q("BEGIN;")
    r = db.q("INSERT INTO tf VALUES (2),(1);")
    assert err(r, "duplicate_key"), r
    assert ok(db.q("INSERT INTO tf VALUES (3);"))
    assert ok(db.q("COMMIT;"))
    assert rows(db.q("SELECT id FROM tf ORDER BY id;")) == [[1], [3]]

@case("update 无匹配返回 0")
def _(db):
    db.q("CREATE TABLE u (id BIGINT PRIMARY KEY, v VARCHAR(4));")
    db.q("INSERT INTO u VALUES (1,'a');")
    assert affected(db.q("UPDATE u SET v='b' WHERE id = 99;")) == 0

@case("delete 无匹配返回 0")
def _(db):
    db.q("CREATE TABLE dl (id BIGINT PRIMARY KEY);")
    db.q("INSERT INTO dl VALUES (1);")
    assert affected(db.q("DELETE FROM dl WHERE id = 99;")) == 0

@case("多表隔离")
def _(db):
    db.q("CREATE TABLE m1 (id BIGINT PRIMARY KEY);")
    db.q("CREATE TABLE m2 (id BIGINT PRIMARY KEY);")
    db.q("INSERT INTO m1 VALUES (1);")
    db.q("INSERT INTO m2 VALUES (2);")
    assert rows(db.q("SELECT id FROM m1;")) == [[1]]
    assert rows(db.q("SELECT id FROM m2;")) == [[2]]

@case("嵌套事务报错")
def _(db):
    db.q("BEGIN;")
    assert not ok(db.q("BEGIN;"))
    db.q("ROLLBACK;")

@case("表不存在/列不存在错误")
def _(db):
    assert not ok(db.q("SELECT id FROM nope;"))
    db.q("CREATE TABLE cn (id BIGINT PRIMARY KEY);")
    assert not ok(db.q("SELECT missing FROM cn;"))

@case("重建索引后重启仍可用")
def _(db):
    db.q("CREATE TABLE ix (id BIGINT PRIMARY KEY, e VECTOR(2));")
    db.q("INSERT INTO ix VALUES (1,'[0,0]'),(2,'[5,5]');")
    db.q("CREATE VECTOR INDEX ixv ON ix(e);")
    db.restart()
    r = db.q("SELECT id, L2_DISTANCE(e,'[0,0]') AS d FROM ix ORDER BY d LIMIT 1;", "ann")
    assert rows(r) == [[1, 0.0]], r

def main():
    failures = 0
    for name, fn in CASES:
        subprocess.run(["rm", "-rf", DATA])
        db = Db()
        try:
            fn(db)
            print(f"PASS  {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {name}: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass
    print(f"\n{len(CASES) - failures}/{len(CASES)} passed")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
