#!/usr/bin/env python3
"""ANN vs exact 基准：recall@10 与查询延迟。"""
import json, os, random, statistics, subprocess, sys, time

BIN = sys.argv[1] if len(sys.argv) > 1 else "target/release/mvdb"
ROWS = int(sys.argv[2]) if len(sys.argv) > 2 else 10000
DIM = 128
QUERIES = 100
K = 10
DATA = "/tmp/mvdb-bench-data"

class Conn:
    def __init__(self):
        self.p = subprocess.Popen([BIN], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                  env=dict(os.environ, MVDB_DATA=DATA))
        self.id = 0
        line = self.p.stdout.readline()
        assert json.loads(line) == {"type": "ready"}, line
    def req(self, sql, mode="exact"):
        self.id += 1
        self.p.stdin.write(json.dumps({"id": self.id, "op": "execute", "sql": sql, "search_mode": mode}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())
    def close(self):
        self.p.stdin.write(json.dumps({"id": 99999, "op": "shutdown"}) + "\n")
        self.p.stdin.flush()
        self.p.stdout.readline()

def vec(v):
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"

random.seed(7)
CLUSTERS = int(os.environ.get("CLUSTERS", "0"))
subprocess.run(["rm", "-rf", DATA])
c = Conn()
assert c.req("CREATE TABLE docs (id BIGINT PRIMARY KEY, category VARCHAR(8), embedding VECTOR(%d));" % DIM)["ok"]
if CLUSTERS:
    centers = [[random.uniform(-1, 1) for _ in range(DIM)] for _ in range(CLUSTERS)]
    base = [[c + random.gauss(0, 0.05) for c in centers[i % CLUSTERS]] for i in range(ROWS)]
else:
    base = [[random.uniform(-1, 1) for _ in range(DIM)] for _ in range(ROWS)]
t0 = time.time()
B = 500
for s in range(0, ROWS, B):
    rows = ",".join(f"({i},'c{i%5}','{vec(base[i])}')" for i in range(s, min(s + B, ROWS)))
    r = c.req(f"INSERT INTO docs VALUES {rows};")
    assert r.get("ok"), r
t_load = time.time() - t0
print(f"加载 {ROWS} 行: {t_load:.2f}s")

t0 = time.time()
assert c.req("CREATE VECTOR INDEX docs_vec ON docs(embedding);")["ok"]
print(f"建 IVF 索引: {time.time()-t0:.2f}s")

queries = [[random.uniform(-1, 1) for _ in range(DIM)] for _ in range(QUERIES)]
def run(mode):
    lat, results = [], []
    for q in queries:
        sql = f"SELECT id, L2_DISTANCE(embedding, '{vec(q)}') AS d FROM docs ORDER BY d LIMIT {K};"
        t = time.perf_counter()
        r = c.req(sql, mode)
        lat.append((time.perf_counter() - t) * 1000)
        results.append([row[0] for row in r["rows"]])
    return lat, results

exact_lat, exact_res = run("exact")
ann_lat, ann_res = run("ann")
for qi, (a, e) in enumerate(zip(ann_res, exact_res)):
    if set(a) != set(e):
        print("first mismatch q", qi)
        print("  exact:", e[:10])
        print("  ann  :", a[:10])
        break
hits = sum(len(set(a) & set(e)) for a, e in zip(ann_res, exact_res))
recall = hits / (QUERIES * K)
print(f"exact: p50 {statistics.median(exact_lat):.2f} ms  p95 {sorted(exact_lat)[int(QUERIES*0.95)-1]:.2f} ms")
print(f"ann  : p50 {statistics.median(ann_lat):.2f} ms  p95 {sorted(ann_lat)[int(QUERIES*0.95)-1]:.2f} ms")
print(f"recall@{K}: {recall:.4f}")
c.close()
