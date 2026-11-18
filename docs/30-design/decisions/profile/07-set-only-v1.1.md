# Profile 同步 v1.1：set-only 数据模型与周期裁决（历史方案）

> 状态：已实现（2026-11-14，基线 v0.15.13）——历史方案，已被 [08-profile-sync.md](08-profile-sync.md)（v1.2）取代。本文保留为 v1.1
> 过程记录，不再继续打补丁；当前实现按 v1.2 文档执行。最后更新：2026-11-14（仅改版本名）。
>
> 云端唯一索引是部分索引（`op_id <> ''`），这样协议 1 的空 `op_id` 插入不会互相冲突。
>
> 本文档是 Profile 账本与同步协议的重构需求，替代此前「set/delta 双 op + 快照时间过滤」的
> 约定。旧数据通过**原地迁移**进入新语义：只加列、不新建表、不改表名。

## 1. 背景与问题

2026-11-12 手机端独立执行任务产生了 200 多条 Profile 记录，点击「立即同步」却被要求
「先清除本地数据」，清除后记录全部丢失。排查确认这不是单点 bug，而是旧模型的结构性问题：

1. **上传被快照阻塞**：本地有数据、但尚未落地过云端快照时，同步直接返回
   `pending_overwrite`，没有任何上传路径，只能引导用户删除本地数据。
2. **bootstrap 把未上传行标记成已上传**：落地快照后把 `local_history_id` 快进到本地最大
   history id，本地真实产生的记录永远不会再上传。
3. **按快照创建时间丢弃 op**：服务端把 `happened_at` 早于最新快照 `created_at` 的 op 当成
   「已包含在快照里」而跳过。另一个设备在 A 变更后刷新快照，会误杀本设备对 B 的变更。
4. **delta 语义放大了复杂度**：回放需要按周期做加法、旧周期 op 要丢弃、并发写要区分
   `set`/`delta`，任何一环判断错误都会造成静默覆盖。
5. **realtime regen 的时间戳不是真实时间**：为了把小数进度编码进整数 + 时间，写入时把
   `updated_at` 回拨（`normalize_realtime_write`），导致跨端 LWW 和 regen 锚点语义混乱。

结论：**放弃 delta，所有变更统一为绝对值的 `set`；周期变化改为读时函数；上传不设前置条件；
每个 key 只仲裁最新一次真实写入。**

## 2. 目标与非目标

### 2.1 目标

1. 新的同步操作只有绝对值 `set`。不再产生或回放 `delta`。旧 `delta` 行保留本地类型，
   若需要上传，体是当时的 `new_value`。
2. reset、regen 等周期变化是 `(value, event_at, 配置, now)` 的读时函数，不作为数据同步，
   也不写进账本。
3. 所有时间戳都是真实事件时间，不再回拨编码小数进度。
4. 同步建立后，任何一条本机真实写入都必须有机会上传；云端有更新数据、快照刷新、周期变化
   都不能阻止上传。绝对值写入在离线时也要先落库。
5. 待上传不看可能失真的 `local_history_id`。一条本机 `set`/`delta` 在 `remote_id IS NULL`
   且新于该 key 的快照候选时才上传；更旧的行标成放弃，避免补传整段旧账。
6. 每个 key 的当前值只由 `(event_at, op_id)` 仲裁；来源实例只作展示，不参与比较。
7. 手机端与 PC 端共用同一套模型与读时函数。周期告警仍只在 PC 的
   `ProfileEngine` 上根据读时函数发出。regen/reset 不写账本，因此不再触发
   `change_script`。手机不跑这套引擎。本地消耗当时落库，不要求先同步。
8. **原地升级**：本地与云端都只对现有表加列、改索引、回填数据，不新建表、不改表名；
   新增列压到最小：本地 3 列（`entries.op_id`、`history.op_id`、`history.remote_id`），
   云端 1 列（`history.op_id`）。默认迁移不碰 `accounts`。

### 2.2 非目标

- 不做多端并发消耗的加法合并（见 §9.1）。v1 接受「后写覆盖」。
- 不改文档通道（`users/*.json`、`profile.yaml`、头像、页面结构）的整文档覆盖语义。
- 不做网页端编辑，网页继续只读。
- 不引入 CRDT、向量时钟或服务端业务折叠。
- 不把 `ProfileProcessor` 的周期告警搬到手机（避免同一恢复触发两次）。
  周期 `change_script` 不再由引擎触发。

## 3. 术语

| 术语 | 含义 |
| --- | --- |
| entry | `profile_entries` 中某个 `(username, type, key)` 的当前物化状态 |
| op | `profile_history` 中的一次真实写入。新写入永远是绝对值 `set`；旧 `delta` 行保留原 `change_type` |
| snapshot | `profile_snapshot` 中某个 `remote_id` 水位上的全量 entries，只用于加速落地与清理 |
| `event_at` | 真实事件时间。本地复用 `profile_history.ts`、`profile_entries.updated_at`；云端复用 `happened_at`。格式固定为无时区的本地秒串 `YYYY-MM-DDTHH:MM:SS` |
| `remote_id` | 云端序号，或本地放弃标记。`NULL` = 尚未决定；正整数 = 云端 ack；`-1` = 本地判定不上传 |
| `remote_history_id` | 本机已应用到的云端账本水位，存在 `account.json` 的 `profile_sync` |
| 快照候选 | 某个 key 在最新快照条目里的 `(updated_at, op_id, value)`。没有该 key 时视为比任何真实时间都旧 |
| 待上传 | `device_id = 本机`、`remote_id IS NULL`、`change_type IN ('set','delta')`、`op_id` 不以 `unscoped:` 开头，且 `(ts, op_id)` 严格新于该 key 的快照候选 |

