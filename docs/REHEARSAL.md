# 交接演练验收

2026-11-13 使用独立执行者，仅提供交接入口、三个已安装 skill 与隔离写入范围。
没有给出预定优化答案，也没有切换用户的主会话或主模型。
执行者完成一个 P01 查询候选后停止，正式基线未变。

## 实际结果

- 读取交接和 skill，自行选择 SELECT 谓词预绑定；先用隔离 baseline 做 gprof，
  观察到 matches 50021次、literal 26595次、field 60591次。0.01秒采样只能说明重复调用，
  不能证明时间占比或主瓶颈。
- 仅修改隔离副本 `storage.cpp`，一次绑定谓词列下标与常量。空表/LIMIT0校验仍提前执行。
- 六个空表非法谓词、五种比较、AND、长字符串比较反例通过；
  types/ordering/random_model/transaction 四组独立检查通过。
- 以1000行×16维、100查询做 baseline/candidate/candidate/baseline 顺序筛选。
  查询耗时分别0.1374/0.1264/0.1186/0.1275秒，所有查询的两个召回组均1.0。
- 正确判定 **inconclusive，未采用**：样本太短、没有完整三轮负载，
  不据此计算正式E或宣称全面加速。正式 best 仍为 `86adad6`。
- 学习材料只记录 Agent/tool，没有编造新的学生发言或理解；格式校验通过。
- 正式源码、原有学习材料、安装 skill、baseline镜像/tag 在演练中保持不变，
  演练容器已清理。随后主代理单独归档并更新收尾说明。

## 发现并处理的问题

独立演练要求所有临时文件留在指定目录，原生 Session 默认使用系统 temp；
执行者用 TMPDIR 将其限制到实验目录，没有修改正式工具或其他项目。
`git diff --no-index` 的1退出码表示存在差异，不是测试失败；归档时已按真实结果处理。

完整耗时482.496秒，比本次8分钟预算多2.496秒。最后归档脚本混用了 DateTime
和 DateTimeOffset，已修正并记录实际耗时。工程 skill 已增加为归档/清理预留预算、
统计整个周期时间的提醒；不将超时改写为“完全在预算内”。

## 接续位置

原始文件：`artifacts/handoff-rehearsal/report.md`、`record.json`、
`source/src/storage.cpp`、`probe.py`、四份 `screen-*/result.json`。
精选报告、审计、差异和真实学习材料保存在 `docs/evidence/rehearsal/`；
其中 `candidate.diff` 对应原始 `candidate.patch`，作为审阅材料。
大体积生成负载、二进制和 profile 原始数据仍保留在本机 artifacts。

下一模型可复用 P01 原型，在新的有界周期里补正式镜像构建、
公开用例及完整三轮本地比较，再决定是否采用。不要把本次演练当成已经合并的优化。
本次前置工作到此完成，等待用户在同一会话切换模型并发出继续指令。
