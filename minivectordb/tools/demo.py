#!/usr/bin/env python3
"""交互演示：把 JSONL 请求/响应原样打印出来。"""
import json, os, signal, subprocess, sys, time

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
DATA = "/tmp/mvdb-demo"

class Conn:
    def __init__(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        self.id = 0
        print(f"  ← {self.p.stdout.readline().strip()}")
    def q(self, sql, mode="exact", show=True):
        self.id += 1
        req = {"id": self.id, "op": "execute", "sql": sql, "search_mode": mode}
        self.p.stdin.write(json.dumps(req, ensure_ascii=False) + "\n"); self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        if show:
            print(f"  → {json.dumps(req, ensure_ascii=False)}")
            print(f"  ← {json.dumps(resp, ensure_ascii=False)}")
        return resp
    def shutdown(self):
        self.p.stdin.write(json.dumps({"id": 9999, "op": "shutdown"}) + "\n"); self.p.stdin.flush()
        print(f"  ← {self.p.stdout.readline().strip()}")
        self.p.wait()
    def kill(self):
        os.kill(self.p.pid, signal.SIGKILL); self.p.wait()

subprocess.run(["rm", "-rf", DATA])
print("== 启动（输出握手）")
c = Conn()

print("\n== 建表 + 插入（含向量）")
c.q("CREATE TABLE docs (id BIGINT PRIMARY KEY, category VARCHAR(8), content VARCHAR(16), embedding VECTOR(2));")
c.q("INSERT INTO docs VALUES (3,'乙','三','[0,2]'), (1,'甲','一','[0,0]'), (2,'甲','二','[1,0]');")

print("\n== 普通查询：条件 + 排序")
c.q("SELECT id, content FROM docs WHERE category = '甲' ORDER BY id;")

print("\n== 向量检索：TopK（按距离，距离相同按主键）")
c.q("SELECT id, L2_DISTANCE(embedding, '[0,0]') AS distance FROM docs ORDER BY distance LIMIT 3;")

print("\n== 事务：回滚")
c.q("BEGIN;")
c.q("UPDATE docs SET content = '改过' WHERE id = 1;")
c.q("SELECT id, content FROM docs WHERE id = 1;")
c.q("ROLLBACK;")
c.q("SELECT id, content FROM docs WHERE id = 1;")

print("\n== 事务：提交 + 重启（正常关闭）")
c.q("BEGIN;")
c.q("UPDATE docs SET content = '已提交' WHERE id = 2;")
c.q("COMMIT;")
c.shutdown()
print("\n== 重新启动，读回数据")
c = Conn()
c.q("SELECT id, content FROM docs ORDER BY id;")

print("\n== 崩溃测试：kill -9 后重启")
c.q("INSERT INTO docs VALUES (4,'丙','四','[3,4]');")
print("  （对进程发送 SIGKILL…）")
c.kill()
c = Conn()
c.q("SELECT id, content FROM docs ORDER BY id;")

print("\n== 向量索引 + ann 模式")
c.q("CREATE VECTOR INDEX docs_vec ON docs(embedding);")
c.q("SELECT id, L2_DISTANCE(embedding, '[0,0]') AS distance FROM docs ORDER BY distance LIMIT 3;", "ann")

print("\n== 不支持语句必须报错且不影响后续")
c.q("ALTER TABLE docs ADD extra BIGINT;")
c.q("SELECT id FROM docs ORDER BY id LIMIT 2;")
c.shutdown()