> 周期键不落库。quota 的周期由 `event_at` 与**当前** `profile.yaml` 在读时计算；
> 存储旧配置算出的周期键会在配置变更后压过真实时间，因此不设该列。

## 4. 核心模型

### 4.1 一条 op

```text
op = {
  op_id, username, type, key,
  value | value_text,        # 绝对值 / 文本
  event_at,                  # 真实事件时间
  source                     # 来源描述，仅用于历史展示
}
```

- 上传的 op 一律按 `set` 处理；仲裁只看 `event_at` 与 `op_id` 平局。
- `source` 只给历史显示，**不进入仲裁**；不再单独传 reason 字段。
- 来源实例（`device_id` / `instance_id`）留在账本行里做展示与迁移映射，不是 op 的仲裁字段。
- reset / regen 不产生 op。旧库的 `tick` / `reset` 只留在本地审计，不上传。

### 4.2 当前值函数（唯一读取口径）

| 模型 | 当前值 |
| --- | --- |
| stock | `value` |
| note | `value_text` |
| quota | `event_at` 落在当前周期 ? `value` : `0` |
| regen realtime | `min(cap, value + elapsed_seconds / period_seconds(unit) × regen_rate_value)` |
| regen boundary | `min(cap, value + count_boundaries(event_at, now) × amount)` |

- 配额的跨周期读值就是 0，与今天 `ProfileEngine` 把配额重置到 0 一致，不另存 base 列。
- realtime 的单位与 `compute_realtime_value` 相同：`period_seconds` 把 `minute` / `hour` /
  `day` / `week` 换成秒，再乘 `regen_rate_value`。不能把 `regen_rate_value` 直接当成每秒速率。
- 周期归属在读取时用 `event_at` + 当前 `profile.yaml` 现算，不读取存储的周期键。
- 页面、工作流、告警、Web 展示使用同一公式。Web 与客户端各有一份实现，用同一组
  输入输出向量锁定，不为此共享运行时代码。
- `value` 列是 REAL：realtime regen 直接存精确小数，不再「整数入库 + 回拨时间」。

### 4.3 派生值不落账本

| 写入类型 | 是否产生 op | 是否上传 | 说明 |
| --- | --- | --- | --- |
| `profile_set` / 单元格 / `profile_observe` | 是 | 是 | 绝对值，离线也落库 |
| `undo` | 是 | 是 | 把账本行的 `old_value`（note 用 `old_value_text`）写成新的绝对值 |
| `profile_inc` / 消耗 | 是（绝对值） | 是 | 读当前值 → 加增量 → clamp → 写绝对 set。当时落库，不要求先同步 |
| quota reset | 否 | 否 | 读时函数隐含；旧库里的 `reset` 行只作审计 |
| regen realtime / boundary | 否 | 否 | 读时函数计算；旧库里的 `tick` 行只作审计 |

新写入不再产生 `delta`。旧库已有的 `delta` 行**不改** `change_type`，历史仍能看出它是增量；
若它新于快照候选，上传体取当时的 `new_value`。`tick` / `reset` 不上传。
上表的「是否上传」说的是新写入有没有上传资格；迁移存量还要满足 §3 的待上传条件。

### 4.4 本地写入不等人同步

消耗、绝对值编辑、观测和撤销都在发生时写入本地账本。是否已经登录、这次进程有没有完成拉取，都不参与这次写入。同步只合并已经记下的行。

`ProfileEngine` 不把 regen / quota 的推算结果写成 `set` 或 `reset`。展示和告警直接用读时函数。

## 5. 本地数据库结构（原地升级）

现有两张表不变名、不重建，只加列并改变部分列的语义。

### 5.1 `profile_entries`

保留现有列，新增一列：

```sql
ALTER TABLE profile_entries ADD COLUMN op_id TEXT NOT NULL DEFAULT '';
```

语义映射（逐列审计）：

| 现有列 | 新用途 | 处理 |
| --- | --- | --- |
| `username` / `type` / `key` | 主键 | 保留 |
| `value` | 绝对值 | 保留 |
| `value_text` | note 文本 | 保留 |
| `updated_at` | `event_at` | 保留列名，迁移时改写为真实事件时间 |
| `updated_time` | 本机落库时间 | 保留，仅诊断 |
| `locked` | 本机锁 | 保留，不同步 |
| （新增）`op_id` | 胜出 op 身份 | `ALTER TABLE ADD COLUMN` |

- entries **不记录 origin**：来源实例/本地序号只留在账本行里做展示与迁移，不参与当前值仲裁。
- `locked` 永远是本机语义：快照和远端 op 都不能改它。
- 快照/op 落地必须用 `UPDATE` 只更新 `value/value_text/updated_at/op_id`，
  **不得整行 REPLACE**，否则会把 `locked` 清掉。

### 5.2 `profile_history`（沿用现有账本表）

保留现有列，新增两列：

```sql
ALTER TABLE profile_history ADD COLUMN op_id TEXT NOT NULL DEFAULT '';
ALTER TABLE profile_history ADD COLUMN remote_id INTEGER;
```

语义映射（逐列审计）：

