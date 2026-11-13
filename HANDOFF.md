# 同一会话接续入口

用户要求：当前模型完成前置工作与一次交接演练后停止，由用户自行在**本会话**
切换 6.1SOL，再继续长线优化。不要创建新会话、自动切模型或启动无人看管的后台循环。
最新完成状态、基线身份与证据见 `optimization/state.json`、`docs/BASELINE.md`。

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
原生 Release `/opt/minivectordb-build/minivectordb`，
ASAN `/opt/minivectordb-asan/minivectordb`，
oracle `/opt/minivectordb-build/mvdb_oracle`。

PowerShell 可直接调用：

```powershell
wsl -d Ubuntu-24.04 -- python3 /mnt/e/develop/project_files/project/oceanbase/minivectordb/tools/verify.py --binary /opt/minivectordb-build/minivectordb --output /mnt/e/develop/project_files/project/oceanbase/minivectordb/artifacts/resume-check-01
```

命令/完整 Docker/benchmark 入口见 `docs/VALIDATION.md`。当前基线镜像标签
`minivectordb:baseline`；采用候选时使用新标签，按 image ID 核对身份。
输出目录必须是新目录。已有完整测试通过且身份未变时不需要为“接手”重跑全套；
可先检查报告、二进制 hash、git diff 和一次短验证。

## 第一个有界周期

确认用户已切换并发出继续指令后，核对基线/tag和工作树；从队列选择一个假设。
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

准备阶段结束保留所有事实；`state.json` 标记等待用户切模型。用户接续时直接在本会话说：

> 已切换 6.1SOL，读取 minivectordb/HANDOFF.md 和三个工作区 skill，先按一个有界周期继续优化，保留基线与真实证据。

这句话只授权当次本地优化，不默认授权推送或正式平台提交。源仓库当前 origin 指向
官方题包（本机 git 可能经 gh-proxy 重写），不能直接推过去。已提测通过的 Dify
位于同级 `powercontext/`，与本次新仓库分开。
