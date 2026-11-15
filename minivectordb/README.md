# MiniVectorDB

2026 OceanBase 数据库大赛「赛题一」参赛实现：单进程、单客户端的轻量向量数据库，
通过 **JSONL over stdin/stdout** 与判题器通信（协议见 `docs/PROTOCOL.md`）。

## 当前状态

| 能力 | 状态 |
|---|---|
| 协议（`ready` / `execute` / `shutdown`，id 原样回传） | ✅ |
| `CREATE TABLE`（BIGINT / VARCHAR(n) / VECTOR(n)，单 BIGINT 主键） | ✅ |
| `INSERT`（多行）/ `SELECT`（投影、`*`、比较、AND、ORDER BY、LIMIT）/ `UPDATE` / `DELETE` | ✅ |
| 持久化（页式堆文件 + WAL 页镜像重做 + 校验点） | ✅ |
| 事务（BEGIN/COMMIT/ROLLBACK、语句原子性、shutdown 回滚） | ✅ |
| 崩溃恢复（`kill -9` 后已提交数据不丢、未提交不生效） | ✅ |
| `L2_DISTANCE` 精确 TopK、过滤后 TopK、距离并列按主键升序 | ✅ |
| `CREATE VECTOR INDEX` + IVF 簇索引（球下界剪枝的精确 TopK） | ✅ |
| 索引维护（建索引后增删改、重启重建、`exact`/`ann` 切换） | ✅ |
| 性能优化（零分配距离计算、流式 TopK、剪枝、走索引优先） | ✅ |
| 内存约束（有界页缓存 + undo 回滚，1M 行 / 2 GiB 内运行） | ✅ |
| Agent 学习材料 `agent/conversation.json` | ✅ |

公开题包自测：**20/20 通过**（原生二进制与 `linux/amd64` 容器各跑一遍）。

## 构建

```bash
cargo build --release                 # 本机（macOS arm64）原生
cargo build --release --target x86_64-unknown-linux-musl   # 交叉编译 amd64 静态二进制
docker build --platform linux/amd64 -t mvdb:local .        # 提交用镜像（平台原生构建）
```

## 本地自测

```bash
# 需要先取得公开题包（mysqltest/ 与 protocol_check/），放在 testcases/
python3 testcases/protocol_check/run.py \
  --command '["'$PWD'/target/release/mvdb"]' \
  --package testcases --output /tmp/out
```

## 设计要点

- **页式存储**：8 KB 页，`[u16 slot_count][u16 free_start][u32 next_page]` + 槽目录（从页尾向下生长），
  行按 schema 编码（null bitmap + 定长/变长/向量 blob），向量存 float32。
- **WAL**：提交时把脏页整页镜像追加到 `wal.log`，随后写提交标记并 `fsync`；恢复时只重放
  「有提交标记」的整批页镜像，未提交批次丢弃。
- **事务**：`BEGIN` 时对页表和目录做快照，`ROLLBACK` 直接恢复快照；自动提交语句自身即一个事务。
  语句级原子性通过「先校验、后修改」保证（多行 INSERT 先查重再落盘）。
- **校验点**：`shutdown` 回滚未提交事务 → 全量写回数据文件 `fsync` → 清空 WAL。
- **恢复顺序**：加载数据文件 → 重放 WAL → 重建目录 → 输出 `{"type":"ready"}`。

## 目录

```
src/main.rs     协议循环与响应组装
src/json.rs     无依赖的 JSON 解析/序列化
src/sql.rs      SQL 词法/语法分析（本题子集）
src/engine.rs   存储、WAL、事务、执行器
Dockerfile      linux/amd64 镜像
```

## 基准（本机 M4，60k × 128 维，K=10，100 条查询）

| 数据分布 | exact p50 | ann p50 | recall@10 |
|---|---|---|---|
| 均匀随机 | 22.5 ms | **11.4 ms（2.0×）** | **1.0000** |
| 200 簇 + 高斯噪声 | 22.1 ms | **2.2 ms（10×）** | **0.9610** |

`ann` 路径不是"近似牺牲正确性"，而是**用包围球下界做安全剪枝**：

```
对每个簇：下界 = d(q, 质心) - 半径（成员到质心的最大距离）
若 下界 > 当前第 K 距离（留 1e-5 舍入裕量）→ 整簇跳过
```

所以随机数据上剪枝基本无效（退化为全表 + 零分配距离，仍比解码整行快 2×）；
聚类数据上能跳过绝大多数簇，提速 10×。recall 在两种分布下都 ≥ 0.95。

复现：

```bash
python3 tools/bench.py target/release/mvdb 60000            # 随机分布
CLUSTERS=200 python3 tools/bench.py target/release/mvdb 60000  # 聚类分布
```

## 资源约束实测

| 场景 | 结果 |
|---|---|
| 1,000,000 行 × 128 维（`MVDB_CACHE_MB=32`） | 加载后 RSS **485 MB**，查询后 **205 MB** |
| 200,000 行（`MVDB_CACHE_MB=16`） | 加载后 **141 MB**，流式 TopK 查询同为 **141 MB**（改造前 214 MB） |
| 容器 2 CPU / 2 GiB 跑公开题包 | **20/20** |

页缓存默认 64 MB（`MVDB_CACHE_MB` 可调），脏页不换出；提交时 WAL 落盘后回写数据文件，保证
换出的干净页可安全重读。事务回滚用 undo 前镜像，内存只与「本次事务改动的页数」相关。

## 限制（当前实现）

1. 主键未建索引：点查/更新/删除仍是全表扫描；
2. WAL 按整页镜像记录，写放大明显，尚未做增量日志与检查点合并；
3. `ORDER BY` 只支持单列 / 距离别名；
4. IVF 质心未持久化，重启按行重建（大库恢复成本高）；
5. 距离计算为标量循环，未做 SIMD。