| 现有列 | 新用途 | 处理 |
| --- | --- | --- |
| `id` | 本地顺序 / 扫描游标 | 保留 |
| `ts` | `event_at` | 保留。本机写入的 `ts` 已是墙钟（`history_ts or write_ts`），不是回拨锚点 |
| `username` / `type` / `key` | 主键 | 保留 |
| `old_value` | 历史展示 / undo | 保留 |
| `new_value` | 绝对值 | 保留。旧 `delta` 行的 `new_value` 就是当时写入后的绝对值 |
| `change_type` | 本地变更类型 | 保留。`delta` 不改成 `set`；旧 `tick`/`reset` 只作审计 |
| `source` | 来源描述 | 保留，仅展示 |
| `old_value_text` | 历史展示 / undo | 保留 |
| `new_value_text` | `value_text` | 保留 |
| `delta_value` | 历史展示 | 保留，不参与同步 |
| `sync_from` | 历史展示 | 保留，不参与同步 |
| `device_id` | 来源实例（展示/迁移） | 保留，不参与仲裁 |
| `origin_local_id` | 旧 op 身份 | 保留。云端 `local_id` 对得上的是它，不是本库自增 `id` |
| （新增）`op_id` | 同步幂等键 / 平局身份 | `ALTER TABLE ADD COLUMN` |
| （新增）`remote_id` | ack 或放弃标记 | `NULL` 未决定；正整数已 ack；`-1` 不上传 |

索引在 `op_id` 回填完成之后再建，见 §8.1。回填前所有行的 `op_id` 都是空串，
此时建唯一索引会失败。

```sql
DROP INDEX IF EXISTS idx_history_origin;
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_op_id ON profile_history(op_id);
CREATE INDEX IF NOT EXISTS idx_history_pending
    ON profile_history(device_id, id)
    WHERE remote_id IS NULL AND change_type IN ('set', 'delta');
```

- 账本本身就是 outbox，不维护第二份事件流。
- `op_id` 使用 uuid，不用 `(instance, local_id)`：DB 被离线包替换、本地 id 回退都不会撞身份。
  旧的 `idx_history_origin` 在回填并建好 `op_id` 唯一索引之后删除。
- 写入与回放的幂等按 `op_id`：`INSERT ... ON CONFLICT(op_id) DO NOTHING`。
  新行必须写入非空 uuid，不能依赖默认空串。
- 已 ack 的行写入服务端返回的正整数 `remote_id`。重复上传返回已有 `remote_id`，客户端照写。
- 被拒绝或不新于快照候选的行写 `remote_id = -1`。待上传查询只认 `NULL`，因此 `-1` 不会堵在队首。
  每次推送开始时，本机 `set`/`delta` 里 `remote_id = -1` 且不是 `unscoped:` 的行先改回 `NULL`，
  再按当前快照候选重判。时间规则或候选变了，原先误放弃的行还能上传；仍然不新于候选的行
  再次标 `-1`，这一步不访问网络。
- `local_history_id` 不再参与待上传判定，也不再作为扫描下限。

### 5.3 同步状态

不新建 `sync_state` 表，继续落在 `account.json` 的 `profile_sync`：

```json
{
  "profile_sync": {
    "remote_history_id": 0,
    "profile_snapshot_id": 0,
    "snapshot_remote_id": 0
  }
}
```

- `local_history_id` 可以留在文件里作诊断，同步逻辑不读它。
- 定义摘要继续由定义同步自己维护；库结构版本继续用现有 `schema_version` 表
  （下一个版本号是 11）。
- 待上传集合以 `profile_history.remote_id` 为准。`account.json` 损坏时，仍能扫出
  `remote_id IS NULL` 的行，再按快照候选决定上传或标 `-1`。

## 6. 云端结构（原地升级）

### 6.1 `profile_history`（沿用现有账本表）

新增一列，**这一步不建 `op_id` 唯一索引，也不删除旧索引**：

```sql
ALTER TABLE profile_history ADD COLUMN op_id TEXT NOT NULL DEFAULT '';
```

逐列审计：

| 现有列 | 新用途 | 处理 |
| --- | --- | --- |
| `id` | 行号 | 保留 |
| `account_id` | 账户 | 保留 |
| `remote_id` | 拉取水位 / ack 序号 | 保留 |
| `instance_id` | 来源实例（展示） | 保留，不参与仲裁 |
| `local_id` | 写入方当时的 `history.id` | 保留到回填结束。它等于各端账本上的 `origin_local_id`，不等于回放端的自增 `id` |
| `username` / `type` / `key` | 主键 | 保留 |
| `op` | `set` / 历史 `delta` | 保留；协议 2 之后新行只写 `set` |
| `value` | 绝对值 | 保留 |
| `delta` | 历史 delta 审计 | 保留，新行为 NULL |
| `value_text` | note 文本 | 保留 |
| `happened_at` | `event_at` | 保留，仍是无时区本地秒串 |
| `created_at` | 服务端接收时间 | 保留。不参与仲裁，也不写进快照条目 |
| `source` | 来源展示 | 保留，不参与仲裁 |
| （新增）`op_id` | 同步幂等键 | 先加列，回填后再建唯一索引 |

回填与索引切换见 §8.3。`0010` 先建 `op_id <> ''` 的部分唯一索引。`0011` 把空 `op_id` 回填成 `legacy:<instance_id>:<local_id>`，再删除 `profile_history_idempotent`。协议保持 2，不退回 1。

