# 验证工具与证据边界

所有数据库运行在 WSL Ubuntu-24.04。先进入：

```bash
cd /mnt/e/develop/project_files/project/oceanbase/minivectordb
```

每次使用新输出目录，完整 stdout/stderr 和 result.json 保留在 `artifacts/`（不入 git）。
精选结果复制到 `docs/evidence/` 入版本，原始路径仍记录。工具失败非零退出。

## 正确性

```bash
cmake -S . -B /opt/minivectordb-candidate -DCMAKE_BUILD_TYPE=Release
cmake --build /opt/minivectordb-candidate -j4
python3 tools/verify.py --binary /opt/minivectordb-candidate/minivectordb --output artifacts/native-new
docker build -t minivectordb:candidate .
python3 tools/verify.py --image minivectordb:candidate --output artifacts/docker-new
```

冻结原生基线 `/opt/minivectordb-build/minivectordb` 保留，不用候选覆盖。
`verify.py` 保持上游 driver/case 不变；先跑公开 20 case，再跑本地扩展。
原生额外跑 6 个故障点：追加前、部分追加、commit marker 前、sync 后、
apply 后、恢复过程中。每个故障确认 exit86，正常恢复两次；其他扩展测试还真实 kill
已提交/未提交进程。Docker 扩展包含真实容器 kill/restart，但故障环境变量仅原生测试注入。

随机模型测试固定种子 20261113，240 操作、48 次独立状态和 48 次过滤 TopK 比较。
Sanitizer 单独构建，不与性能比较混用：

```bash
cmake -S . -B /opt/minivectordb-asan -DCMAKE_BUILD_TYPE=Debug -DSANITIZE=ON
cmake --build /opt/minivectordb-asan -j4
python3 tools/verify.py --binary /opt/minivectordb-asan/minivectordb --output artifacts/asan-new
```

查询小改动可先定向执行：

```bash
python3 tests/contract.py --binary /opt/minivectordb-candidate/minivectordb --check ordering --check random_model --output artifacts/query-focus
python3 tests/query_paths.py --binary /opt/minivectordb-candidate/minivectordb --output artifacts/query-paths-new
python3 tests/map_collisions.py --binary /opt/minivectordb-candidate/minivectordb --output artifacts/map-collisions-new
python3 tests/cache_eviction.py --binary /opt/minivectordb-candidate/minivectordb --output artifacts/cache-eviction-new
```

`query_paths.py`和`map_collisions.py`是B01新增独立工具，分别覆盖119次跨查询路径全排序比较和24个同桶键的
事务/恢复状态比较；也支持 `--image`。修改缓存时还需验证容量淘汰、映射身份及超内存数据。
`cache_eviction.py`为B02新增工具，两表24000行、8200次回滚、80次查询及全状态比较，
覆盖淘汰和映射身份复用；先在冻结基线上通过，再验证候选。

故意破坏隔离副本，确认测试会拒绝，原始代码不变：

```bash
python3 tools/mutation_check.py --binary /opt/minivectordb-build/minivectordb --output artifacts/mutations-new
python3 tests/tool_contract.py
```

## 超内存数据

```bash
python3 tools/out_of_core.py --image minivectordb:candidate --memory-mib 128 --data-mib 256 --output artifacts/ooc-small-new
python3 tools/out_of_core.py --image minivectordb:candidate --memory-mib 2048 --data-mib 2304 --output artifacts/ooc-full-new
```

活跃行负载大于限额，非 WAL 重复覆盖；额外检查更新、回滚、全表数量、TopK、重启。
使用 8 MiB 行缓存和包含页缓存的 cgroup 内存上限。当前 WSL Docker 报 SwapLimit=false，
实际 memory.swap.max 可能仍为 max；报告记录该值及整段 host pswpin/pswpout。
测试要求没有 swap-out 才采纳内存证据，但这不代表 Docker 成功禁止了 swap。
不修改系统 swap、不全局清缓存、不停止其他项目。

## 性能

```bash
python3 tools/benchmark.py --image minivectordb:candidate --oracle /opt/minivectordb-build/mvdb_oracle --output artifacts/bench-new
python3 tools/compare.py docs/evidence/benchmark-baseline.json artifacts/bench-new/result.json --output artifacts/bench-new/comparison.json
```

默认 10000 行×128 维，1000 查询，K10，一半过滤，每100行一批提交，固定本地种子，
每轮独立数据卷，默认3轮。微型筛选可加 `--rows 200 --queries 4 --dimensions 8 --rounds 1`，
但单轮报告会被 compare 拒绝，不能宣布全面加速。

Oracle 使用独立 C++ 全排序，未链接引擎执行代码；差分测试另有 Python 实现。
写入在统计结束后重新打开逐行核对向量和内容；查询对每条请求核对过滤、结果数、
去重、距离与排序。exact 必须命中精确 ID；ann 的过滤/无过滤组召回分别至少 .95。

T 是驱动端完整批次或查询序列墙钟时间（包含响应传输/解析）；B/M 覆盖启动、装载、
索引、预热、执行、关闭和 sync -f。容器用 sleep 保持 cgroup 存活，数据库通过
docker exec 运行，事后取 io.stat/memory.peak；睡眠监督进程与 sync 的少量开销一并计入。
冷暖状态不全局重置、无独占设备/官方带宽限制，因此仅用于同机本地比较。

compare 使用官方公式但没有官方 reference：基线 T/B/M 分别取三轮中位数，
候选每轮计算 E 再取中位数，四舍五入6位；同一负载/环境才比较。不能把 E 当排名分。
三轮任一失败无有效比较。检测换页或明显系统竞争时先解决测量条件/标不确定，不挑最好轮次。

## 提交与证据

```bash
python3 tools/evidence.py agent/conversation.json
python3 tools/submission_check.py --require-clean
python3 tools/install_skills.py --workspace .. --check
```

submission_check 为只读检查，不提交、不推送。冻结完整 commit 与镜像 ID，
同一提交内包含源码/Dockerfile/agent/conversation.json。官方正式29 case、
官方性能基线与 Agent 评价均未由这些工具运行。
