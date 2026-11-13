# 准备基线记录 — 2026-11-13

这是本地可运行、可验证的优化起点，未提交官方正式评测。原有 Dify 项目没有改动。
精确代码/镜像/工具身份写入 `optimization/state.json` 和 `docs/evidence/identity.json`，
基线 tag 为 `prep-baseline-20261113`。公开题包上游固定为
`f4d4e26653af823a7601668802f80e374ad6643c`。

## 已执行验证

| 验证 | 结果与范围 | 原始结果副本 |
|---|---|---|
| 原生公开测试 | 20 case 全通过 | `evidence/native-public.json` |
| 原生扩展 | 5组语义/随机检查 + 6故障点，全通过 | `evidence/native-extended.json` |
| Docker 真实入口 | 20公开 + 5组扩展，全通过 | `evidence/docker-public.json`、`docker-extended.json` |
| ASAN+UBSAN | 20公开 + 11组本地检查，全通过，无 sanitizer 报告 | `evidence/sanitizer-public.json`、`sanitizer-extended.json` |
| 小内存活跃数据 | 128 MiB 限额 /256 MiB payload，通过；该早期运行尚无 swap 监测 | `evidence/out-of-core-128m.json` |
| 超过正式内存量级 | 2 GiB 限额 /2304 MiB payload，36864行，通过；恢复26.55秒；无OOM/换页 | `evidence/out-of-core-2g.json` |
| 三轮性能 | 每轮写入重启校验10000行，1000条查询，两个分组召回均1.0 | `evidence/benchmark-baseline.json` |

原始日志在 `artifacts/`，`evidence/manifest.json` 记录副本哈希和来源。
故障测试只覆盖进程终止，不是断电/磁盘损坏证明；正式29例仍未知。

## 本地性能起点

负载为10000行×128维、1000查询、一半过滤、K10，每100行事务提交，mode=exact，
seed=20261113，2 CPU/2 GiB，每轮独立数据卷，WSL2/i7-14650HX。

| 项目 | 第1轮 T | 第2轮 T | 第3轮 T | T 中位数 | B 中位数 | M 中位数 |
|---|---:|---:|---:|---:|---:|---:|
| 写入 | 2.4528s | 2.4047s | 2.4479s | 2.4479s | 35348480 bytes | 13643776 bytes |
| 查询 | 9.2013s | 8.7566s | 9.2019s | 9.2013s | 40644608 bytes | 22261760 bytes |

M 是 cgroup peak，含页缓存；B 是本机 cgroup 物理读写。
本地 supervisor/exec/sync 开销也在资源范围内；宿主缓存未统一清空，
没有独占官方设备、带宽限制或官方 reference。不可据此承诺官方E/排名。
WSL Docker 报 swap 限额不支持，实际 memory.swap.max=max；
本次各阶段主机 pswpin/pswpout 增量均0。换环境/测量条件应重新建立基线。

`baseline-self-comparison.json` 是工具演示，不是优化收益：各指标中位数归一化后
再取每轮E中位数，基线自比也不强制精确等于0.5；恒定指标的单元测试验证0.5。

## 已知缺口与下一步

日志无checkpoint/回收；恢复重放完整历史。ANN 当前精确回退；索引扫描仍加载整行。
普通PK条件缺少定位优化，谓词逐行解析。这些只是候选热点，下一阶段要用实测定位。
参考 `optimization/queue.md`；先选一个低风险假设，避免同时改多个核心机制。

学生学习材料保持真实：当前技术实现、实验与解释归 Agent/工具，尚未出现足够的
学生技术解释。JSON格式校验不能把自动化产物转换成学生学习成绩。
冻结前修复了 WHERE 比较误用 VARCHAR 写入长度限制的问题，真实失败反例保存在
`evidence/predicate-regression-before.json`。两项早期故意破坏的副本（缺过滤、
缺commit marker）被独立测试拒绝，控制组通过，见 `evidence/mutation-controls.json`。
最终扩展测试还覆盖了真正截断在64KiB复制块内部的日志记录。

交接演练已完成，见 `docs/REHEARSAL.md`。候选未采用，正式基线保持该 tag 对应源码；
后续提交只补充交接记录、状态及 skill 的收尾预算提醒。