- 旧行：`op_id = 'legacy:' || instance_id || ':' || local_id`。
- 新 pull 只取 `op='set'`。旧 `delta` 留在表里作审计，不回放；它们对当前值的影响只通过
  §8.2 的一次性归约进入新快照。

### 6.2 `profile_snapshot`（沿用现有单数表）

表结构不变。条目继续使用现有字段名，只多一个 `op_id`：

```json
{
  "username": "...",
  "type": "quota",
  "key": "...",
  "value": 12.5,
  "value_text": "",
  "updated_at": "2026-11-12T12:00:00",
  "op_id": "winning-op-uuid-or-legacy-id"
}
```

- `type`、`updated_at` 与今天的 `_snapshot_entries()` 和 `0001_init.sql` 注释一致，不改成
  `model` / `event_at`。
- `updated_at` 是该 key 胜出写入的真实事件时间，不是快照行的 `created_at`，也不带时区偏移。
- `op_id` 是胜出 op 的身份。只存在于旧快照、没有账本行的条目沿用条目里的 `updated_at`，
  `op_id` 留空；空 `op_id` 在平局时输给任何非空值。

### 6.3 `accounts`（默认不加列）

默认方案是**部署时一次性脚本**完成云端迁移，协议版本用 Worker 常量
`PROTOCOL_VERSION`。因此 `accounts` 不需要新增任何列。

只有在明确要求「首次 v2 访问时惰性迁移」时，才需要以下三列（可选）：

```sql
ALTER TABLE accounts ADD COLUMN profile_protocol_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE accounts ADD COLUMN profile_migration_state TEXT NOT NULL DEFAULT '';
ALTER TABLE accounts ADD COLUMN profile_migration_cursor INTEGER NOT NULL DEFAULT 0;
```

- `profile_protocol_version`：1 = 旧协议，2 = set-only。
- `profile_migration_state`：`''` / `running` / `done`，用租约防止并发重复迁移。
- `profile_migration_cursor`：已归约到的旧账本 `remote_id`，用于崩溃续跑。
- 惰性方案也要遵守 §8.3 的索引顺序：回填完成前不能建 `op_id` 唯一索引。

### 6.4 服务端职责

协议 2 的服务端只做这些事：

1. 字段校验。`event_at` 先收成无时区本地秒串。过去的真实时间收下。无法解析，或比服务器时间超前超过 2 天，按条拒绝；
2. 按 `op_id` 幂等去重。插入使用 `ON CONFLICT DO NOTHING`，因此旧的 `(account_id, instance_id, local_id)` 冲突也返回已有行，不让整批 503。命中的旧行若 `op_id` 仍为空，补上这次的 `op_id`；
3. 为成功入库的 op 分配账户内单调 `remote_id`；
4. 入库前把 `happened_at` 收成 `YYYY-MM-DDTHH:MM:SS`。只去掉偏移、小数和空格，不换时区，也不改成服务器当前时间；
5. **按条**返回 `{op_id, remote_id, status}`。`status` 为 `accepted`、`duplicate` 或 `rejected`。
   一条被拒绝不让整批失败。

`rejected` 时 `remote_id` 为空，响应里带 `invalid_event_at`。客户端把该行写成
`remote_id = -1`，界面提示校准时间，队列继续上传后面的行。下一轮推送会把这类 `-1` 放回待决定再判。

明确不做：快照前置校验、`base_remote_id` 校验、按 `happened_at` 过滤、按 key 仲裁、
改写 `event_at`。`profile_definition`、`profile_layout` 两套通道保持不变。

`PROTOCOL_VERSION` 变成 2 的同一次部署拒绝新的 `op='delta'`。不存在「版本已经是 2、delta
仍可写入」的窗口；那种行不会被新 pull 读到，消耗会丢。

`/api/sync/state` 增加 `protocol_version`（取 Worker 常量；只有采用惰性迁移时才读
`accounts.profile_protocol_version`）。

## 7. 操作说明

### 7.1 写入

| 入口 | 行为 |
| --- | --- |
| `profile_set` / 单元格编辑 / `profile_observe` | 直接写绝对值 |
| `profile_inc` / 消耗 | 读当前值（函数）→ 加增量 → clamp → 写绝对值 |
| `undo` | 读该行 `old_value` / `old_value_text`，写成新的绝对值 |
| reset / regen | 不写 canonical、不产生 op。引擎不再写回 |

- `event_at = now`（真实墙钟），写入 `ts`，格式见 §3。
- trigger / 告警仍按 `old_value` / `new_value` / 展示用 `delta_value` 在本地触发。
- 本地写入与 entry 更新在同一事务内完成。

### 7.2 读取

统一调用 §4.2 的函数。禁止在 UI、工作流、同步模块中各自实现另一套 regen/reset 公式。

### 7.3 合并与仲裁

对每条候选值（拉取的 op、快照条目、本地已有 entry）：

```text
if entry.locked: 只记历史，不改 entry
比较 (event_at, op_id)
incoming 更大 → 覆盖 entry 的 value / value_text / updated_at / op_id
否则 → 只写历史，不改 entry
```

时间比较把两边解析成无时区的本地钟面，**不按字符串比较**。存储格式只有
`YYYY-MM-DDTHH:MM:SS` 一种；不写 `Z` 或 `+08:00`。带偏移的字符串和同钟面的无偏移字符串
按字典序不是同一个时刻。

