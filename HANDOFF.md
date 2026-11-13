# 同一会话接续入口

最新 F01（2026-11-14）：用户反馈提交 `81a7935...` 正式准入失败，平台汇总为
1个 `JSON response timeout`，没有失败用例/SQL/阶段。见 `docs/FORMAL_FAILURE_F01.md`。
当前赛题分支源码为 P07+P02：主键等值CRUD使用既有lookup，避免全表加载；
这是有本地验证的超时风险缓解候选，**没有证明同一正式失败已经修复**。
原P07性能最佳快照 `best-b02-20261114`、原始基线和B03负/不确定结果继续保留。
暂先获取正式失败细节，不自动开始P08，也不再次正式提测。

前置工作和交接演练已完成，用户已在**本会话**切换模型并明确恢复长线优化。
当前按有界周期进行本地实验。不要创建新会话、自动切模型或启动无人看管的后台循环。
最新完成状态、基线身份与证据见 `optimization/state.json`、`docs/BASELINE.md`。
首批结果见 `docs/OPTIMIZATION_B01.md`：P01/P03/P04均未单独采用。
第二批P07组合缓存已采用，见 `docs/OPTIMIZATION_B02.md`；两种子查询约快3倍，
本地E为0.597888/0.599353。完整快照tag为 `best-b02-20261114`，原始基线仍保留。
第三批已profile P07并验证P02，见 `docs/OPTIMIZATION_B03.md`：
点查询两种子快3.18/3.74倍，默认E为0.494849/0.531551，未确立重复综合收益。
B03当时只追加工具和材料，没有替换P07；F01新增大数据证据后采用相同P02源码，
原隔离分支 `optimization/b03-p02-20261114` 仍保留。

## 接手读什么

1. 本文件、`optimization/state.json`、`AGENTS.md`。
2. 使用引擎 skill 开发，验证 skill 做验收，学习证据 skill 维护真实记录。
   工作区安装路径为 `../.agents/skills/minivectordb-*/SKILL.md`，
   仓库内同名源文件位于 `.agents/skills/`。
   若当前会话菜单尚未显示新 skill，直接读取对应 SKILL.md 继续，不需换会话。
3. 按需要读 `docs/CONTRACT.md`、`docs/ARCHITECTURE.md`、`docs/VALIDATION.md`，
   `optimization/queue.md` 给出可证伪候选。不要一上来重读所有原始资料。

## 环境与入口

Windows 根：`E:\develop\project_files\project\oceanbase\minivectordb`。
WSL：`Ubuntu-24.04`；Linux 路径 `/mnt/e/develop/project_files/project/oceanbase/minivectordb`。
当前原生 Release `/opt/mvdb-b03-p02/minivectordb`，
ASAN `/opt/mvdb-b03-p02-asan/minivectordb`；
冻结原生基线 `/opt/minivectordb-build/minivectordb`，
oracle `/opt/minivectordb-build/mvdb_oracle`。

PowerShell 可直接调用：

```powershell
wsl -d Ubuntu-24.04 -- python3 /mnt/e/develop/project_files/project/oceanbase/minivectordb/tools/verify.py --binary /opt/mvdb-b03-p02/minivectordb --output /mnt/e/develop/project_files/project/oceanbase/minivectordb/artifacts/resume-check-01
```

命令/完整 Docker/benchmark 入口见 `docs/VALIDATION.md`。当前基线镜像标签
`minivectordb:baseline`；P07性能参考为 `minivectordb:b02-p07`，
当前修复候选为 `minivectordb:f01-point-fix`，按 image ID 核对身份。
输出目录必须是新目录。已有完整测试通过且身份未变时不需要为“接手”重跑全套；
可先检查报告、二进制 hash、git diff 和一次短验证。

## 接续有界周期

本会话已满足恢复条件；接续时核对最新state、基线/tag和工作树，从队列选择一个假设。
P01 的演练历史见 `docs/REHEARSAL.md`，B01已补全完整负载比较但未采用。
P03/P04也已有完整负结果；P07已在第二批采用，P02在第三批完成两种子评估。
先读最新报告，避免重复相同实验。P07向量/点操作profile已经完成。
F01之后先取得正式失败用例/SQL/阶段日志，区分定位证据与风险缓解；
没有新指令不直接启动P08。恢复性能探索时再计数TopK淘汰前的JSON空数组分配。
P02点操作收益不能当作默认性能项目综合提升。
先定位开销，保持单一可解释改动，用隔离实验目录保存反例、差分与小规模筛选。
只有值得继续且正确的候选才跑3轮完整本地比较。一次最多3候选或45分钟为默认批次，
结束记录 best/rejected/inconclusive、剩余预算和下一步；用户新预算优先。

源码修改遵循工程 skill，测量遵循评估 skill。后续有新的真实学生解释或验证反馈时，
学习证据 skill 再纳入材料，不能由模型补写学生答案。DeepSeek Key 与本题数据库实现
和本地验证无关，不需要读取此前 Dify 的密钥。

## 停止与恢复

发现正确性回归、测量条件失配或结果不确定，先缩小/修复或明确拒绝该候选，
不要通过修改断言、忽略失败、混用轮次或扩大资源去制造提升。
日志/持久化结构变化要重复崩溃恢复；内存结构变化要重做超内存数据验证。
若出现需要用户才能决定的外部提交或预算问题，保存状态后简短说明。

准备阶段曾按要求停止等待切模型；当前已恢复，无需再次切换。后续可在本会话接续：

> 读取最新state和所需skill，按下一个有界周期继续优化，保留基线与真实证据。

这句话只授权当次本地优化，不默认授权推送或正式平台提交。源仓库当前 origin 指向
官方题包（本机 git 可能经 gh-proxy 重写），不能直接推过去。
用户已指定提测仓库 `https://github.com/shironekoalice/seekdb`，
本地远端名为 `submission`，赛题分支跟踪它；本轮修复沿用这个推送范围，
不授权自动正式提测。已提测通过的 Dify
位于同级 `powercontext/`，与本次新仓库分开。
