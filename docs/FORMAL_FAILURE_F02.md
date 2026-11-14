# F02：环境复核与可复现的 INSERT 无限循环修复

2026-11-14 再次失败的记录为 **47253416**，实际提交为
`3f784f1a089e86d84471a3ac8a48a57b3ec19670`。
平台开始于北京时间03:19:55，结束于03:40:47；仍仅反馈1个JSON响应超时。
没有用例名、SQL、超时阶段或stderr，不能推断其他正式用例是否通过。
记录已脱敏；未再次正式提测。

## 环境核对

重新完整阅读赛题和计分规则，并核对在线赛题3.1/4.2.1。
上游公开分支仍为`f4d4e26653af823a7601668802f80e374ad6643c`，
协议、配置、公开manifest及驱动与本地一致（仅换行差异）。
原文3.1声称“不再采用此前示例中的JSON封装”，但`STDIO_PROTOCOL.md`和实际驱动
要求JSONL，平台本次也报JSON响应超时。因此保持可执行题包的JSONL，不改纯SQL，
也不需要启动MySQL/seekdb服务或开放端口。

从实际提交git archive到全新的Linux目录，`--no-cache --platform=linux/amd64`
构建成功。运行二进制SHA-256为`d530fc51ae9b9e09020030a8dffbf489488b97a55ce9b8b6a6f44da9f5b85688`，与原被测Release一致。
确认ELF x86_64、动态依赖齐全、默认ENTRYPOINT/CMD正确。
实际Docker条件为离线、只读根、2 CPU、2147483648字节、128 PID、cap-drop ALL、
no-new-privileges、stdin打开、无TTY、可写/data卷。

| 检查 | 结果 |
|---|---|
| 完全未发送输入前的主动ready | 通过，约1.238秒 |
| 干净提交镜像全部公开用例 | 20/20通过 |
| UTF-8跨写入边界、CRLF、大请求/大响应 | 通过 |
| 提交、回滚、正常重启、kill后的恢复 | 通过 |
| root及UID65534的可写bind挂载 | 均通过 |
| 不可写/data负对照 | 0.861秒内退出，报database exited before response，没有超时 |

最初环境工具因Docker省略默认User字段发生KeyError；修正为读取默认空User后通过。
原工具和失败报告均保留，不能把工具错误归为数据库失败。
这些检查没有发现公开配置下的运行环境错误；它们不等于检查了正式主机。

## 四条SQL的独立反例

```sql
CREATE TABLE statement(id BIGINT PRIMARY KEY,body VARCHAR(8));
INSERT INTO statement VALUES(0,'a');
INSERT INTO statement VALUES(18956,'b');
INSERT INTO statement VALUES(26218,'c');
```

这四条SQL单独在原提交镜像复现：前三条成功，第四条等待
60.041425秒后由未修改的公开驱动报`JSON response timeout`。
另一个六表回归工具在原提交和保留的P07镜像中也都复现同一超时，
采用完全相同的工具SHA和公开60秒请求超时，没有放宽限制。

根因是`INSERT`用`statement.pk`做本语句重复键检查；合法表名`statement`
也将其主键文件命名为`statement.pk`。后续INSERT截断了表索引，
内存中的旧桶头仍在，追加同桶键时节点65560的next成为65560，
查找另一个同桶键就无限循环。0、18956、26218均落在同一桶。
文件结构诊断保存了实际节点，不只由耗时推测原因。

## 修复及实测

只把重复键临时映射改为`statement_keys.tmp`，使其后缀与表的`.pk`不同；
不禁止合法表名，不改SQL、WAL、提交同步、恢复算法或缓存结构。
修复镜像四条SQL全部完成，第四条实测0.006089秒。
还实际将旧版卡住后留下的同一数据卷交给修复版，恢复两条已提交记录，
继续第三次插入并核对全表内容通过。

- 新Docker：公开20例、扩展5项通过。
- 新sanitizer二进制：公开20例、扩展11项通过，包含6个故障点及重复恢复。
- 六种表名、同桶键、跨表INSERT、更新/删除、失败多行INSERT、事务/回滚、
  索引检索、重启及kill恢复：Docker和sanitizer均通过。
- 新镜像环境交互与权限矩阵通过；32份实际sanitizer stderr日志未发现报告。
- 新镜像2 GiB上限、2304 MiB活跃正文检查通过：
  总耗时177.370秒，恢复27.158秒，
  实际swap.max=0，host换页增量0，无OOM。

当前源码SHA-256：`52f9997d9c4865d3ee52751b5b98c123f63e3f52fd36d4bc639609ac90d2b97e`。
Release二进制SHA-256：`f5af5bd0c049851cf835c56b17853cfea7156e0e34c3ed3b79abd2f1abd38e35`。
Docker镜像：`minivectordb:f02-namespace-fix`，ID`sha256:4dd903e8c7f970428c6411d59f68213a89ea0aa29d8744b2210a729a843f128b`。

原始基线、P07最佳性能快照及历史失败材料保持不变。本轮是正确性修复，没有运行
三轮性能比较，不报告新E或官方分数。JSON学习材料仅追加真实用户质疑和工具结果，
F01/F02作为同一条诊断链，不编造学生解释或重复评分实例。

## 结论边界和接续

已确认并修复一个会产生相同60秒JSON超时的本地根因；正式隐藏输入仍不可见，
不能确认平台恰好命中这个表名和键序列，也不能保证正式准入已经通过。
优先取得平台实际启动命令、失败阶段/用例标识及stderr；如平台允许环境预检，
由用户明确发起。当前不会自动再次正式提测或开始无关性能优化。

复核命令：`python3 tests/environment_contract.py --image IMAGE --output NEW_DIR`及
`python3 tests/storage_namespace.py --image IMAGE --output NEW_DIR`。
精选材料SHA见`docs/evidence/formal-failure-f02/manifest.json`；
sanitizer原始日志摘要见同目录`sanitizer-audit.json`。
完整原始目录为`artifacts/formal-environment-f02-20261114-1021/`。