| 情形 | 裁决 |
| --- | --- |
| 同一 key 多条 set | `(event_at, op_id)` 最大者准 |
| 跨周期 | 不单独比较周期；更晚的 `event_at` 天然落在同一或更晚周期。读时函数决定是否显示 0 |
| 旧周期 / 旧时间 op 迟到 | 接收、入库、保留历史；不改变当前值；**不得在上传端拒绝** |
| `event_at` 完全相同 | `op_id` 字典序；空 `op_id` 输给任何非空 `op_id` |
| 派生值与真实 set | 真实 set 永远优先；派生值不落账本 |
| 同周期两端都有真实 set 且互不可见 | 上述规则取最新；接受一方被覆盖 |

**锁定语义**：

- 锁定时不应用任何 op，但 op 仍入库；
- 解锁时按同一仲裁规则，用本地账本中该 key 的最新 op 重建 entry；
- 快照落地与 op 应用都不得修改 `locked` 列。

### 7.4 同步

```text
pull:  取 op='set' 且 remote_id > remote_history_id 的行（分页）→ merge → 水位推进
push:  取 §3 的待上传行，200 条一批
       服务端按条返回 accepted / duplicate / rejected
       accepted 与 duplicate 写入返回的 remote_id
       rejected 写入 -1，不重试该行
```

- 先拉后推。推送前先记下每个 key 的快照候选，再决定哪些本地行上传、哪些标 `-1`。
  比较对象是快照候选，不是合并完本地更新值之后的 entry；否则刚胜出的那条会因为
  「不大于自己」而漏传。云端还没有快照时候选为空，本机 `set`/`delta` 全部上传；
  这是第一份数据。PC 随后建快照，这些行就落在快照水位之前，可以按 §9.4 清理。
- 没有 `stale_base`、没有 `snapshot_required`、没有 pre-snapshot 跳过。
- 网络失败：不改这些行的 `remote_id`，下一轮重试。绝不引导用户清除本地数据。
- 被仲裁淘汰的 op 只要已经入库，也写入 `remote_id`，不重复上传。
- 手机与 PC 使用同一套逻辑，不再区分「手机只拉不推」。

### 7.5 快照

- 由 PC 创建（手机不建快照）。
- 创建前先 pull 到 head，`snapshot_remote_id = head`。
- 快照条目是每个 key 的一个候选值，与本地 entry、未上传 op、拉取到的 op 走 §7.3。
  不得因为「这个 key 有未上传 op」就跳过它。
- 条目的 `updated_at` 是胜出写入的事件时间。创建快照时从 entry 抄 `updated_at` 和 `op_id`，
  不改成快照行的 `created_at`。
- 首次 v2 同步，或本地 `profile_snapshot_id` 与云端最新快照不一致，或
  `remote_history_id < 最新快照的 snapshot_remote_id` 时，先做一次 per-key 合并，
  再把 `remote_history_id` 推进到快照水位。
- 合并快照不修改 `locked`，只更新 `value` / `value_text` / `updated_at` / `op_id`。
- 水位已经不低于快照水位、且本地 `profile_snapshot_id` 就是这份快照时，按自己的水位回放，
  不再落地一次。
- 快照不决定「某条 op 该不该收」。服务端收下能解析、且不是明显未来时间的 `set`。

### 7.6 周期引擎

- 周期告警仍只在 PC 的 `ProfileEngine` 上运行，依据是读时函数，不写账本。
  手机不跑 ticker。周期 `change_script` 不再触发：没有 reset/regen 写入，
  就不存在一次「周期变化」事件去挂钩脚本。
- 引擎不再把 regen 物化或 quota 重置写进账本。展示和 `profile_inc` 用 §4.2。
- `profile_inc` 按 §4.4 当时落库。

## 8. 迁移

### 8.1 本地原地迁移（schema v11）

不新建表、不改表名。`op_id` 唯一索引放在回填之后：

1. 备份 `profile.db` 到 `.before-v11.db`。用 SQLite backup API 或先做 WAL checkpoint，
   再复制 `profile.db` 与 `account.json` 到 `config/session/_migration-backup-<时间戳>/`。
   直接拷贝尚未 checkpoint 的 db 文件会漏掉 WAL 里的提交。
2. 加列：
   ```sql
   ALTER TABLE profile_entries ADD COLUMN op_id TEXT NOT NULL DEFAULT '';
   ALTER TABLE profile_history ADD COLUMN op_id TEXT NOT NULL DEFAULT '';
   ALTER TABLE profile_history ADD COLUMN remote_id INTEGER;
   ```
3. 回填 `op_id`。云端 `local_id` 是写入方的 `history.id`，各端对应列是 `origin_local_id`。
   回放行的本库自增 `id` 是另一套编号，用它会和对方另一条 op 撞车，拉取时被
   `ON CONFLICT(op_id)` 丢掉。
   ```sql
   UPDATE profile_history
      SET op_id = 'legacy:' || device_id || ':' || origin_local_id
    WHERE op_id = ''
      AND device_id <> ''
      AND origin_local_id IS NOT NULL;
   UPDATE profile_history
      SET op_id = 'unscoped:' || id
    WHERE op_id = '';
   ```
   `unscoped:` 行来源不明，不上传，但参与本地 entry 归约：它们仍可能是这个库里
   该 key 最新的绝对值。
