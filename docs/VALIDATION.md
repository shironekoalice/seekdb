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
python3 tests/point_paths.py --binary /opt/minivectordb-candidate/minivectordb --output artifacts/point-paths-new
```

`query_paths.py`和`map_collisions.py`是B01新增独立工具，分别覆盖119次跨查询路径全排序比较和24个同桶键的
事务/恢复状态比较；也支持 `--image`。修改缓存时还需验证容量淘汰、映射身份及超内存数据。
`cache_eviction.py`为B02新增工具，两表24000行、8200次回滚、80次查询及全状态比较，
覆盖淘汰和映射身份复用；先在冻结基线上通过，再验证候选。

`point_paths.py`为B03新增Python状态oracle，1218次点查询、812次强制范围扫描、
90个错误反例、120次随机操作，覆盖PK等值、其他AND条件、提前报错和事务/恢复，
也支持 `--image`。已在P07控制组和隔离P02的原生/Docker/sanitizer运行；
B03时主分支仍为P07；F01收到正式超时反馈后，补充大数据对照才采用P02。
这不改变B03的历史结论，也不证明正式超时已定位。

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

当前 WSL 可在有本任务容器 cgroup 写权限时，给 `out_of_core.py` 增加
`--enforce-no-swap`。工具只设置本次新容器的 `memory.swap.max=0` 并回读核实；
失败则保留错误，不放宽原有 host 换页检查，也不修改全局 swap。
仅有 Docker 的 `--memory-swap` 参数不能证明实际限制已经生效。

正式超时反馈后的多记录诊断使用：

```bash
python3 tests/large_point_paths.py --image minivectordb:f01-point-fix --enforce-no-swap --output artifacts/large-points-new
```

默认20万行×1024字节正文，活跃正文超过128 MiB容器内存；
逐条核对PK命中/未命中、其他AND条件、事务、回滚及重启，另有范围扫描控制。
记录每个请求的墙钟时间和cgroup实际读取字节。此诊断保留公开请求/启动超时，
不是正式失败用例的复现或默认性能E。F01报告与原样保留的资源检查失败见
`docs/FORMAL_FAILURE_F01.md`。

## 正式超时的环境与文件名反例

F02重新从实际提交的git archive在全新Linux目录进行
`docker build --no-cache --platform=linux/amd64`，再直接运行未改动的公开驱动。
构建日志、镜像/ELF/依赖身份和有效资源配置见 `FORMAL_FAILURE_F02.md` 及其证据目录。
新增工具保留默认120秒启动、60秒请求时限：

```bash
python3 tests/environment_contract.py --image minivectordb:f02-namespace-fix --output artifacts/environment-new
python3 tests/storage_namespace.py --image minivectordb:f02-namespace-fix --output artifacts/namespace-new
python3 tests/storage_namespace.py --binary /opt/mvdb-f02-asan/minivectordb --output artifacts/namespace-asan-new
```

环境工具检查真实默认入口、主动ready、UTF-8分片/CRLF/大报文、事务和重启，
以及root/非root可写bind挂载；不可写挂载是必须快速失败的负对照。
它不安装依赖、不修改Dockerfile或主机设置，也不能读取正式主机的配置。

文件名工具用独立字典核对六种表名、同桶键、跨表写入、索引、事务、语句失败原子性、
重启和kill恢复。旧版本在合法 `statement` 表第三次同桶插入时发生60秒JSON超时，
实际节点next指向自身；当前把INSERT临时重复键文件改为 `statement_keys.tmp`。
旧版与新版使用相同工具和配置，没有修改公开断言或增加超时。
原提交的最小四条SQL反例及修复后继续使用旧数据卷也已实测，记录在F02证据中。
这确认本地bug，但正式失败SQL依然不可见，不应宣称已复现同一个隐藏输入。

## 准入边界

F03准入边界工具（不是性能比较）：

```bash
python3 tests/admission_edges.py --image minivectordb:f03-admission --output artifacts/admission-edges-new
python3 tests/out_of_core_ordering.py --image minivectordb:f03-admission --output artifacts/order-small-new
python3 tests/out_of_core_ordering.py --image minivectordb:f03-admission --memory-mib 2048 --data-mib 2304 --output artifacts/order-full-new
```

`admission_edges.py`覆盖最终TopK与中间候选大小的区别、精确16MiB响应及超一字节、
反引号Token、大字段声明、超过64MiB的单行提交/强杀恢复及宽键Unicode排序。
`out_of_core_ordering.py`以固定128MiB/256MiB或2GiB/2304MiB活跃正文，
查询不投影正文的宽字符串排序、事务回滚与重启。只修改本次cgroup的swap.max并回读，
要求实际0、无OOM、host无换出；启动120秒/单请求60秒保持公开值。
题干逐项核对及未公开边界见 `ADMISSION_AUDIT_F03.md`。

F04内部文件身份与长名称检查：

```bash
python3 tests/file_identities.py --image minivectordb:f04-admission --output artifacts/file-identity-new
python3 tests/file_identities.py --binary /opt/mvdb-f04-release/minivectordb --previous-binary /opt/mvdb-f03-release/minivectordb --output artifacts/file-upgrade-new
```

覆盖11张表、长达4096字符的名称、相同长前缀、覆盖层复用、多表事务和重复恢复；
可选previous-binary实际用旧版产生失败目录，再交给新版恢复。
原生检查还真实在排序键写入后exit86，确认重启清理非权威临时文件。
F04再次运行F03边界、公开/扩展、独立查询及完整超内存排序；
详见 `ADMISSION_AUDIT_F04.md`。

## 性能

B04新增两种工具。行借用检查通过公开JSONL对独立完整状态及全排序结果作比较：

```bash
python3 tests/row_views.py --binary /opt/mvdb-b04-p09/minivectordb --output artifacts/row-views-new
```

同一工具可指定ASAN二进制。实际使用0/1024/65536/1048576字节行缓存、
两表、多种投影/过滤/升降序/并列/TopK，包含扫描更新、事务回滚与kill恢复。
不读取被测缓存内部状态，不能以实际通过代替更大规模的内存验证。

在存在先后顺序波动时，完整轮次可以交错运行：

```bash
python3 tools/paired_benchmark.py --baseline-image minivectordb:f04-admission --candidate-image minivectordb:b04-p09 --oracle /opt/minivectordb-build/mvdb_oracle --seed 20261113 --first baseline --output artifacts/paired-new
```

默认仍是10000×128、1000查询、三轮、独立全排序oracle与逐轮重启核对写入。
每轮两个镜像各有独立数据卷，顺序AB/BA/AB；第二种子用`--first candidate`反转。
两个子目录各保存原有格式的T/B/M报告，`order.json`记录实际顺序，
`comparison.json`沿用既有公式与换页拒绝条件。没有改变公开驱动或断言。

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

PK点操作另用 `tools/point_benchmark.py --image TAG --output NEW`：
默认10000行，SELECT/UPDATE/DELETE各300次、重插约150次，命中/未命中交替，
逐条自动提交，每轮关闭并重启核验全部状态。三轮T按操作报告，B/M统计全轮；
这不是默认向量性能项目，不计算官方E。`--rows 1000 --operations 60 --rounds 1`
仅用于筛选。第二种子反转两个镜像顺序，保留弱收益或负结果。

`tools/profile_workload.py --binary PATH --output NEW`使用10000×128/1000查询诊断负载。
独立以RelWithDebInfo、编译/链接均加 `-pg` 构建，从新的诊断目录执行，
再用 `gprof PATH gmon.out` 保存完整报告。计数含装载/索引，插桩时间不是平台T，
不能和Release性能混比。B03完整profile压缩副本在精选证据目录。

## 提交与证据

```bash
python3 tools/evidence.py agent/conversation.json
python3 tools/submission_check.py --require-clean
python3 tools/install_skills.py --workspace .. --check
```

submission_check 为只读检查，不提交、不推送。冻结完整 commit 与镜像 ID，
同一提交内包含源码/Dockerfile/agent/conversation.json。官方正式29 case、
官方性能基线与 Agent 评价均未由这些工具运行。
