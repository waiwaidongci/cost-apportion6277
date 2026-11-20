# 工时台账

`workentryops`：
- `id` 数据库自增序号，作为投影游标；
- `entry_id` 业务记录编号；`rev` 该记录内的版本序号；
- `op_id` 唯一，客户端重试幂等；
- `kind` = create / update / withdraw；`base_rev` 客户端所见基版本；
- `changes` 仅存本次改动的字段，因此并发改不同字段可以合并。

`workledger`：(gen, person, day, department) 唯一，秒数用 `seconds_num/seconds_den`
保存精确分数；`day` 为 Asia/Shanghai 自然日，跨日条目已拆分。

`ledgergens`：台账版本。状态 building → active → retired；同一时刻只有一个 active。
`workrecomputes`：每次重算的影响人日、并发冲突与结果，用于核对增量/全量。
`ledgercursors`：`last_op_id` 已投影到的操作序号，`active_gen` 生效版本，
`full_checksum` 最近一次全量核对摘要。

升级已有数据库：SQLAlchemy 启动会新建以上表，不改动既有底稿/口径/报告表；
旧报告没有 `result.worktime`，前端会显示“未包含工时依据快照”，不影响历史数据。