4. **不**把 `delta` 改成 `set`。`tick` / `reset` 保持原样。
5. 确认没有空 `op_id` 之后调整索引：
   ```sql
   DROP INDEX IF EXISTS idx_history_origin;
   CREATE UNIQUE INDEX IF NOT EXISTS idx_history_op_id ON profile_history(op_id);
   CREATE INDEX IF NOT EXISTS idx_history_pending
       ON profile_history(device_id, id)
       WHERE remote_id IS NULL AND change_type IN ('set', 'delta');
   ```
6. 迁移 entries。先抄下每个 entry 的 `value` 和 `updated_at`（回拨锚点），再决定胜者。
   空 `op_id` 的 entry 输给任何账本行，因此胜者几乎总是账本行；**不能**把 entry 上的回拨
   时间当成事件时间，也不能先改写 `updated_at` 再拿它做减法。
   - 胜者 = 该 key 的账本行里 `(ts, op_id)` 最大的 `set` 或 `delta`。没有这样的行时保留 entry。
   - 非 realtime：`value` 取胜者的 `new_value`，`updated_at` 取胜者的 `ts`，`op_id` 取胜者的 `op_id`。
   - realtime：只有胜者 `new_value` 与迁移前 `entry.value` 一致时，才用迁移前的锚点还原小数：
     ```text
     elapsed_seconds = 胜者.ts − 迁移前 entry.updated_at
     exact = entry.value + elapsed_seconds / period_seconds(unit) × regen_rate_value
     exact = min(cap, exact)          # 无 cap 则不截断
     ```
     然后写 `value = exact`、`updated_at = 胜者.ts`、`op_id = 胜者.op_id`。
     两者不一致时，说明锚点不属于这次写入：直接取胜者的 `new_value` 和 `ts`，不要把回拨间隔加到另一个数上。
   - `locked` 原样保留。
7. 此时所有行的 `remote_id` 仍是 `NULL`。不在迁移事务里猜测哪些已经上过云；
   第一次同步按 §7.4 与服务端对账。不新于快照候选的行标 `-1`，不会因为旧
   `local_history_id` 被抬高而永久跳过，也不会整段推上去。
8. 旧行不删除。

### 8.2 云端基线（一次性归约）

不新建表、不改表名。这一步在 §8.3 的停写窗口里做，并且当时 `op_id` 已经回填完毕。

对每个账户：

1. 读最新旧快照，作为每个 key 的起点：`value` / `value_text` 用条目原值，
   `updated_at` 用条目自己的 `updated_at`。条目没有 `updated_at` 时才用快照行的 `created_at`。
   `op_id` 为空。
2. 只重放 `remote_id > 该快照 snapshot_remote_id` 的旧账本行。排序键是
   `(happened_at, op_id)`，**不是** `remote_id`。
   - `set`：该 key 的值改为 `value`；
   - `delta`：在当前归约值上加 `delta`。云端 delta 没有绝对值，这一次加法只用于做出基线，
     客户端以后不再回放 delta。
3. 某个 key 被上述行碰过之后，条目的 `updated_at` 改为最后应用到它的那条 op 的
   `happened_at`，`op_id` 改为那条 op 的 `op_id`。没被碰过的 key 保持第 1 步的时间。
4. 把结果插入 `profile_snapshot`，`snapshot_remote_id = 当前 head`。条目格式见 §6.2。
   新快照行自己的 `created_at` 只表示这份快照何时生成，**不写进任何条目**。
5. 不改旧账本行。旧 `delta` 仍是 `op='delta'`。

归约函数在 `server/src/profile_baseline.mjs`。对指定 SQLite 文件执行：

```bash
node scripts/one_off/profile_v2_baseline.mjs <sqlite-path>
```

最新快照的条目已经带 `op_id` 字段时跳过。线上协议已经是 2，脚本不改协议版本。

到达序会算错。例如起点 10，`set 15 @ 12:00` 先入库，`delta +5 @ 11:00` 后入库：按
`remote_id` 得到 20，按事件时间得到 15。手机上 12:30 的未上传写入必须和 12:00 比，而不是和
迁移脚本的运行时间比。

### 8.3 上线顺序与协议门

1. **加列部署**：云端只执行 §6.1 的 `ADD COLUMN`。不建 `op_id` 唯一索引，不删
   `profile_history_idempotent`。`PROTOCOL_VERSION` 仍为 1。旧 Worker 不写 `op_id` 也能插入。
2. 客户端可以随这个部署升级。读到 `protocol_version != 2` 时停止 Profile 同步并提示
   「服务端尚未升级」，不把新 op 送进旧接口。本地 schema v11 可以先做完。
3. **低峰停写** Profile 账本（推送与回放都停）。然后：
   - 回填 `op_id = 'legacy:' || instance_id || ':' || local_id`；
   - 确认该表没有空 `op_id`；
   - 创建 `profile_history_op_id`，再删除 `profile_history_idempotent`；
   - 按 §8.2 生成新基线快照。脚本可重跑：已经有 v2 基线快照（条目含 `op_id` 字段）的账户跳过。
4. **同一次部署**把 `PROTOCOL_VERSION` 置 2，并开始拒绝 `op='delta'`。然后恢复写入。
   不允许先开协议 2、稍后再拒 delta。
5. 客户端看到版本 2 之后，按 §8.5.3 做快照合并和补传。

线上协议已经是 2，不再退回 1。索引删除由迁移 `0011` 完成，基线归约由上面的脚本完成。没有这份基线的账户，新设备只会落到旧快照，水位之后的旧 `delta` 不会被 pull 读到。

