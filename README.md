# MiniVectorDB

OceanBase 加赛的自研单机数据库基线，C++20，Linux x86_64。源码来自空的题包骨架，
未使用现成数据库/向量引擎；nlohmann/json 仅用于 JSON/CBOR 序列化。

## 开始

在 WSL Ubuntu-24.04 中：

```bash
cd /mnt/e/develop/project_files/project/oceanbase/minivectordb
cmake -S . -B /opt/minivectordb-candidate -DCMAKE_BUILD_TYPE=Release
cmake --build /opt/minivectordb-candidate -j4
docker build -t minivectordb:local .
python3 tools/verify.py --image minivectordb:local --output artifacts/my-first-check
```

输出目录必须新建。原生快速验证使用 `--binary /opt/minivectordb-candidate/minivectordb`。
性能 oracle 编译为同目录的 `mvdb_oracle`，仅测试工具使用，不进入运行镜像。
已冻结的 `/opt/minivectordb-build/minivectordb` 和 `minivectordb:baseline` 保留作对照。

## 运行入口

```bash
docker run --rm -i --network=none --read-only --cpus=2 --memory=2g \
  --memory-swap=2g --mount type=volume,src=mvdb-demo,dst=/data minivectordb:local
```

启动先收到 `{"type":"ready"}`，再输入一行一个请求：

```json
{"id":1,"op":"execute","sql":"CREATE TABLE t(id BIGINT PRIMARY KEY,text VARCHAR(20),v VECTOR(2));"}
{"id":2,"op":"execute","sql":"INSERT INTO t VALUES(1,'数据库','[1,0]'),(2,'向量','[0,1]');"}
{"id":3,"op":"execute","sql":"SELECT * FROM t ORDER BY id;"}
{"id":4,"op":"execute","sql":"CREATE VECTOR INDEX vi ON t(v);"}
{"id":5,"op":"execute","sql":"SELECT id,L2_DISTANCE(v,'[1,0]') AS d FROM t ORDER BY d LIMIT 1;","search_mode":"exact"}
{"id":6,"op":"execute","sql":"BEGIN;"}
{"id":7,"op":"execute","sql":"UPDATE t SET text='临时' WHERE id=1;"}
{"id":8,"op":"execute","sql":"ROLLBACK;"}
{"id":9,"op":"execute","sql":"BEGIN;"}
{"id":10,"op":"execute","sql":"UPDATE t SET text='已提交' WHERE id=1;"}
{"id":11,"op":"execute","sql":"COMMIT;"}
{"id":12,"op":"shutdown"}
```

再次运行同一 Docker 命令，查询 `SELECT * FROM t ORDER BY id;` 可见提交值；
不要再次建同名表。出错响应为 `ok:false` 加非空 `error.code`/`message`。
支持范围、资源限制、日志格式及恢复流程见 `docs/CONTRACT.md` 和 `docs/ARCHITECTURE.md`。

本地工具只用于开发验证；官方 29 case、冻结负载/基线及 Agent 评审不在公开题包中。
当前 ANN 使用精确检索回退。交接入口是 `HANDOFF.md`，正式学习材料入口是
`agent/conversation.json`，不是上游遗留的根目录空文件。

最近一次正式超时的环境复核、四条SQL反例和文件名冲突修复见
`docs/FORMAL_FAILURE_F02.md`。本地反例已修复；正式隐藏输入仍不可见，
本地验证不能作为正式准入已通过的声明。
