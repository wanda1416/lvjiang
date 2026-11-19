# Profile 读写操作

> 最后更新：2026-11-19（基线 v0.15.17）。强制点以 `core/profile/` 现行实现为准。

Profile 是主引擎的用户数据模块，四类模型（配额 / 再生 / 库存 / 备注）共用**一条读入口和一条写入口**。
本文只讲 Agent 怎么操作它：选哪个调用、写一次会发生什么、哪些约束是强制哪些只能靠自觉。
字段定义、账本列与同步协议不在本文，见[Profile 数据字段模型](../../40-reference/03-profile/01-data-model.md)
与[Profile 共享模块](../../30-design/01-architecture/03-models/04-profile.md)。

## 1. 两条执行路径，同一条管线

| Agent 手里的权限 | 可用入口 |
|---|---|
| 在工作流里（DSL） | `profile_get` / `profile_set` / `profile_inc` / `profile_observe` / `profile_all` / `profile_model` / `profile_locked` / `profile_declare` / `profile_history_aggregate` / `profile_reject`，签名见 [DSL 函数表](../../40-reference/02-dsl/06-functions.md) |
| 能跑本机 Python | `lvjiang.core.profile.service` 的 `profile_read` / `profile_read_all` / `profile_action` / `profile_observe` / `profile_is_locked` |
| 只有 MCP | **没有 Profile 工具**（工具目录只有读装备、方案、调律、任务那批）。不要用 SQL 或脚本绕开，如实说明需要本机权限或让用户在界面录入 |

DSL 的 `profile_set` / `profile_inc` 都是 `profile_action` 的薄包装，行为与界面写入完全一致。
不要自己开 SQLite 连接写 `profile_entries` / `profile_history`：那样既不留账本，也不触发
`sync_targets` 与变更脚本。

## 2. 动作前先确认三件事

1. **key 归属哪个模型**：`profile_model(key)`；未定义返回空串。未定义的 key 写入直接返回 0，不会报错。
2. **该用户这个 key 是否锁定**：`profile_locked(key)`。锁定等于物理失效——读返回 `null`、写入被拒绝、
   周期不再重置。要区分「锁定」和「没数据」，只能靠这个调用，不能拿 `null` 当 0 用。
3. **定义里的上限与增量意图**：`cap` / `soft` / `decimal` / `increment_only`，从定义卡片或
   `config/session/profile.yaml` 读，不要猜。

缺定义时用 `profile_declare(model, key, definition)` 声明：幂等，已存在（任意模型）一律不改，
返回 `{ok, created, key, reason}`；**事务内不能创建定义**，要在任务开始前声明。

## 3. 读

`profile_read(user, key)`（DSL `profile_get(key[, username])`）返回的是**派生值**，不是库里的原始行：

| 模型 | 读到的值 |
|---|---|
| 配额 | 本周期进度；条目时间早于本周期边界就直接读作 0 |
| 再生 | 当前存量；realtime 按速率补、boundary 按准点补，封顶 `cap` |
| 库存 | 库里原值 |
| 备注 | `value_text` 文本 |

读取**绝不写库**：不落账本、不改 `event_at`、不触发同步。`profile_read_all` / `profile_all`
一次性拿某用户全部条目，用于批量核对。读别的用户不需要额外权限，写只作用于指定用户。

## 4. 写：按意图选一个，别混用

| 意图 | 调用 | 说明 |
|---|---|---|
| 记一次增减（花费、产出、+1） | `profile_action(user, key, delta=d)` / `profile_inc(key, d)` | 管线在同一事务里读当前值再加减，最不易丢更新 |
| 对齐到已知绝对值 | `profile_action(user, key, set_value=v)` / `profile_set(key, v)` | 内部换算成 `delta = v − 当前值`，后续管线完全一致 |
| OCR / OCR 类外部观测配额进度 | `profile_observe(key, v)` / `profile_observe(...)` | 仅配额；同周期内小于当前值会被拒绝，返回 `accepted=false` + 原因 |
| 备注文本 | `profile_set(key, "文本")` | 数字文本会取整并按硬上限截断；`0` / `false` / `null` 清空 |

备注模型**没有增量语义**：`profile_inc` 对备注只记一条警告并返回 0，要改文本必须用 `set`。