### 8.4 回滚

- 保留 `.before-v11.db` 与 `_migration-backup-*`；云端旧行不删除。
- 回滚时切回旧版本并恢复备份。旧版本不认识新列，SQLite / D1 读取旧列时忽略多余列。
- 协议门保证版本仍为 1 时，新客户端不会写入。版本已经到 2 之后若要回滚服务端，按发布失败处理，
  不在本文展开。

### 8.5 自动迁移执行模型

迁移不依赖用户手动点按钮。云端归约在停写窗口里由脚本完成，不在请求路径里做。

#### 8.5.1 客户端：启动时自动迁移

1. **触发**：升级后首次 `get_profile_db()`。沿用现有迁移框架，新增
   `(11, "profile set-only in-place", _migrate_v11)`。
2. **备份**：见 §8.1 第 1 步。
3. **执行**：在 `_ensure_schema` 的同一事务内完成加列、回填和索引。
4. **幂等**：`schema_version` 只前进一次。回填语句带 `WHERE op_id=''`。
5. **失败**：事务回滚，`schema_version` 不前进。提示迁移失败和备份路径，下次启动重试。
6. **同步状态**：继续用 `account.json` 的 `profile_sync`。不读 `local_history_id`。
7. **用户可见**：迁移期间显示「正在迁移本地总览数据」。完成后显示「待核对的本地记录 N 条」，
   N 是 `remote_id IS NULL` 且 `change_type IN ('set','delta')` 的本机行数。
   第一次同步把其中不新于快照的标成 `-1`，真正上传的数量可以更小。

#### 8.5.2 服务端

执行模型就是 §8.3。默认不做请求内惰性迁移。若单独批准惰性方案，仍要先停写该账户、
回填、建索引、生成快照，再把该账户标成协议 2；建索引前不能接受会写出空 `op_id` 的插入。

#### 8.5.3 首次同步的自动收敛

1. 读 `protocol_version`。不是 2 就停止 Profile 同步并提示，不向旧接口发送 op。
2. 若本地 `profile_snapshot_id` 与云端最新快照不一致，或
   `remote_history_id < 最新快照的 snapshot_remote_id`，按 §7.5 合并快照条目，
   把水位推进到快照水位。合并前记下每个 key 的快照候选。
3. 拉取快照水位之后的 `op='set'`，逐条 merge。
4. 用第 2 步记下的快照候选划分本机 `remote_id IS NULL` 的 `set`/`delta`：
   - `(ts, op_id)` 严格大于候选：进入待上传。`delta` 上传体是 `op=set`、`value=new_value`；
   - 其余：`remote_id = -1`；
   - `unscoped:` 与 `tick` / `reset`：不上传。`tick` / `reset` 保持 `remote_id NULL` 即可，
     待上传条件本来就不包含它们。
5. 待上传按 200 条一批推送。`accepted` 与 `duplicate` 写入返回的 `remote_id`；
   `rejected` 写入 `-1`。不设 1000 条上限，也不因队首一条坏时间停掉整批。
6. 任一步网络失败只留下尚未写上 `remote_id` 的行，下一轮重试。

#### 8.5.4 边界情况

| 情况 | 行为 |
| --- | --- |
| 升级时离线 | 本地迁移照常完成。消耗和绝对值写入照常落库 |
| 未登录 | 本地迁移照常完成。消耗和绝对值写入照常落库 |
| 迁移中崩溃 | 本地事务回滚。云端脚本在停写窗口内可重跑，已有 v2 基线的账户跳过。备份保留 |
| 重复启动 | `schema_version` 与云端「已有 v2 基线则跳过」防止做第二次归约 |
| 手机与 PC 先后升级 | 各自做本地迁移。服务端基线只有一份。后同步的一端按快照候选仲裁，新于候选的本地行再上传 |
| 服务端还是 v1 | 客户端停止 Profile 同步，不向旧接口发 op |
| 旧 `local_history_id` 被抬高 | 不参与判定。新于快照候选的本机行仍会上传；更旧的行标 `-1` |
| 队列中有一条非法时间 | 该行 `rejected` 后标 `-1`，同批其余行继续 |

## 9. 风险与取舍

### 9.1 并发消耗仍会后写覆盖

```text
周期内初值 10
设备 A 离线消耗 1 → set 9 @ 10:00
设备 B 离线消耗 1 → set 9 @ 10:05
LWW 结果 = 9，而不是 8
```

这是 set-only 的固有限制。长期离线由 `event_at` 的周期归属隔离；同一周期内两端都做真实
消耗且互相不可见时，单值 set 必丢一边。若未来要求无损，只能在 entry/op 中增加
`contributions: {instance: 累计值}` 的每设备累计 map（仍是绝对值，不是 delta）。v1 不做。

### 9.2 时钟可信度

`event_at` 是客户端墙钟。过去的真实时间都收下，包括几天前的补传。只拒绝无法解析的串，以及比服务器时间超前超过 2 天的串。拒绝时不把钟面改成服务器当前时间：改写会移动 regen 锚点，使 `value` 和事件时间不再是同一次写入。客户端把该行标 `-1` 并提示校准时间。下一轮同步先把本机这些 `-1` 放回待决定；规则放宽后，原先被误拒的行会重新上传。

### 9.3 历史展示

