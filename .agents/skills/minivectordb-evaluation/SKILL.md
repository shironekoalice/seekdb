---
name: minivectordb-evaluation
description: "验证 MiniVectorDB 加赛的 JSONL 协议、SQL/事务正确性、崩溃恢复、超内存数据及性能；独立比较候选并审计提交材料。适用于本项目的验收和优化实验。"
---

# MiniVectorDB evaluation

定位工作区 `minivectordb/` 或当前含 `docs/VALIDATION.md` 的仓库。先读 `HANDOFF.md` 的状态与 `docs/VALIDATION.md`；语义不明时查 `docs/CONTRACT.md`、原文 `docs/rules/`、`STDIO_PROTOCOL.md`。

保留四层结论：公开 20 case、本地扩展验证、本地 benchmark、官方正式评测。前三者不能证明正式 29 case 全过，也不能推算官方排名。比较前确认被测二进制/镜像身份和源码指纹。

使用现成工具：

- `tools/verify.py`：保持上游公开驱动和断言原样，加本地独立模型与崩溃检查。真实 Docker 入口要再测只读根、离线网络、2 CPU/2 GiB。
- `tools/out_of_core.py`：活跃逻辑数据超过实际 cgroup 内存限额，含查询、更新/回滚、重启。不能拿反复覆盖产生的大 WAL 冒充超内存数据。
- `tools/benchmark.py`：独立全排序 oracle、固定种子、三轮写入/查询、分别记录 T/B/M、分组召回。默认 10k×128、1k 查询、一半过滤、K10；这是本地负载，不是官方隐藏种子或冻结生成器。
- `tools/compare.py`：按已公布公式计算本地相对 E；要求三轮全过、负载和环境一致；基线各分量取中位数，候选逐轮算 E 再取中位数，平滑量 1ms/1MiB/1MiB。不得拼接各轮最优值。
- `tools/evidence.py` 和 `tools/submission_check.py`：分别检查材料结构和提交一致性；不调用官方评审。

CPU 时间或进程 RSS 不能冒充平台 T/M。cgroup `memory.peak` 含页缓存；查询加载、建索引、预热及写回进入 B/M；完成同步后再读资源，保持 cgroup 存活。当前 WSL 的 swap 限制支持情况必须据实报告；检测到换页时内存比较需标记不确定。

微型筛选只能决定是否值得做完整测试，不能用来宣称全面加速。性能实验顺序执行，避免本任务的 build、oracle、大数据验证互相干扰。不通过全局 drop_caches 或 swapoff 改动其他项目的运行条件。

新增验证应针对风险或已发现错误，用独立参考结果/反例/故障注入；不要重复实现被测算法作为唯一 oracle。若怀疑测试无效，可用隔离副本故意破坏关键行为，确认测试确实拒绝它；不要修改正式基线制造失败。

失败时保存最小 SQL、种子、真实 stderr、重启前后结果、镜像/源码身份；缩小复现并修复后有针对性复测。未实际执行的项目标计划，超时/缺数据标未知，不标通过。

报告保留数字、单位、轮次、样本条件和已知局限。官方每日报名/提测配额属于用户决策，不以本地测通代替外部提交授权。