`source` 尽量显式传业务名（例如「扫描钱袋」）。账本、按来源累计额度和历史筛选都依赖它，
省略时落成「DSL 写入」，事后无法区分谁写的。

## 5. 写一次会发生什么

`profile_action` 内按模型把状态先固化、再写业务行，最后派发：

- 再生：确实有恢复量时先写一条 `regen` 行（恢复后的绝对值），再写业务 `set` 行。
- 配额：跨周期且库里不是 0 时先写一条 `reset` 行（`event_at` = 周期边界），再写业务行。
- 库存 / 备注：只有业务行。
- 之后：跑 `change_script`（`change_script_sync: true` 的在落盘前跑，否则进异步队列），
  再沿该 key 的 `sync_targets` 递归写目标 key。

所以「一次操作」在账本里可能是两行，这是设计要求（`new = old + delta` 的自洽），不是重复记账。
异步脚本失败只回滚它自己，不回滚已提交的父变更；同步脚本拒绝或失败会连父变更一起回滚。

## 6. 顺序：被联动的字段必须后写

`sync_targets` 每条边有方向（`both` 任意变动 / `pos` 仅增加 / `neg` 仅减少）、倍率 `ratio`
（可负、可小数）与 `lock_on_cap`。源字段写入成功后，目标字段会被按边改写。

操作规则：

- 要写「源字段 + 目标字段」时，**先写源、再写目标**，否则目标值会被随后的联动覆盖。
- 目标字段的最终值以你写的那次为准，但账本上会看到「联动一行 + 手工一行」，这属正常形态。
- 目标达到硬上限且该边 `lock_on_cap: true` 时，源字段会被锁成 `—` 并禁止编辑；
  派生计数器、真实资源这类场景用户通常会关掉该开关，动手前先确认这条边的配置。

## 7. 哪些约束是强制的

| 约束 | 强制点 |
|---|---|
| 下限 0 | 引擎强制：任何模型都 `max(0, 值)` |
| `cap` 硬上限 | 引擎强制截断；`soft: true` 只提醒不截断 |
| `increment_only` | **只在界面快捷操作生效**（负幅度不出现在候选里）；脚本与 `profile_action` 不校验，Agent 必须自己遵守定义意图，否则会把只增的配额写回去 |
| `decimal` | 只是界面输入校验；引擎不做小数位限制 |
| 备注数字取整 | 引擎强制（可转数字时取整 + 硬上限截断） |
| 锁定 key | 引擎强制：写入被拒，返回旧值 |

## 8. 并发、重跑与幂等

同进程内的写操作由一把 RLock + SQLite 事务串行；跨进程没有额外的用户锁，只有 SQLite 的
事务与 `busy_timeout`，因此：

- 「先读再按目标值写」的跨进程场景，用 `use_cas=True` + `expected_entry` 声明期望的库行，
  冲突会抛 `ProfileWriteConflict`，重读后重试，别硬写。
- 按 `delta` 写由管线在同一事务内算，一般不丢更新。
- **重跑不是幂等**：同一批 `delta` 再来一遍会再加一次。重跑前用账本判断已经落地到哪一步
  （`profile_history_aggregate` 按 `source` / 时间范围聚合，或在界面按来源筛历史），
  只补缺的那一步；不要整批重放。

## 9. 写完怎么自证

- 读回：`profile_read` 拿到派生值，和自己算的目标值比对。
- 聚合核对：`profile_history_aggregate(key, operation="sum", source=..., since=...)`，
  确认本次来源的累计增减与预期一致。
- 联动核对：被 `sync_targets` 牵连的目标 key 也要读一遍，别只看源字段。
- 报告时区分「已写入」和「拒绝/跳过」：`profile_observe` 的 `accepted`、`profile_declare` 的
  `ok` / `created`、`profile_locked` 的 `true` 都是要如实转达的结果，不要笼统说成功。

## 10. 不要做

- 不要直连 SQLite 改 `profile_entries` / `profile_history`，也不要手写账本行冒充历史。
- 不要在读取路径写库（历史上「每台设备每个 key 各刷一批 reset」就是这么来的）。
- 不要把派生值（周期归零后的 0、再生算出来的存量）当成库里的存储值回写。
- 不要在事务内创建定义；定义只能在任务开始前声明。
- 不要删历史、改来源来「让数字好看」，那会改变按来源累计的额度。
- 不要把真实用户名、账号、数值写进文档、提交信息或日志。