`old_value` / `delta_value` 只是展示字段。旧 `delta` 上传成绝对值之后，不保证别的设备上
`new = old + delta_value`。历史 UI 按「独立 set 事件 + 来源 + 时间」展示。
本地 `change_type='delta'` 仍表示这条旧记录当时是增量。

### 9.4 账本增长与清理

- 云端与本地保留 `remote_id > 最新快照 snapshot_remote_id` 的全部 op。
- 更早的 op 可以按 90 天清理。水位低于快照的设备用快照候选重新落地，再拉之后的 `set`。
- 本地 `remote_id IS NULL` 的待上传行不清理。`remote_id = -1` 的行是本地历史，按本地保留期处理，不上传。
- 补传只把新于快照候选的行放进快照水位之后，因此一次迁移不会把多年旧账变成删不掉的新尾巴。

### 9.5 迁移近似

- realtime 的小数在回拨过的 `updated_at` 里，账本 `ts` 是写入墙钟。§8.1 在改写条目前用这两个数还原，单位与 `compute_realtime_value` 相同。
- 云端旧 `delta` 没有绝对值。§8.2 按事件时间加到上一个快照起点上，只做这一次。
  这不是运行时规则，也不能改成按 `remote_id` 到达序加。
- 旧快照条目已有 `updated_at` 时沿用它。只有字段缺失才用快照行 `created_at`。
- 归约值与某台设备当时的本地值可能差一次并发 delta。两端 rebase 之后都以这份基线为准；
  仍新于基线的本地绝对值会再上传并按 §7.3 仲裁。

## 10. 验收标准

1. **原地升级**：本地与云端只做 `ALTER TABLE`、回填和索引调整，不新建表、不改表名。
   新增列只有本地 3 列与云端 1 列。`accounts` 默认不加列。`op_id` 唯一索引在回填之后创建。
2. **协议 1 期间旧客户端仍能推送**：加列之后、停写之前，旧 `INSERT` 不写 `op_id` 也能成功，
   `(account_id, instance_id, local_id)` 仍唯一。
3. **本机新记录可上传**：手机离线产生 N 条绝对值写入，云端已有另一台设备的快照，且这 N 条的
   事件时间新于对应 key 的快照候选。首次同步后这 N 条都取得正整数 `remote_id`，PC 能拉到。
4. **旧水位不冻结、旧账不灌入**：`local_history_id` 即使已经等于最大 history id，新于快照候选的
   本机 `set`/`delta` 仍会上传。不新于候选的行变成 `remote_id = -1`，云端 `remote_id` 不因此新增尾巴。
5. **快照时间不误杀**：快照条目的 `updated_at` 是胜出写入的事件时间。手机对另一个 key 的更晚写入
   仍被接收。迁移脚本的运行时间和快照行 `created_at` 不写进条目。
6. **归约顺序**：§8.2 的例子按事件时间得到 15，不会因为 `delta` 后入库而得到 20。
7. **快照参与仲裁**：本地较旧的未上传 op 不覆盖更新的快照值，并标 `-1`。本地较新 op 保留，
   上传后在其他设备上赢过该快照候选。
8. **清理后可恢复**：本地水位低于最新快照水位时，按 per-key 仲裁落地快照，再回放之后的 `set`。
9. **不阻塞上传**：无快照、水位落后、云端已有更新时，新于快照候选的本机写入仍可上传。
   不新于候选的行标 `-1`，留在本地，不进云端。这是有意的：候选已经更新，再把更旧的
   绝对值推上去只增加账本尾巴，不改变当前值。不出现「先清除本地数据」。
10. **真实时间戳**：realtime 迁移后 `updated_at` 是胜出写入的墙钟，`value` 含迁移前锚点还原出的小数。
    同一锚点上两端当前值的浮点误差小于 `1e-6`。
11. **消耗当时落库**：已登录且本次进程尚未 pull 时，`profile_inc` 仍写入一条绝对值 `set`。
    `profile_set`、`profile_observe`、单元格编辑和 `undo` 同样不依赖同步。
12. **锁定语义**：锁定期间的 op 入库但不改 entry，也不改 `locked`。解锁后按最新 op 重建 entry。
13. **幂等与坏行**：同一 `op_id` 重复上传得到 `duplicate` 和已有 `remote_id`，账本只有一行。
    一批里的一条 `invalid_event_at` 只把该行标成 `-1`，其余行继续。过去的合法时间不是非法时间。
    下一轮同步会重判已标 `-1` 的本机 `set`/`delta`。
14. **协议门**：`protocol_version != 2` 时客户端不向旧接口发 op。版本变为 2 的部署同时拒绝新的 `delta`。
15. **身份回填**：回放行的 `op_id` 是 `legacy:<device_id>:<origin_local_id>`，与云端
    `legacy:<instance_id>:<local_id>` 相同。拉取已存在的 op 不插入第二行。
16. **快照字段名**：新快照条目使用 `type` 与 `updated_at`，并带 `op_id`。时间串没有时区偏移。
17. **delta 退场**：新写入不再产生 `delta`。本地旧 `delta` 仍显示为增量；上传体是当时的 `new_value`。

## 11. 相关文档

- [用户 Profile 系统需求总览](../../../20-plans/profile/README.md)（仅源码检出）
- [页面结构与搬运](06-overview-layout-sync.md)
- 账户与同步架构（历史背景）：独立 admin 仓库 `docs/30-design/architecture/07-account-and-sync.md`（不随主工程发布）
- [公共服务与桌面适配](../../architecture/platform/11-background-services.md)
