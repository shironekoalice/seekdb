# 需求与验证契约

基于用户提供的 `rules/task.md`、`rules/scoring.md`，以及上游
`2026-competition-round2` 的 commit `f4d4e26653af823a7601668802f80e374ad6643c`。
当前公开包只有 20 case/95 SQL；正式 29 case 必须全过才有两项排名资格。

| 要求 | 当前实现 | 验证 |
|---|---|---|
| JSONL ready/execute/shutdown，id 同类型原样返回，flush，stderr 日志 | `src/main.cpp` | 公开协议驱动、真实离线只读 Docker |
| BIGINT int64 精度，VARCHAR 按 Unicode 码点长度/排序，有限 float32 | `src/sql.cpp` | 公开边界、独立 types/ordering、随机模型 |
| CREATE、CRUD、多行 INSERT、投影、AND 比较、ORDER BY/LIMIT | 子集解析与执行 | 公开 SQL、扩展 DDL/列序/拒绝不支持语法 |
| 默认 exact，欧氏距离，先 WHERE 后 TopK，同值 PK 升序 | 堆选 TopK；ann 也精确 | Python 独立距离、全排序 C++ oracle、过滤反例 |
| 索引创建及增删改、事务中普通/向量可见性一致 | 派生 flat 索引 + 当前 PK 版本过滤；事务走合并视图 | index maintenance、transaction、restart |
| 语句失败原子性，保留显式事务之前成功的语句 | statement.tmp 完整验证后合入 txn | 批量重复键、非法更新、回滚与提交 |
| 自动提交、显式提交/回滚、成功响应前持久化 | 日志 commit marker + fdatasync | kill 后恢复、6 个故障点、重复恢复 |
| 超内存数据，所有数据/临时/索引位于 /data | 磁盘主键映射 + 有界 row cache + 流式日志 | 128 MiB/256 MiB 和 2 GiB/2304 MiB 活跃数据检查 |
| 离线 Linux x86_64，源码构建，固定依赖 | 多阶段 Docker，固定基础镜像/编译器，JSON vendored | 实际 docker build 与全公开 suite |
| Agent 单 JSON，UTF-8 ≤128 KiB，无重复键/非有限值/密钥 | `agent/conversation.json` | `tools/evidence.py`；真实性仍需原始来源 |

## 必须保留的边界

- 进程崩溃模型，不含断电、磁盘物理损坏；本地未证明这些额外故障下安全。
- 无 NULL、JOIN、聚合、子查询、ALTER、外键、嵌套事务、PK 更新支持。
- exact 不能在建索引后切换成有损检索；ann 达到召回率不等于 exact 正确。
- SELECT 输出不能超过协议 16 MiB；实现内部投影预算 15 MiB。
- 只支持列内 `BIGINT PRIMARY KEY` 等已约定子集；未验证额外 SQL 方言。
- 标识符支持字母/数字/下划线（首字符非数字）；不支持引号内的路径符号等扩展形式。
  VARCHAR(n) 的 n 约束写入值，不约束 WHERE 比较字符串的长度。
- WAL 单记录上限 64 MiB、类型长度解析上限 100000000；这些是当前实现限制，
  不能声称来自正式题包。遇到合法但超过这些边界的实际需求应重新评估。
- 超内存测试和内存/页缓存计量是本地证据；WSL 无 swap 限额支持时必须报告实际换页监测。

## 文档差异与外部未知

1. 原文较早段落写“不含 JSON 外壳”，当前 `STDIO_PROTOCOL.md` 与可执行驱动明确使用 JSONL，以实际题包为准。
2. 提交材料旧称根目录 `conversation.json`，4.4 明确默认 `agent/conversation.json`，使用后者。
3. 两份文档对未指定最终版本的默认选择有差异；正式提交后应由用户明确指定最终完整 commit。
4. 公开包没有正式性能基线、隐藏随机种子/全部 case、冻结 Agent 模型/提示词。
   本地 E 不能换算官方名次，材料格式通过不能预测学习得分。
5. 两项各 40 分为排名分，每下降一名少 0.5；原有 620 是满分而非自动基础分。
   每日正式提交最多两次，本地检查不限；本次未进行外部提测。
