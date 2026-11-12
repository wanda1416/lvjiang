# Profile v2 重构：本地/云端两套表与 v2 接口

> 状态：规划中（未实施）。最后更新：2026-11-13。
>
> 本文档取代 [07-set-only-sync.md](07-set-only-sync.md) 的 v1.5 同步方案。v1.5 保留为历史记录，
> 不再继续打补丁；新服务端、新客户端直接使用本文的 v2 表与 v2 接口，v1 表与 v1 接口保持原样，
> 不主动停用，也不做双写。

## 1. 为什么要重构

v1/v1.5 的问题不是某一个字段缺失，而是**同一份数据有三套表示、两个写者**：

- 本地 `profile_entries`（当前值）
- 本地 `profile_history`（审计账本）
- 云端 `profile_history`（同步账本）+ `profile_snapshot`（客户端快照）

同步协议又叠了 delta、remote_id 水位、快照前置、候选放弃、rebase、op_id/local_id 双身份。
每加一个展示字段（例如旧值），都要同时改两套表、两套回放、两套历史语义，bug 自然反复。

v2 的目标是把模型收敛成：

```text
本地：entries（当前值） + history（全部 value 记录） + sync（水位）
云端：history（全部 set） + snapshots（基线） + entries 实时表（回放结果）
```

**云端不做业务裁决，只接受数据、分配提交序号、存快照。**
**本地回放只做一条规则：`(event_at, op_id)` 更大的更新 value，其余只进 history。**

## 2. 核心模型

### 2.1 只有 set 和 meta

- 所有值写入都是绝对值 `set`；`regen` / `reset` 也是 set，只是展示标签不同。
- **v2 没有 `delta` 同步**：服务端不接收、不下发、不回放；
  本地 history 可以保留 v1 的 delta 作为只读历史，但推送时跳过。
  更新后的客户端建立首份快照后，不可能再产生 delta。
- `meta` 只表达 key 的锁定状态：`old_value/new_value` 为 0/1，只改 `locked`，不改 value；
  回放按 `remote_id` 顺序取最新，不做仲裁。
- regen/quota 的周期变化继续是读时函数；只有真实写入才进 history。

### 2.2 提交序号：本地 `local_id`，云端 `remote_id`

本地 `profile_history_v2.local_id` 是本地自增顺序；云端 `profile_history_v2.remote_id`
是 `INTEGER PRIMARY KEY AUTOINCREMENT`，由 SQLite 在**插入时**分配，代表云端提交顺序：

- `remote_id <= W` 的行都已经提交可见；
- 不会出现更低的 `remote_id` 在之后才可见；
- 快照绑定 `snapshot_id = W`，客户端只拉 `remote_id > W`；
- 空洞无害，`remote_id` 不要求连续。

v1 的 `remote_id` 是「领号时分配」，领号和插入是两个事务，低号可能晚于高号提交；
v2 改为插入时分配，领号与插入是同一个事务，不再有低号晚可见的问题。

### 2.3 本地 entries 的裁决

```text
拉到/写入的每一行都进 history
if (event_at, op_id) > entry 的 (event_at, op_id):
    entry 更新
else:
    entry 不动，只保留记录
```

- 过期 set 也允许上传、允许入库；它只影响 history，不影响 entries。
- `event_at` 为空/无法解析，或晚于**该行 `created_at` + 24h** 的行：**仍然进 history**，
  但不更新 entries；避免坏钟压住 key，同时不丢历史。本地与云端用同一判定基准。
- `op_type='delta'` 只留在 history，不参与 value 回放；服务端也不会下发它。
- 回放只认 `set/regen/reset/meta`；其他未知 `op_type` 只进 history，不更新 entries。
- `locked` 通过 `op_type='meta'` 的 history 行同步，不新增锁专用字段：
  - `old_value/new_value` 是 0/1，表示该 key 的 locked 从 false 到 true；
  - 不做 rank、不做冲突裁决，回放按 `remote_id` 顺序，后到的 meta 行覆盖；
  - 快照 entries 带 `locked`，作为新设备的初始锁状态。
- 同一用户不会并发 `inc -1`（游戏前提），所以不需要分布式计数器。
- 同一 key 的两台设备并发 `set`：后者覆盖，history 两条都保留。

### 2.4 快照

- 快照 = 某个 `remote_id` 水位上的全量 entries（value + locked），只是「从 W 开始回放」的基线，
  不参与裁决；用于新设备/长离线设备快速到最新。
- 任意已认证客户端（PC / Android，能力对齐）在完成一次干净 pull + push 后上传快照：
  `snapshot_id = 已应用到的最大 remote_id`。
- **快照管状态，水位管历史**：
  - 新设备（本地 history 为空）：选择基线。
    - 用最新快照：最快到最新，但该快照水位之前的历史不可得；
    - 用最早保留的快照：可拿到该水位之后的全量历史，但要多回放。
  - 已有本地 history 的设备：**不得用快照推进 `remote_id`**，继续从本地水位 pull；
    快照条目最多作为 entries 的候选合并，不改变历史水位。

### 2.5 周期引擎

- `ProfileEngine` 只在**本次进程完成一次干净 pull + push** 之后执行周期处理。
- 周期值仍然读时计算，不写 entries、不写 history。
- 没有 `profile_inc` 写闸门；离线写入照常，写入本地 history 等待推送。

## 3. 本地数据库 v2

旧表不删除，保留为 `_v1` 备份；新代码只读写 v2 表。

> **本地没有 `account_id`**：profile 数据的身份是 `(username, type, key)`。
> 云端账号只是同步目标；切换云端账号只重置同步状态，不触碰 entries/history。

### 3.1 `profile_entries_v2`（当前值）

```sql
CREATE TABLE profile_entries_v2 (
    username     TEXT NOT NULL,
    type         TEXT NOT NULL,            -- quota / regen / stock / note
    key          TEXT NOT NULL,
    value        NOT NULL DEFAULT 0,       -- 无类型声明：数值存 REAL，note 存 TEXT
    event_at     TEXT NOT NULL DEFAULT '', -- 胜出写入的真实事件时间
    op_id        TEXT NOT NULL DEFAULT '', -- 胜出写入的 op_id
    locked       INTEGER NOT NULL DEFAULT 0, -- 当前锁状态 0/1
    PRIMARY KEY (username, type, key)
);
```

- 本地 entries 是云端 `profile_entries_v2` 的子集：云端只多 `account_id`，其余列完全一致。
- `locked` 是 key 的当前锁状态，由 `op_type='meta'` 的 history 行维护。
- `value` 是**无类型列**：数值模型存 REAL，note 存 TEXT；不要改成 `REAL` 或 `TEXT`。
- 没有 `value_text`；note 的文本就是 `value`。
- 没有 `origin` 列；变更来源只在 history 的 `source` 里。
- 真实 entries 的 `event_at` 必须非空；列的 `DEFAULT ''` 只是兜底，迁移/导入须填真实时间（见 §7.3）。

### 3.2 `profile_history_v2`（全部记录 + 推送源）

```sql
CREATE TABLE profile_history_v2 (
    local_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 本地顺序
    op_id           TEXT NOT NULL,                     -- 客户端 uuid，幂等键
    username        TEXT NOT NULL,
    type            TEXT NOT NULL,
    key             TEXT NOT NULL,
    op_type         TEXT NOT NULL DEFAULT 'set',       -- set/regen/reset/meta
    old_value,                                         -- 无类型：数值 REAL / note TEXT
    new_value,                                         -- 同上
    delta_value     REAL,                              -- 展示用；note 为 NULL
    source          TEXT NOT NULL DEFAULT '',          -- 变更来源：OCR/DSL/手动等
    sync_from       TEXT NOT NULL DEFAULT '',          -- 同步来源 key：model:key
    device_id       TEXT NOT NULL DEFAULT '',          -- 本机 instance.id，永不变化
    event_at        TEXT NOT NULL,                     -- 真实事件时间，无时区秒串
    created_at      TEXT NOT NULL                      -- 本机落库时间；pull 行保留云端 created_at
);
CREATE UNIQUE INDEX profile_history_v2_op
    ON profile_history_v2(op_id);
CREATE INDEX profile_history_v2_key
    ON profile_history_v2(username, type, key, local_id DESC);
```

- 本地 history 同时是推送源，但**不保存每行的上传状态**：
  `profile_sync_v2.local_id` 记录已推到的 `local_id`，下次从它之后继续推。
- 推送成功 = 服务端 accepted/duplicate（`op_id` 幂等）；整批成功后把 `local_id`
  推进到本批最后一条 `local_id`，失败则保持原位重推。
- `device_id` 是 `config/session/instance.id` 的内容：首次生成后**永不变化**，
  换账号、换 token 都不变；只有删除实例数据才会变，视为严重异常，不做兼容。
- `device_id = 本机 instance.id` 的行才推送；pull 回来的行 `device_id` 是远端，推送时跳过。
- `op_type='delta'` 行只作本地历史，推送时跳过并推进 `local_id`；服务端永远不会收到 delta。
- pull 写入的远端行也会占用本地 `local_id`，但**不推进** `profile_sync_v2.local_id`；
  只有 push ack 才推进推送水位。因此不会跳过或影响尚未推送的本地旧行。
- 形状不合法的行本地不应产生；若出现，推送时跳过并推进 `local_id`，只记日志，不阻塞后续行。
- `old_value/new_value` 对本地写入由写入前的 entry 计算；对远端行直接使用云端上传的值。
  数值模型是 REAL，note 是 TEXT。
- history **没有** `locked` 列；锁状态用 `op_type='meta'` 行表达：
  `old_value/new_value` 为 0/1，只改 entry 的 `locked`，不改 value。
- `created_at`：本地新写入用本机时间；pull 回来的行保留云端的 `created_at`。

### 3.3 `profile_sync_v2`（水位与快照）

```sql
CREATE TABLE profile_sync_v2 (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    remote_id           INTEGER NOT NULL DEFAULT 0, -- 已拉取到的云端 remote_id
    local_id            INTEGER NOT NULL DEFAULT 0, -- 已推送到的本地 local_id
    snapshot_ref_id     INTEGER NOT NULL DEFAULT 0, -- 本地引用的快照主键（云端 snapshot.id）
    snapshot_id         INTEGER NOT NULL DEFAULT 0, -- 该快照覆盖到的云端 remote_id
    protocol_version    INTEGER NOT NULL DEFAULT 2
);
```

- 本地只有一份同步状态；切换云端账号时由账号流程重置 `remote_id`/`local_id`/快照引用，
  **不修改 entries/history 数据**。
- `remote_id` 是拉取水位，`local_id` 是推送水位；`snapshot_ref_id` 是本地快照主键，
  `snapshot_id` 是该快照覆盖的云端水位。只有这两个 id 概念。
- 不再把同步状态放 `account.json`；与 history 同库，掉电不会出现「行写了、水位没写」。

## 4. 云端数据库 v2

v2 新增三张业务表（history / entries / snapshots）+ 一张账户版本元数据表：

- **history**：全部写入记录，云端与本地同构；
- **entries**：实时当前值，云端与本地结构一致（云端多 `account_id`），
  **两端都不直接写，只由 history 回放刷新**；
- **snapshots**：某个 `remote_id` 水位上的 entries 全量，仍用单个 JSON 字段保存，
  一份快照一次写入，不展开成上千行。

### 4.1 `profile_history_v2`（只追加的 set 日志）

```sql
CREATE TABLE profile_history_v2 (
    remote_id      INTEGER PRIMARY KEY AUTOINCREMENT, -- 云端提交顺序 / 拉取水位
    account_id     TEXT NOT NULL,
    op_id          TEXT NOT NULL,
    username       TEXT NOT NULL,
    type           TEXT NOT NULL,
    key            TEXT NOT NULL,
    op_type        TEXT NOT NULL DEFAULT 'set',  -- set/regen/reset/meta
    old_value,                                  -- 无类型：数值 REAL / note TEXT
    new_value,                                  -- 同上
    delta_value    REAL,                        -- 展示用；note 为 NULL
    source         TEXT NOT NULL DEFAULT '',    -- 变更来源：OCR/DSL/手动等
    sync_from      TEXT NOT NULL DEFAULT '',    -- 同步来源 key：model:key
    device_id      TEXT NOT NULL DEFAULT '',    -- 写入实例 id，来自会话
    event_at       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX profile_history_v2_op
    ON profile_history_v2(account_id, op_id);
CREATE INDEX profile_history_v2_pull
    ON profile_history_v2(account_id, remote_id);
CREATE INDEX profile_history_v2_key
    ON profile_history_v2(account_id, username, type, key, remote_id);
```

- 过期、`event_at` 为空/非法、未来时间都接受并原样入库；能否解析成事件时间由回放端判断。
- `event_at` 能解析就存 canonical 秒串；不能解析存空串，空串在回放端视为 history-only。
- `delta_value` 仅展示变动量，不校验。
- `device_id` 取会话实例（客户端 `config/session/instance.id` 的内容），不由客户端正文决定。

### 4.2 `profile_snapshots_v2`（快照）

```sql
CREATE TABLE profile_snapshots_v2 (
    id                  INTEGER NOT NULL,        -- 逻辑快照 id；同一快照的分片共享
    account_id          TEXT NOT NULL,
    snapshot_id         INTEGER NOT NULL,        -- 覆盖到的 profile_history_v2.remote_id
    shard_id            INTEGER NOT NULL DEFAULT 0, -- 0 = 未分片；后续 1..N
    origin_device       TEXT NOT NULL DEFAULT '',   -- 创建者 device_id，同 history.device_id（instance.id）
    origin_name         TEXT NOT NULL DEFAULT '',   -- 展示名，客户端自报
    entry_count         INTEGER NOT NULL DEFAULT 0, -- 当前分片的条目数
    entries             TEXT NOT NULL,           -- 当前分片的 JSON 数组
    created_at          TEXT NOT NULL,
    PRIMARY KEY (account_id, id, shard_id)
);
CREATE INDEX profile_snapshots_v2_latest
    ON profile_snapshots_v2(account_id, id DESC);
CREATE INDEX profile_snapshots_v2_shard
    ON profile_snapshots_v2(account_id, id, shard_id);
```

- `id` 不是行自增主键，而是逻辑快照 id：当前每份快照只写 `shard_id=0` 一行；
  未来分片时同一 `id` 写 `shard_id=0..N` 多行。
- `id` 由服务端在同一事务内按 `MAX(id)+1` 分配；分片行共享同一个 `id`。
- 索引以 `(account_id, id[, shard_id])` 为前缀，后续增加分片不需要改索引。
- `origin_device` 与 history 的 `device_id` 同义，都是客户端的 `instance.id`；
  保留 `origin_device` 命名只为延续快照展示字段，不要另造第三套设备标识。

`entries` 每条字段与 `profile_entries_v2` 完全一致（只是不含 `account_id`）：

```json
{"username": "甲", "type": "stock", "key": "钱袋", "value": 123.5,
 "event_at": "2026-11-12T12:00:00", "op_id": "uuid", "locked": 0}
```

note 的 `value` 是字符串：

```json
{"username": "甲", "type": "note", "key": "备注", "value": "主玩会心双刀",
 "event_at": "2026-11-12T12:00:00", "op_id": "uuid", "locked": 0}
```

- **规模上限与后续分片**：当前单 shard 的 JSON 上限是 5000 条（约 1MB，接近 D1 单行限制）。
  超过时同一 `id` 写多个 `shard_id`（1..N），读取按 `shard_id` 升序拼接；
  `id` / `snapshot_id` 语义不变，客户端和接口不需要改。
- `snapshot_id` 必须等于当前 `head`（小于 head 返回 `409 snapshot_behind_head`）。
- **服务端自动补空基线**：账户首次写入 history、且还没有任何快照时，push 事务内自动插入
  `snapshot_id=0`、`shard_id=0`、`entry_count=0`、`entries='[]'`、`origin_name='server'` 的一行。
  因此 `head>0` 时必有快照，`/pull` 不以快照为前置；空快照也是合法快照。
- 保留最近 20 份按逻辑 `id` 计数，不按 shard 行数。

### 4.3 `profile_entries_v2`（云端实时表）

结构与本地 entries 一致，只多 `account_id`：

```sql
CREATE TABLE profile_entries_v2 (
    account_id  TEXT NOT NULL,
    username    TEXT NOT NULL,
    type        TEXT NOT NULL,
    key         TEXT NOT NULL,
    value,                        -- 无类型：数值 REAL / note TEXT
    event_at    TEXT NOT NULL,
    op_id       TEXT NOT NULL,
    locked      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, username, type, key)
);
```

- **客户端不直接写这张表**；它是 history 的回放结果，云端与本地都靠回放刷新。
- 服务端在 push 的同一事务内回放：`set/regen/reset` 按 `(event_at, op_id)` 取更大者，
  `op_type='delta'` 跳过，`op_type='meta'` 直接覆盖 `locked` 且按 `remote_id` 升序应用；
  事务失败整体回滚，因此实时表始终等于 `head`，不存在异步滞后。
- 与本地回放 §2.3 对齐：`event_at` 为空/无法解析，或大于**该行 `created_at` + 24h** 的行
  只留在 history，不更新实时表。`created_at` 是服务端接收时间，不是客户端时钟；
  该判定在入库后是确定的，不会随回放时间推移而改变。
- 它只是物化缓存，权威数据仍是 history + snapshot；删表后也能从快照 + history 重建。
- 网页矩阵的 `as_of_remote_id == head` 直接读它；`as_of` 更小时必须走快照 + tail 折叠（见 §5.6），
  因为实时表只有当前值。

### 4.4 `profile_accounts_v2`（账户版本）

```sql
CREATE TABLE profile_accounts_v2 (
    account_id TEXT PRIMARY KEY,
    version    INTEGER NOT NULL DEFAULT 2
);
```

- 用于判断账户是否已经完成 v2 迁移。

### 4.5 写路径：entries 只读不写

- 客户端写入先写 history，再回放 entries；云端只收 history 并回放实时表；快照单独上传。
- entries 不会与 history 分叉，任何时候都能从 snapshot + history 重建。

### 4.6 history 字段对齐

本地 `profile_history_v2` 与云端业务字段完全一致；本地主键 `local_id`，
云端主键 `remote_id`，云端另多 `account_id`。

| 分组 | 字段 | 说明 |
| --- | --- | --- |
| 本地专有 | `local_id` | 本地自增顺序 / 推送水位 |
| 共享 | `op_id` | 客户端 uuid，幂等键 |
| 共享 | `username` / `type` / `key` | 定位 |
| 共享 | `op_type` | set/regen/reset/meta；meta 只表达锁状态；v2 没有 delta |
| 共享 | `old_value` / `new_value` | 旧值 / 新值；数值是 REAL，note 是 TEXT |
| 共享 | `delta_value` | 展示用变动量；note 为 NULL |
| 共享 | `source` | 变更来源描述：OCR 观测 / DSL 写入 / key 改名重设等 |
| 共享 | `sync_from` | 同步来源 key，格式 `model:key`；表示该行由哪个 key 同步而来 |
| 共享 | `device_id` / `event_at` | 写入设备、真实事件时间 |
| 共享 | `created_at` | 本端落库/接收时间 |
| 云端专有 | `remote_id` | 云端提交顺序 / 拉取水位 |
| 云端专有 | `account_id` | 云租户；**本地没有这一列** |

注意：

- 本地 profile 的身份是 `(username, type, key)`；云端账号只是同步目标。
- `source` 是变更来源描述（OCR/DSL/手动等）；`sync_from` 是同步来源 key
  （`model:key`）。两者业务含义不同，不能互相覆盖或混用。
- history 与 push/pull 的业务字段统一叫 `new_value`；快照 entries 是当前值，用单 `value`。

### 4.7 单 value 的数据库约束

已在 SQLite 3.49.1 与本地 D1（wrangler/miniflare）验证：无类型列会保留存储类，
`typeof()` 和 JSON 输出都能区分 number / string。

硬约束：

1. `profile_entries_v2.value`、`profile_history_v2.old_value/new_value` **不写类型**；
   不要改成 `REAL` 或 `TEXT`。
2. `type` 是唯一判别器：quota/regen/stock 绑 float，note 绑 str。
3. 数值 SQL 必须排除 note：`type <> 'note'` 或 `typeof(value)='real'`；
   `new_value - old_value` 不能直接作用在 note 行上。
4. API 的 `value` / `new_value` 是 `number | string`，网页按 `type` 渲染。
5. `delta_value` 只对数值行有值，note 行为 NULL。
6. 数值聚合/变动量计算必须排除 `op_type='meta'` 行：它只是锁状态 0/1。
7. 增加启动/迁移自检：插入一个 float 和一个字符串，断言 `typeof` 分别是
   `real`/`text`，JSON 输出分别是 number/string。

### 4.8 时间列审查

| 表 | 时间列 | 用途 | 结论 |
| --- | --- | --- | --- |
| entries（本地/云端） | `event_at` | 胜出写入的业务事件时间，LWW 必需 | 保留 |
| history（本地/云端） | `event_at` | 业务事件时间，回放仲裁 | 保留 |
| history（本地/云端） | `created_at` | 本端落库/接收时间，审计与展示 | 保留 |
| snapshots | `created_at` | 快照创建时间，列表展示 | 保留 |
| sync | — | 同步进度由 `remote_id` / `local_id` 表示 | 已删除 `last_sync_at` |
| accounts | — | 迁移完成由 `version` 表示 | 已删除 `migrated_at` |

- `event_at` 与 `created_at` 语义不同，不能合并：前者是业务事件时间，后者是本端处理时间。
- 云端 entries 不存 `created_at`：它是 history 回放的物化结果，时间信息以 history 为准。
- 本地 entries 不存 `created_at`：`event_at` 已是胜出写入时间，落库时间在 history 里。

## 5. v2 接口

统一前缀 `/api/v2/profile`；认证沿用现有 Bearer 会话。v1 `/api/sync/profile/*` 保持原样，
但新客户端只走 v2，不做 v1/v2 双写。

### 5.0 通用约定

- 认证：`Authorization: Bearer <access_token>`；缺失/过期 → `401 unauthorized`。
- 请求/响应：`application/json; charset=utf-8`；未知字段忽略。
- 账户：`account_id` 从会话取，正文不得传；`device_id` 是客户端 `instance.id`，从会话取。
- 时间：`event_at` / `created_at` / `server_time` 为 `YYYY-MM-DDTHH:MM:SS` 无时区秒串。
- ID：`head_remote_id` / `oldest_remote_id` / `remote_id` / `snapshot_id` / `snapshot_ref_id`
  为整数；`latest_snapshot_id` 可为 `null`。
- 分页：`limit` 为整数；pull 默认 500、最大 1000；history 默认 100、最大 1000。
- 错误信封：

```json
{"error": "snapshot_expired", "error_code": "PROFILE_SNAPSHOT_EXPIRED",
 "message": "快照已被清理", "details": {"latest_snapshot_id": 8}}
```

  关键业务字段（`latest_snapshot_id` / `oldest_remote_id` / `head_remote_id`）同时放在顶层，
  便于客户端直接读取。

| HTTP | `error` | `error_code` | 场景 |
| --- | --- | --- | --- |
| 400 | `invalid_request` | `PROFILE_INVALID_REQUEST` | 参数/形状不合法 |
| 400 | `invalid_as_of` | `PROFILE_INVALID_AS_OF` | `as_of` 超前/非整数 |
| 401 | `unauthorized` | `PROFILE_UNAUTHORIZED` | 未登录/令牌失效 |
| 404 | `not_found` | `PROFILE_V2_NOT_AVAILABLE` | v2 接口未部署 |
| 409 | `snapshot_behind_head` | `PROFILE_SNAPSHOT_BEHIND_HEAD` | 快照水位落后于 head |
| 409 | `history_gap` | `PROFILE_HISTORY_GAP` | 客户端水位落入已清理区间 |
| 409 | `snapshot_too_old` | `PROFILE_SNAPSHOT_TOO_OLD` | `as_of` 早于所有有效快照 |
| 410 | `snapshot_expired` | `PROFILE_SNAPSHOT_EXPIRED` | 快照不存在或尾部历史已清理 |
| 413 | `payload_too_large` | `PROFILE_PAYLOAD_TOO_LARGE` | 批量/快照超过限制 |
| 429 | `rate_limited` | `PROFILE_RATE_LIMITED` | 频率限制 |
| 500 | `internal` | `PROFILE_INTERNAL` | 服务端异常 |

- 幂等：push 按 `(account_id, op_id)`；snapshot 按 `(snapshot_id, entries 哈希)`。
- `PROFILE_SNAPSHOT_REQUIRED` 只作为 `/state` 的业务提示，不是 HTTP 错误。
- `stale` 不是 HTTP 错误：`/entries` 返回 200 + `stale=true`，见 §5.6。

### 5.1 `GET /api/v2/profile/state`

```json
{
  "protocol_version": 2,
  "head_remote_id": 12345,
  "oldest_remote_id": 1,
  "latest_snapshot_id": 7,
  "snapshot_id": 12000,
  "snapshot_required": false,
  "snapshot_state": "ready",
  "error_code": null,
  "next_action": "pull",
  "server_time": "2026-11-12T12:00:00"
}
```

客户端先读它；`protocol_version != 2` 时暂停 Profile 同步并提示服务端未升级，不回退 v1。

- `oldest_remote_id`：云端仍保留的最小 `profile_history_v2.remote_id`；0 表示没有历史行。
  客户端用它检测自己的水位是否已经落到已清理区间。
- `snapshot_required` / `snapshot_state`：`none` 只可能是账户还没有任何 history（`head=0`）：
  - `true` / `none`：直接 `POST /snapshot` 创建空快照（`snapshot_id=0`、entries 为空），再 pull；
  - `false` / `ready`：先 `GET /snapshot` 取基线，再 pull。基线可能是服务端自动补的
    `snapshot_id=0` 空快照（首份 push 时创建，见 §4.2）。

请求参数：无。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `protocol_version` | int | 固定 2；不是 2 时客户端停止 Profile 同步 |
| `head_remote_id` | int | 当前最大 `remote_id`；0 表示无历史 |
| `oldest_remote_id` | int | 仍保留的最小 `remote_id`；0 表示无历史 |
| `latest_snapshot_id` | int \| null | 最新快照主键 |
| `snapshot_id` | int \| null | 最新快照覆盖的 `remote_id` |
| `snapshot_required` | bool | 仅 `head=0` 且无快照时为 `true` |
| `snapshot_state` | `"ready"` \| `"none"` | 快照状态 |
| `error_code` | string \| null | 无快照时为 `PROFILE_SNAPSHOT_REQUIRED` |
| `next_action` | `"pull"` \| `"create_snapshot"` | 客户端下一步 |
| `server_time` | string | 服务端当前秒串 |

错误：`401 unauthorized`、`500 internal`。不存在 v2 时返回 404，且同样走统一错误信封
（`error_code=PROFILE_V2_NOT_AVAILABLE`）；客户端不得回退 v1。

### 5.1.1 首次启动无快照的契约

`/state` 始终返回 **HTTP 200**；`snapshot_state="none"` 只出现在账户还没有任何 history
（`head=0`）时。客户端直接创建空快照即可：

```json
{
  "protocol_version": 2,
  "head_remote_id": 0,
  "oldest_remote_id": 0,
  "latest_snapshot_id": null,
  "snapshot_id": null,
  "snapshot_required": true,
  "snapshot_state": "none",
  "error_code": "PROFILE_SNAPSHOT_REQUIRED",
  "next_action": "create_snapshot"
}
```

- 空快照也是合法快照：`snapshot_id=0`、`entries=[]`，之后 `/pull` 从 0 返回空。
- 一旦有任何 push 写入 history，服务端会在同一事务内自动补一份 `snapshot_id=0` 的空快照
  （§4.2），所以 `head>0` 时一定存在快照。
- 因此 `/pull` 不以快照为前置，不存在「必须先建快照才能 pull」的死锁。

建快照前仍需 pull 到 head 并追平 `local_id`（§5.4），不要用局部 entries 直接建快照：

> 反例：云端已有 1..100，本机只 push 了 101..150 就建快照 150，entries 并不代表
> 水位 150 的全量；必须先把 1..150 拉全再建快照。

### 5.2 `POST /api/v2/profile/push`

```json
{
  "ops": [
    {
      "op_id": "uuid",
      "username": "甲",
      "type": "stock",
      "key": "钱袋",
      "op_type": "set",
      "new_value": 123.5,
      "old_value": 120,
      "delta_value": 3.5,
      "source": "OCR 观测",
      "event_at": "2026-11-12T11:59:00"
    }
  ]
}
```

- 每批最多 200 条。
- 服务端按 `(account_id, op_id)` 幂等；重复返回已有 `remote_id` 和 `duplicate`。
- 每批响应：

```json
{
  "results": [
    {"op_id": "uuid", "remote_id": 12346, "status": "accepted"},
    {"op_id": "uuid2", "remote_id": 12000, "status": "duplicate"}
  ],
  "head_remote_id": 12346
}
```

- 不要求先拉、不要求快照、不检查 base。过期 set 照常 accepted。
- 若账户还没有任何快照，本批 push 事务会自动补一份 `snapshot_id=0` 的空快照（见 §4.2）。
- `event_at` 为空/非法/未来**不是拒绝理由**：照常入库，由回放端决定是否更新 entries。
- 只有形状不合法（缺 `op_id`/`key`/`type`，value 既非有限数字也非文本，
  或 `op_type` 不在 `set/regen/reset/meta`）才拒绝；
  客户端跳过该行并推进 `local_id`，只记日志，不阻塞后续行。
- PC 迁移出的历史会带 `op_type` 和 `sync_from`；普通新写入 `op_type='set'`、`sync_from=''`，
  由 key 同步映射产生的写入带 `sync_from='model:key'`。
  `op_type` 不参与 value 仲裁；`sync_from` 只是同步来源标识，不参与仲裁。
- `op_type='meta'` 行只改锁：`new_value` 必须是 0/1，否则 rejected；`delta_value=null`；
  它不看 `event_at`，按 `remote_id` 顺序生效，不参与 value 仲裁。
- `event_at` 严格按 `YYYY-MM-DDTHH:MM:SS` 解析；不合格式时存空串（history-only），
  不保存原始非法串。
- `delta_value` 不校验是否等于 `new_value - old_value`；它是展示字段，由客户端负责。
- `op_id` 去重只在对应行仍存在时生效：行被清理后，同一 `op_id` 重推会按新行接受、
  分配新 `remote_id`；客户端以本地唯一 `op_id` 去重，云端允许出现重复内容行。

请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `ops` | array | 是 | 1..200 条 |
| `op_id` | string | 是 | 8..80；账户内唯一；不以 `unscoped:` 开头 |
| `username` | string | 是 | 1..80 |
| `type` | string | 是 | 1..20；`quota/regen/stock/note` |
| `key` | string | 是 | 1..120 |
| `new_value` | number \| string | 是 | 数值模型为有限 number；note 为 string；meta 行为 0/1；不接受 NaN/Inf |
| `old_value` | number \| string \| null | 否 | 展示用；note 为 string；meta 行为 0/1 |
| `delta_value` | number \| null | 否 | 展示用；note 为 null；不校验 `new-old` |
| `op_type` | string | 否 | 默认 `set`；取值 `set/regen/reset/meta` |
| `source` | string | 否 | 0..200；变更来源描述（OCR/DSL/手动等） |
| `sync_from` | string | 否 | 0..200；同步来源 key，格式 `model:key`；不参与仲裁 |
| `event_at` | string | 是 | 19 位秒串或空串；非法/空存空串（history-only） |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `results` | array | 与请求 `ops` 一一对应 |
| `results[].op_id` | string | 对应请求 |
| `results[].remote_id` | int \| null | accepted/duplicate 为正整数；rejected 为 null |
| `results[].status` | `"accepted"` \| `"duplicate"` \| `"rejected"` | 结果 |
| `results[].error` | string | 仅 rejected；`invalid_shape` / `invalid_op_id` / `invalid_value` |
| `head_remote_id` | int | 本批处理后的最大 `remote_id` |

HTTP：整批 200 即返回逐条结果；整批形状错误 400；未登录 401；超大 413；限流 429。
`accepted` 与 `duplicate` 都必须带可用 `remote_id`；客户端不需要落库，只用于确认本批成功。
同一批内重复 `op_id`：第一条 accepted，后续 duplicate；`results` 与请求 `ops` 同序。

### 5.3 `GET /api/v2/profile/pull?since_remote_id=&snapshot_ref_id=&limit=`

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `since_remote_id` | int | 否 | 默认 0；>= 0 |
| `snapshot_ref_id` | int | 否 | 本地快照主键；用于失效校验 |
| `limit` | int | 否 | 1..1000，默认 500 |

- pull 不以快照为前置：无快照时按空基线从 `since_remote_id` 回放；
  `head>0` 时服务端已在首份 push 时自动补了 `snapshot_id=0` 的空快照（§4.2）。
- `snapshot_ref_id` 可选：客户端带上本地快照 id 做有效性校验。
  - 快照不存在，或 `snapshot_id < oldest_remote_id - 1`（尾部历史已被清理）→
    `410 snapshot_expired`：
    ```json
    {"error": "snapshot_expired", "latest_snapshot_id": 8, "head_remote_id": 12345,
     "oldest_remote_id": 1}
    ```
    **按本地水位分开处理**：
    - 本地 `remote_id >= oldest_remote_id - 1`：历史仍可连续拉取。
      用 `latest_snapshot_id` 调 `GET /snapshot?id=` 取新的 `snapshot_id`，
      只把快照引用切过去作为后续兜底，**不推进 `remote_id`**，继续从原水位 pull；
      不要因为快照失效而跳过仍存在的行。
    - 本地 `remote_id < oldest_remote_id - 1`：同时存在历史缺口，按 §6.1 的
      `history_gap` 分支重建基线、记录缺口。
  - 快照有效 → 正常返回增量。

```json
{
  "ops": [
    {
      "remote_id": 12346,
      "op_id": "uuid",
      "username": "甲",
      "type": "stock",
      "key": "钱袋",
      "op_type": "set",
      "new_value": 123.5,
      "old_value": 120,
      "delta_value": 3.5,
      "source": "OCR 观测",
      "device_id": "v2415A",
      "event_at": "2026-11-12T11:59:00",
      "created_at": "2026-11-12T12:00:00"
    }
  ],
  "last_remote_id": 12346,
  "latest_snapshot_id": 7
}
```

- 多端同时首启以 `/state` 的 `latest_snapshot_id` 为准拉取，不假设 first-wins；
  `POST /snapshot` 只在 entries 完全相同且水位相同时 reused。
- **漏拉检测**：如果 `since_remote_id < oldest_remote_id - 1`，说明客户端水位落在已清理区间，
  pull 返回 `409 history_gap`：
  ```json
  {"error": "history_gap", "oldest_remote_id": 1, "head_remote_id": 12345,
   "latest_snapshot_id": 7}
  ```
  客户端必须停止增量拉取，改用快照重建基线，并记录「该区间的历史已不可得」；
  不得把 `remote_id` 静默跳到 head。
- `since_remote_id == oldest_remote_id - 1` 时不报错，从 `oldest_remote_id` 开始拉全。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ops` | array | `remote_id > since_remote_id` 的行，按 `remote_id` 升序 |
| `ops[].remote_id` | int | 云端提交序号；客户端用它推进水位 |
| `ops[].op_id` | string | 幂等键 |
| `ops[].username/type/key` | string | 定位 |
| `ops[].op_type` | string | set/regen/reset/meta |
| `ops[].new_value/old_value` | number \| string \| null | 数值或 note 文本 |
| `ops[].delta_value` | number \| null | 展示用 |
| `ops[].source` | string | 变更来源描述 |
| `ops[].sync_from` | string | 同步来源 key（`model:key`） |
| `ops[].device_id` | string | 写入设备 |
| `ops[].event_at/created_at` | string | 事件时间/服务端接收时间 |
| `last_remote_id` | int | 本页最后一条 `remote_id`；无新行时等于 `since_remote_id` |
| `latest_snapshot_id` | int \| null | 最新快照主键；无快照时为 null |

错误：`400 invalid_request`、`401 unauthorized`、`409 history_gap`、`410 snapshot_expired`。

### 5.4 `POST /api/v2/profile/snapshot`

```json
{
  "snapshot_id": 12345,
  "origin_name": "DESKTOP-PC",
  "entries": [
    {"username": "甲", "type": "stock", "key": "钱袋",
     "value": 123.5, "locked": 0,
     "event_at": "2026-11-12T12:00:00", "op_id": "uuid"}
  ]
}
```

- 上传方必须已经 **pull 到 `head_remote_id`**（`remote_id == head_remote_id`）；
  pull 写入的远端行会占用本地 `local_id` 但不推进推送水位，因此 pull 到 head 后
  **必须再跑一轮 push**：远端行/`delta` 行跳过并推进 `local_id`，
  直到 `local_id` 追平本机最新记录。**只有 `remote_id == head` 且 `local_id` 追平后才能建快照**；
  push ack 不算 pull，rejected 行跳过、不进入快照。
- 若 `POST` 时 head 已前进，导致 `snapshot_id < head`，返回
  `409 snapshot_behind_head`；客户端重新 pull 到新 head 后再提交。
- 写入：同一事务内完成：
  1. 插入 `profile_snapshots_v2` 一行（当前 `shard_id=0`；分片后同一 `id` 多行）；
  2. **用这份 entries JSON 重置该账户的 `profile_entries_v2`**：删除旧行并批量插入，
     再回放 `remote_id > snapshot_id` 的 history tail；
  3. 提交。
  这样云端实时表与快照水位严格一致；重置只在创建快照时发生一次，不是每次同步。
  如果账户还没有任何 history（Android 只迁 entries），实时表就完全由这份快照建立。
  `snapshot_id == head` 的校验、快照插入、实时表重置必须在**同一事务**里完成，
  避免并发 push 在水位校验与重置之间插入行导致实时表丢数据。
- 服务端只做形状校验，不做业务范围校验：
  1. `snapshot_id` 是整数且 **等于 `head_remote_id`**；小于 head 返回
     `409 snapshot_behind_head`，大于 head 返回 400；
  2. `entries` 数量 `<= 5000`（当前单个 shard 的保护值，约 1MB；分片后按 shard 分别限制）；
     `username/type/key` 非空且在长度限制内，`event_at` 非空且为 19 位秒串；
     `head_remote_id>0` 时 entries 不能为空（空快照只在 `head_remote_id=0` 允许）；
  3. `value` 是有限数字或字符串；`type='note'` 允许字符串，数值模型要求有限数字；
     不接受 NaN/Inf；
  4. `locked` 只能是 0 或 1，缺省为 0；
  5. 同一份 entries 内 `(username, type, key)` 不重复；
  6. `origin_name` 在长度限制内。
- 服务端无法证明 entries 一定完整；如需强一致，可在写入时按历史折叠校验（可选）。
- 返回 `{id, snapshot_id, entry_count, reused}`：
  - `reused=true` 仅当同一 `snapshot_id` 且 entries 排序后哈希完全一致；
    数值统一按 `Number(v).toFixed(6)` 归一化，note 文本原样，
    比较包含 `event_at` / `op_id` / `locked`；
  - 否则插入新快照行，`reused=false`，以最新 id 为准；
    允许有数据的客户端覆盖先前的空快照。
  - 空快照（`head_remote_id=0`）也占保留名额；有非空快照时优先清理更旧的空快照。

请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `snapshot_id` | int | 是 | 必须等于当前 `head_remote_id` |
| `origin_name` | string | 否 | 0..80 |
| `entries` | array | 是 | 单个 shard 0..5000 条（见 §4.2 分片预留） |
| `entries[].username` | string | 是 | 1..80 |
| `entries[].type` | string | 是 | 1..20 |
| `entries[].key` | string | 是 | 1..120 |
| `entries[].value` | number \| string | 是 | 数值模型有限 number；note string |
| `entries[].locked` | int | 否 | 0/1，默认 0；该快照的锁状态 |
| `entries[].event_at` | string | 是 | 19 位秒串，必须非空（迁移负责补齐，见 §7.3） |
| `entries[].op_id` | string | 否 | 胜出 op；空串在平局中输给非空 |

响应字段：`id`（快照主键）、`snapshot_id`、`entry_count`、`reused`。
错误：`400 invalid_request`、`401 unauthorized`、`409 snapshot_behind_head`、
`413 payload_too_large`。
`origin_device` 从会话取，正文不传。

### 5.5 `GET /api/v2/profile/snapshot`

- 空 body：最新快照摘要（id、水位、条目数、来源、时间）。
- `?id=`：该快照完整 entries：按 `(account_id, id)` 查所有 shard 行，
  按 `shard_id` 升序拼接 `entries`。
- 如果 `id` 不存在，或 `snapshot_id < oldest_remote_id - 1`（该快照的尾部历史
  已被清理），返回 `410 snapshot_expired`，带 `latest_snapshot_id` 和 `head_remote_id`。
- 新设备只取最新一份；要最早快照之后的全量历史，取仍有效的、最早的一份。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `scope` | `"latest"` \| `"earliest"` \| `"list"` | 否 | 默认 `latest`；与 `id` 同时传 → 400 |
| `id` | int | 否 | 指定快照主键；与 `scope` 同时传 → 400 |

响应：

- `scope=latest`（默认）：最新快照摘要
  `{latest_snapshot_id, snapshot_id, entry_count, origin_name, created_at}`；
  没有任何快照时返回 200，`latest_snapshot_id=null`、`entry_count=0`。
- `scope=earliest`：仍有效的最早快照摘要
  `{snapshot_ref_id, snapshot_id, entry_count, origin_name, created_at}`；
  没有则 `410 snapshot_expired`。
  空账户的 `earliest` 可能是水位 0 的空快照；从它起步会全量回放，这是预期行为，
  且空快照会占保留名额。
- `scope=list`：`{snapshots: [{id, snapshot_id, entry_count, origin_name, created_at}], latest_snapshot_id}`，按 `id DESC` 只列有效快照；没有时 `snapshots=[]`。
- `id=...`：完整详情
  `{id, snapshot_id, origin_device, origin_name, entry_count, entries, created_at}`。
- 分片后 `latest/earliest/list` 按 `id` 聚合，`entry_count` 为各 shard 之和，
  同一份快照只出现一次；`id=...` 的 `entries` 为各 shard 拼接结果。

错误：`400 invalid_request`、`401 unauthorized`、`410 snapshot_expired`
（不存在或 `snapshot_id < oldest_remote_id - 1`）。

### 5.6 `GET /api/v2/profile/entries?as_of_remote_id=`

- `as_of_remote_id == head_remote_id` 时直接读云端实时表 `profile_entries_v2`。
- `as_of_remote_id < head_remote_id` 时**不能读实时表**（它只有当前值），必须折叠：
  取不晚于 `as_of_remote_id` 的最新快照 JSON，再应用
  `remote_id > snapshot_id AND remote_id <= as_of_remote_id` 的 history 行：
  value 按 `(event_at, op_id)` 取每 key 最新；`op_type='meta'` 行按 `remote_id` 顺序覆盖 `locked`。
  折叠与实时表使用同一守卫：`event_at` 为空/非法/超前的行不参与 value，只保留在 history。
- 返回：

```json
{
  "as_of_remote_id": 12345,
  "snapshot_ref_id": 7,
  "entries": [
    {"username": "甲", "type": "stock", "key": "钱袋",
     "value": 123.5, "locked": 0,
     "event_at": "2026-11-12T12:00:00", "op_id": "uuid"}
  ]
}
```

- 网页矩阵直接用它，不再依赖「PC 最近有没有刷新快照」。
- `as_of_remote_id` 缺省等于 `head_remote_id`。
- 如果 `as_of_remote_id > head_remote_id`，返回 `400 invalid_as_of`。
- 如果 `as_of_remote_id < 最新快照的 snapshot_id`，取**不晚于** `as_of_remote_id` 的最新一份快照；
  没有这样的快照时返回 `409 snapshot_too_old`。
- 如果尾巴过大（例如超过 5000 行），返回 **200 + `stale=true`**、
  `stale_reason="tail_too_large"`，并带 `head_remote_id` / `latest_snapshot_id`；
  网页退化展示最新快照并标记「可能过期」，不应直接 500。
  任一在线客户端都可以手动创建新快照来消除 stale。
- 无快照时只可能是 `head_remote_id=0`：返回 200 空 entries（`entries=[]`、
  `snapshot_ref_id=null`、`snapshot_id=null`），网页显示空；`head>0` 时服务端已有自动空快照（§4.2）。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `as_of_remote_id` | int | 否 | 默认 `head_remote_id`；`0..head_remote_id` |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `as_of_remote_id` | int | 实际使用的历史水位 |
| `snapshot_ref_id` | int \| null | 采用的快照主键；无快照且空账户时为 null |
| `snapshot_id` | int \| null | 快照覆盖水位；无快照且空账户时为 null |
| `head_remote_id` | int | 当前最大 `remote_id` |
| `stale` | bool | 尾巴过大时为 true |
| `stale_reason` | string \| null | `"tail_too_large"` |
| `entries` | array | `{username,type,key,value,locked,event_at,op_id}` |

错误：`400 invalid_as_of`（超前/非整数）、`401 unauthorized`、
`409 snapshot_too_old`（`as_of` 早于所有有效快照）。

### 5.7 `GET /api/v2/profile/history?before_remote_id=&limit=&username=&device_id=`

- 直接读 `profile_history_v2`，返回 `id/op_id/username/type/key/op_type/new_value/old_value/
  delta_value/source/sync_from/device_id/event_at/created_at`。
- 网页历史页直接显示旧值/新值，不再做窗口回放。
- 旧值缺失时前端显示 `—`。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `before_remote_id` | int | 否 | 仅返回 `id < before_remote_id`；用于翻页 |
| `limit` | int | 否 | 1..1000，默认 100 |
| `username` | string | 否 | 0..80；过滤 |
| `device_id` | string | 否 | 0..64；过滤 |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ops` | array | 按 `remote_id` 倒序；字段同 pull，另含 `old_value/new_value/op_type/sync_from` |
| `next_before` | int \| null | 本页满 `limit` 且还有更早行时为最后一条 `remote_id`；否则 null |
| `total` | int | 当前过滤条件下的总行数 |

错误：`400 invalid_request`、`401 unauthorized`。

## 6. 同步流程

### 6.1 首次启动 / 新设备

> 本地升级后不再依赖 v1，直接走 v2：
> - 本机有 entries：按 §7.2 迁移，push 迁移出的 history（`profile_sync_v2.local_id=0`），pull，创建快照；
> - 本机无 entries：拉取云端已有快照，或创建空快照作为基线；
> - 无快照只可能是 `head=0`；文档/定义/用户等其他通道照常同步。

1. `GET /state`，确认 `protocol_version=2`。
2. 选择基线：
   - **本地 history 为空（新设备/重装）**：
     - 先 push 本机待推送 history（若有），**push ack 不推进 `remote_id`**；
       若云端还没有快照，本批 push 会自动补 `snapshot_id=0` 的空快照。
     - 看 `error_code` / `snapshot_required`：
       - `true`（无快照，只可能 `head=0`）：先创建空快照（`snapshot_id=0`、entries 为空），
         再继续 pull（无行）；
       - `false`（已有快照）：先 `GET /snapshot` 取基线：
         - 只要最新状态：取最新快照，`remote_id = snapshot_id`；
         - 要最早快照之后的全量历史：取**最早有效**的快照，`remote_id = 该水位`；
     - 设置好 `snapshot_ref_id` / `snapshot_id` / `remote_id` 后再 pull 到 head；
       pull 写入的远端行会占用本地 `local_id` 但不推进推送水位，所以 pull 到 head 后
       **必须再跑一轮 push**：远端行、`delta` 行跳过并推进 `local_id`，
       直到 `local_id` 追平本机最新记录；只有 `remote_id == head_remote_id`
       且 `local_id` 已追平后，才用当前 entries 创建 `snapshot_id=head` 的数据快照。
   - **本地 history 非空（已有设备）**：
     - 本地还没有快照引用时，先按第 4 步 push；服务端会自动补空快照，再取基线；
     - 已有快照引用时先校验：`GET /snapshot?id=本地快照`；
     - 若 `410 snapshot_expired` 且本地 `remote_id >= oldest_remote_id - 1`：
       只把 `snapshot_ref_id` / `snapshot_id` 切到最新快照作为兜底，**不推进 `remote_id`**，
       继续从原水位 pull；
     - 若 `410 snapshot_expired` 且本地 `remote_id < oldest_remote_id - 1`：
       同时存在历史缺口，按 `history_gap` 分支重建基线、记录缺口；
     - 快照有效：不使用快照推进 `remote_id`，从本地 `remote_id` 继续 pull，
       保证 6.6 的一一对应；快照条目只与 entries 按 `(event_at, op_id)` 合并。
3. `GET /pull?since_remote_id=remote_id&snapshot_ref_id=本地快照id`，逐页：
   - 每行按 `op_id` 幂等写入本地 history：已存在则 `INSERT OR IGNORE`，
     保留本地行，不覆盖、不重复插入；
   - `set/regen/reset` 行按 `(event_at, op_id)` 更新 entries；
     `delta` 行和其他未知 `op_type` 只进 history，不回放；`op_type='meta'` 行直接覆盖 entry.locked；
   - pull 回来的行保留云端的 `created_at`；本地新写入才用本机时间；
   - `remote_id` 推进到本页 `last_remote_id`；
   - 本页应用与水位推进在同一个本地事务里，避免「水位写了、行没写完」。
   - 如果响应 `410 snapshot_expired`：按第 2 步的失效分支处理；
     水位仍有效时只换快照引用，不跳 `remote_id`。
   - 如果响应 `409 history_gap`：重建基线并记录缺口，不得静默跳水位。
4. 推送 history：
   - 取 `local_id > profile_sync_v2.local_id` 按 `local_id` 升序，200 条/批；
     `device_id != 本机` 的行直接跳过；
   - accepted/duplicate 都算成功；整批成功后 `profile_sync_v2.local_id` 推进到本批最后一条 `local_id`；
   - rejected 行跳过并推进，只记日志；网络失败则保留 `local_id` 原位重推。
5. 成功完成后允许 `ProfileEngine` 跑周期处理。

> 硬规则：已有本地 history 的设备，任何时候都不能因为云端有更新快照就跳过未拉取的行。
> 快照只用于「本地没有基线」的新设备；历史完整性由本地 `remote_id` 保证。

### 6.2 正常同步

```text
pull → 应用 → push → 应用 ack
```

- 同步循环通常先 pull 后 push，但两者互不阻塞，**不要求先拉后才能上传**。
- pull 只推进 `remote_id`；push 只推进 `local_id`。pull 回来的远端行不改变推送水位。
- 拉取失败只保留 `remote_id` 水位，下一轮重试。
- push 失败只保留 `local_id` 原位，下一轮重推；重复由 `op_id` 幂等过滤。
- push 按 200 条/批；遇到 `429 rate_limited` 或 5xx，按 `Retry-After`（若有）或指数退避，
  分轮补传，不阻塞 pull，也不清空 `local_id`。
- PC 首次全量迁移可能积压几千行，必须允许跨多轮 push，不能一次硬推。
- 没有快照前置、没有 base、没有「先清除本地数据」。

### 6.3 离线写入

- 本地写入：先写 history，再在同一事务里回放该行到 entries；不存在直接 set entries 的路径。
- 锁定/解锁：写一条 `op_type='meta'` 的 history 行，再回放它更新 `entry.locked`；
  不直接改 `entry.locked`。
- 引擎周期处理在本次进程未完成干净同步前跳过；普通 set/inc 照常写。
- 联网后按 6.2 补传。
- 另一台设备已更新同一 key 时：本地待推送行上传后，pull 回来的行按 `(event_at, op_id)`
  决定 entries；旧行只留 history。

### 6.4 快照创建

- **任意已认证客户端都可以创建**（PC / Android 能力对齐），不再限制 PC。
- **空基线自动补**：服务端在首份 push 事务内补 `snapshot_id=0` 的空快照；`head=0` 时由客户端
  直接创建空快照。空快照也是合法快照，`/pull` 不因无快照失败。
- **首份数据快照由第一个客户端创建**：第一个有数据的客户端 pull 到 head、再 push 追平后，
  用回放后的 entries 创建 `snapshot_id=head` 的快照；后续客户端拉取这份快照。
- **后续快照手动刷新**：不再自动周期创建；由用户在 PC 或 Android 上触发 `POST /snapshot`。
- 启动顺序见 §6.1；建快照前必须 `remote_id == head_remote_id` 且 `local_id` 追平本机最新记录。
  push 不要求快照存在，但 push ack 不推进 `remote_id`。
- 快照幂等见 §5.4；快照不修改 history，不删除旧行。

### 6.5 清理

- 先明确历史保证：
  - **全量历史模式**：保留 `remote_id > 最早保留快照的 snapshot_id` 的全部行；
    只清理更早的行。**只有这个模式提供 6.7 的「不丢段」保证。**
  - **快速模式**：只保留 `remote_id > 最新快照的 snapshot_id`；更早行可清理。
    此时历史保证只到最新快照水位，不承诺更早历史。
- 默认采用全量历史模式；存储成本换历史完整性。
- 漏拉检测规则见 §5.3：`since_remote_id < oldest_remote_id - 1` 时报 `history_gap`。
- **快照清理必须跟随 history 清理**：
  - 快照有效条件：`snapshot_id >= oldest_remote_id - 1`（它之后的历史完整保留）；
  - 清理 history 后，删除 `snapshot_id < oldest_remote_id - 1` 的快照，
    避免出现「快照还在、后续历史已删」的不可用基线；
  - 因此「最早有效快照」会随清理前移；全量历史保证以当前的 `oldest_remote_id` 为准。
- 本地：`local_id > profile_sync_v2.local_id` 的行不清理；已推送的历史按 90 天清理。
- **本地 90 天清理是显式保留策略，不影响 §6.7 的云端→本地可拉取完整性**；
  §6.7 只约束仍保留/可拉取的行，不承诺本地永久保留。
- 快照保留最近 20 份；被 history 清理判定失效的快照优先删除；
  有非空快照时优先清理更旧的水位 0 空快照。

### 6.6 历史一一对应的前提

在以下条件同时成立时，云端 `remote_id > 最早保留快照水位` 的行与每个持续同步设备的
本地 history 一一对应：

1. 已有设备从不因云端新快照推进 `remote_id`；
2. 云端不清理最早保留快照之后的 history 行；
3. pull 一页、写 history、推进 `remote_id` 在同一个本地事务里；
4. 本地写入形状合法；过期/无效/未来时间也 accepted，只有形状不合法无法上传；
5. 一次写入一个 `op_id`，重试复用同一个，不复用给另一次写入；
6. 本地库在同步前没有丢失；
7. 设备 `remote_id >= oldest_remote_id - 1`；低于它时 pull 必须返回 `history_gap`，
   客户端重建基线并显式记录缺口，不能静默跳水位。

例外：

- 形状不合法（缺 `op_id`/`key`/`type`、value 既非有限数字也非文本、`op_type` 不在
  `set/regen/reset/meta`，或 meta 行 `new_value` 不是 0/1）的行无法形成
  云端 history，只保留在本地；推送时跳过并记日志。
- 本地库在同步前丢失，未上传行无法恢复；
- 设备水位低于 `oldest_remote_id - 1`：该区间已被清理，pull 报 `history_gap`；
  客户端必须重建基线并记录「这段历史不可得」，这不是静默丢失；
- 重装/新设备从最新快照起步时，本地 history 从该快照水位开始，更早的历史不在本地；
  需要最早快照之后的全量历史，就从最早保留的快照起步。
- entries 是 LWW 结果，可能不包含旧行；**history 包含所有行**，两者不要混淆。

### 6.7 历史不丢失保证

在 6.6 的条件全部成立时：

> 云端 `remote_id > W0`（W0 = `oldest_remote_id - 1`，即仍可从云端连续拉取的起点）的
> 每一行，都会出现在每个已建立该基线且持续同步的设备本地 history 中。

包含：

- 过期、旧周期、重复覆盖的行；
- `event_at` 为空/无法解析的行（存空串，history-only）；
- 未来时间的行（照常入库，回放端不用于 entries）；
- 被 LWW 淘汰、没有更新 entries 的行。

例外见 §6.6。这不等于 entries 一一对应：entries 只保留每个 key 的 LWW 赢家，history 保留全部行。

### 6.8 云端 entries 回放

- push 事务内同步回放：插入 history 和更新 `profile_entries_v2` 在同一事务，失败整体回滚。
  因此实时表始终等于 `head`，不存在异步滞后。
- 回放规则：
  - `set/regen/reset` 行按 `(event_at, op_id)` 取更大者写入 `profile_entries_v2`；
  - `delta` 行及其他未知 `op_type` 跳过，只留在 history；
  - `event_at` 为空/无法解析或大于「该行 `created_at` + 24h」的行，只留在 history，
    不更新实时表；基准用服务端接收时间，与 §2.3 一致；
  - `op_type='meta'` 行直接覆盖 `locked`，不看 `event_at`；同一批按 `remote_id` 升序处理。
  - `remote_id` 在插入事务内分配，跨批次的提交顺序与 `remote_id` 顺序一致，不会出现旧 meta 覆盖新 meta。
- 创建快照事务内：用快照 JSON 重置该账户的实时表，再回放 `remote_id > snapshot_id` 的 tail。
- 冷启动/重建：从最新快照 JSON 装载 entries，再回放 `remote_id > snapshot_id` 的 history。
- 客户端不直接写云端 entries；它只 push history，然后由服务端回放。

## 7. v1 → v2 迁移

### 7.1 云端

1. 部署 v2 表、v2 接口；v1 表与 v1 接口只保留，**网页不再使用 v1**。
2. 网页只读 v2：默认页 `#/profile/entries` 展示实时数据，`#/profile/snapshot` 查看历史快照，
   历史读 `/history`；没有 v2 数据时直接为空，不回退 v1。
   这部分随 v2 接口一起上线；v2 未部署前网页保持 v1，不提前改路由或调用 v2。
3. **云端不做 v1→v2 基线归约**，v2 表初始为空。第一个升级到 v2 的客户端负责：
   - 本地迁移（PC 全量 entries + history；Android 只 entries）；
   - push 迁移出的 history（`profile_sync_v2.local_id=0`）；
   - pull 到 head；
   - 创建第一份 v2 快照。
4. v1 表与接口保持原样，**不主动停用**；新客户端只走 v2。不做 v1→v2 双写或写入转换。
   用户只保留一个真源，旧客户端在 v2 基线之后的写入不会进入 v2。
   发布说明和旧客户端提示必须明确这一点：升级后继续用 v1 写入，数据只留在 v1，
   不会出现在 v2 网页/客户端；避免用户以为记录丢失。
5. 回滚：v1 表未动；切回 v1 前先停 v2 写入，验证后恢复；v2 数据保留。

### 7.2 本地

**PC：**

1. 迁移前把 v1 数据库/旧表导出到 `config/session/_profile-v1-backup-<timestamp>/`
   （下划线开头），**由用户自行删除**；迁移后主库不再读写旧表。
2. **entries 无损迁移**：见 §7.3。
3. **history 迁移**：全部行迁入本地 v2 history；`set/regen/reset` 按 `local_id` 顺序推送，
   `delta` 只作本地只读历史、推送时跳过；v1 的 `tick` 统一改名为 `regen`。
4. 首次 v2 按 §6.1 建立快照：push 迁移出的 history → pull 到 head → 再 push 追平 `local_id`
   → 用回放后的 entries 创建首份快照；`head=0` 时为空快照。
5. 不再读 v1；v1 同步引擎冻结。

**Android：**

1. 新建 v2 三张表；**彻底放弃旧表**，不保留 v1 同步路径。
2. **只迁移 entries（当前值）；不迁移 v1 history 表，直接丢弃**，避免复杂度堆积。
   迁移时保证 `entries.event_at` 非空（规则同 §7.3），否则无法创建快照。
3. 首次 v2：
   - 云端已有快照：拉取快照，按 per-key LWW 合并本机 entries，再从快照水位 pull；
   - 云端没有快照且 `head_remote_id=0`：用本机 entries 创建快照；本机也没有 entries 时创建空快照；
   - 不能只用本机 entries 在 `head>0` 上建快照，否则快照不代表该水位全量；
     `head>0` 时服务端已有自动空快照，按它 pull 到 head 后再建快照。
4. 能力与 PC 对齐：可建快照、可 push/pull、可迁移 entries；只是没有旧历史。

### 7.3 PC 迁移字段映射（迁移脚本单独 review）

entries：

| v1 列 | v2 列 | 说明 |
| --- | --- | --- |
| `value` / `value_text` | `value` | 合并为无类型列：数值 REAL，note TEXT |
| `updated_at` | `event_at` | 已是真实事件时间；realtime 还原小数；为空时退回 `updated_time`，再为空用迁移时间，保证非空 |
| `op_id` | `op_id` | 胜出 op 身份 |
| `locked` | `locked` + meta 行 | 原样迁入；为 1 时生成一条 `op_type='meta'`、old=0/new=1 的待推送 history |
| `updated_time` | — | v2 entries 不再保留本机落库时间 |

history：

| v1 列 | v2 列 | 说明 |
| --- | --- | --- |
| `ts` | `event_at` | 真实事件时间 |
| `old_value` / `new_value` | `old_value` / `new_value` | 无类型列 |
| `old_value_text` / `new_value_text` | 合入 `old_value` / `new_value` | note 文本即值 |
| `change_type` | `op_type` | set/regen/reset；v1 的 tick 统一改名为 regen；delta 不迁入 |
| `delta_value` / `device_id` | 同名 | 原样 |
| `source` | `source` | 原样：变更来源描述 |
| `sync_from` | `sync_from` | 原样：同步来源 key（`model:key`） |
| `origin_local_id` | — | 已编码在 `op_id` 的 `legacy:device:origin` 中 |
| `remote_id` | — | 不迁移；v2 重新推送，`local_id` 从 0 开始 |
| `set/regen/reset` 行 | — | 迁入 v2，按 `local_id` 顺序推送；`op_id` 幂等过滤重复 |
| `delta` 行 | — | 迁入本地 v2 history，只读；推送时跳过 |

- v1 没有独立的 `created_at`；用 `ts` 或迁移时间填充。
- **entries.event_at 必须非空**：`updated_at` 为空时用 `updated_time` 兜底，仍为空用迁移时间；
  否则该条目会让 `POST /snapshot` 整单 400（§5.4 要求 19 位秒串）。
- 迁移后 `set/regen/reset` 行重新推送，`delta` 跳过；`local_id` 从 0 开始；v1 `remote_id` 不沿用。
- 若胜出 op 的 `op_id` 为空，entries 保留空串；它在同秒平局中输给任何非空 `op_id`，
  这是可接受的：历史行仍保留，新的真实写入优先；不要合成假 `op_id`。

迁移脚本 review 清单：

1. 行数：v1 entries/history 与 v2 一致（全部迁入）；`delta` 只读、不推送。
2. 字段抽查：value、old/new、op_type、source、sync_from、device_id、event_at（entries 非空）逐条对比。
3. realtime 小数还原：迁移前后当前值误差 < 1e-6。
4. note 文本：进入单 `value` / `old_value` / `new_value`，没有被转成数字。
5. locked 原样迁入，locked=1 的 key 生成一条待推送的 meta 行；op_id 唯一；
   `profile_sync_v2.local_id=0`。
6. 迁移可重入：重复执行不产生重复行；失败回滚、备份保留。
7. 迁移后不再读 v1 表。

### 7.4 客户端切换

1. 新客户端**不再回退 v1**；`GET /state` 不是 2 时提示服务端未升级，Profile 同步暂停，
   文档/定义/用户等其他通道照常同步。
2. 服务端 v2 可用后，客户端首次同步按 §6.1 建立快照。
3. v1 客户端可以继续存在，但不再是新客户端的真源；新客户端只走 v2。
   旧客户端提示必须明确：v1 写入不会进入 v2，需要继续同步请升级。

## 8. 从 v1.5 删除/冻结的内容

v2 上线后，以下不再进入新路径：

- v1.5 的 `remote_id` 领段与空洞处理；v2 统一用插入时分配的 `remote_id`。
- v1.5 的 `snapshot_id` 旧语义、pre-snapshot 过滤、`stale_base`、base_remote_id。
- v2 的 delta：不接收、不下发、不回放；只作本地只读历史。
- `_period_relation`、周期归零/恢复写账本。
- 快照候选比较、`remote_id=-1`、`reopen_abandoned`、abandon/reject 分流。
- 客户端 rebase 分支、`state_problem`、协议分阶段开关。
- 服务端 `profile_baseline.mjs` 不再使用；v1→v2 由 PC 客户端迁移，云端 v2 从空开始。
- 网页历史窗口回放；旧值直接读 history 列。
- `old_value` 由服务端重建；改为客户端上传、云端原样存。
- `value_text` / `old_value_text` / `new_value_text` 字段；统一为单 `value` /
  `old_value` / `new_value`（无类型列 + `type` 判别）。

## 9. 风险与取舍

1. **快照可信度**：任何已认证客户端（PC / Android）都可上传快照，服务端只校验水位和字段。
   若担心错误快照：
   - 服务端按 `remote_id > snapshot_id` 折叠校验；或
   - 对快照上传加设备白名单。
2. **future / 并发 set**：未来时间戳照常入库，回放时只进 history；同一 key 并发 set 后到覆盖。
   这两条都是 set-only 的预期行为，见 §2.3。
3. **v1/v2 并存**：v1 接口不主动停用，新客户端只走 v2；用户只保留一个真源。
   旧客户端在 v2 基线之后的写入不会进入 v2，也不做转换。
4. **云端实时表回放**：`profile_entries_v2` 是 history 的物化缓存，在 push 事务内同步回放，
   失败整体回滚（history 也不落库），因此实时表始终等于 head。
   `/entries` 只在 `as_of == head` 读实时表，更早水位走折叠；不引入 `entries_remote_id` 水位。
5. **快照新鲜度**：网页矩阵读 `/entries` 的实时表/折叠结果，不依赖 PC 快照的实时性；
   快照只影响折叠成本。

## 10. 验收标准

1. **提交序水位**：pull 用 `remote_id`；并发 push 不会出现低 id 晚可见导致漏拉。
2. **全部接受**：过期、无效、未来 `event_at` 也 accepted 并入库；只有形状不合法才 rejected。
3. **本地回放**：所有行进 history；只有 `(event_at, op_id)` 更大且事件时间有效的行更新 entries。
4. **离线补传**：手机离线产生 200 条，联网后全部取得 `remote_id`；PC/网页可查。
5. **快照重建**：新设备取快照 + `remote_id > snapshot_id` 的 set，得到与源设备一致的 entries；
   取最新快照时历史从该水位开始，取最早保留快照时可得到该水位之后的全量历史。
6. **旧值展示**：网页历史页直接显示上传的 `old_value/new_value`，不依赖窗口回放。
7. **周期引擎**：未完成干净 pull + push 时，`ProfileEngine` 不执行周期处理。
8. **v1 保留**：v1 表/接口原样可用；新客户端只走 v2，不依赖 v1。
9. **无 v1.5 机制**：v2 不出现领号式 `remote_id`、`delta`、快照候选比较、`-1`、rebase。
10. **清理与漏拉检测**：全量历史模式保留最早快照水位之后的全部行；`/state` 返回
    `oldest_remote_id`；pull 在 `since_remote_id < oldest_remote_id - 1` 时返回 `409 history_gap`，
    客户端重建基线并显式记录缺口，不静默跳水位。
    清理 history 时同步删除 `snapshot_id < oldest_remote_id - 1` 的快照，
    不留下「快照还在、尾部历史已删」的不可用基线。
11. **重复上传**：同一 `op_id` 在对应行仍存在时返回 duplicate + 已有 `remote_id`；
    行已被清理时按新行接受并分配新 `remote_id`（允许重复内容，客户端按本地 `op_id` 去重）。
12. **单 value 类型**：数值写入后 `typeof(value)='real'`、JSON 是 number；note 写入后
    `typeof(value)='text'`、JSON 是 string；历史 old/new 同理。
13. **已有设备不跳水位**：云端出现新快照时，本地 `remote_id` 只按 pull 页推进；
    本地 history 与云端 `remote_id >= oldest_remote_id` 的行一一对应（形状不合法/物理丢失除外）。
    快照失效时若 `remote_id >= oldest_remote_id - 1`，只切换 `snapshot_ref_id`/`snapshot_id`，
    不得推进 `remote_id`；只有 `history_gap` 才重建基线。
14. **原子性**：pull 一页、写 history、推进 `remote_id` 在同一本地事务；崩溃重放不丢不重。
15. **rejected 例外与处理**：只有形状不合法（缺 `op_id`/`key`/`type`、value 非有限数字/文本、
    `op_type` 非法、meta 行 `new_value` 非 0/1）才是 rejected；过期、无效、未来时间都不算。
    客户端跳过 rejected 行并推进 `local_id`，只记日志，不保存逐行状态。
16. **历史不丢段**：基线 W0 后，云端 `remote_id > W0` 的每一行都出现在已建立基线设备的本地
    history，包含过期、无效、未来、LWW 淘汰行。
17. **网页 v2 化**：网页只读 v2；v1 接口保留但网页不再使用；没有 v2 数据时网页为空。
18. **PC 迁移范围**：v1 entries/history 全部迁入 v2；`delta` 只作本地只读历史，不推送；
    行数、old/new、op_type、source、sync_from、device_id、event_at（entries 非空）抽查一致；
    迁移后 `profile_sync_v2.local_id=0`。
19. **Android 迁移范围**：只迁 entries，不迁 v1 history；旧表彻底放弃；可建快照、
    可 push/pull，能力与 PC 对齐。
20. **快照契约**：`/state` 始终 200；无快照只可能是 `head=0`，返回
    `error_code=PROFILE_SNAPSHOT_REQUIRED` / `next_action=create_snapshot`，客户端创建空快照。
    首份 push 自动补 `snapshot_id=0` 的空快照，`head>0` 必有快照，`/pull` 不以快照为前置。
    新客户端先 push 待推送 history、再取基线、pull 到 head、再 push 追平 `local_id`，
    然后创建/复用 `snapshot_id=head` 的数据快照。
    快照失效返回 `410 snapshot_expired`：本地水位仍有效时只切快照引用、不推进 `remote_id`，
    落入 `history_gap` 才重建基线并记录缺口；后续快照手动刷新。
21. **迁移脚本单独 review**：迁移脚本与同步逻辑分开评审；通过 §7.3 清单。
22. **PC 备份目录**：旧库/旧表导出到 `config/session/_profile-v1-backup-<timestamp>/`，
    由用户自行删除。
23. **回滚**：v1 表未动；停写 v2 后可切回 v1，备份完整。
24. **as_of 语义**：`/entries` 缺省 `as_of_remote_id=head`；超前返回 400；过旧且无可用快照返回
    409 `snapshot_too_old`；尾巴过大返回 200 + `stale=true`，网页退化为最新快照 + 过期标记。
25. **快照校验**：按 §5.4 的六项形状校验；不校验 cap/周期等业务范围。
26. **旧客户端可见性**：v1 在 v2 基线后的写入只留在 v1，不进入 v2；发布说明/客户端提示
    必须明确。
27. **快照接口**：`/snapshot` 支持 `latest/earliest/list/id`；`reused` 仅同水位且 entries
    排序后哈希一致（数值按 `toFixed(6)` 归一化），不同 entries 新建快照。
28. **空账户**：`/entries` 在 `head=0` 且无快照时 200 空；`head>0` 必有快照（首份 push 自动补）；
    stale 时 200 + `stale=true`。
29. **历史分页**：`/history` 的 `before_remote_id/limit/username/device_id` 生效，
    `next_before` 正确。
30. **快照全量前提**：创建快照前必须 `remote_id == head_remote_id`、`local_id` 追平本机最新记录，
    且 `snapshot_id == head_remote_id`；
    多端并发首启反例：只 push 101..150 未 pull 1..100 时，不得创建水位 150 的快照。
31. **全量补传退避**：PC 迁移几千行时分批 push，遇 429/5xx 按 `Retry-After`/指数退避
    分轮补传，不阻塞 pull、不清 `local_id`；`push ack` 不推进 `remote_id`。
32. **本地无 account_id**：本地 entries/history/sync 三张表都不含 `account_id`；
    切换云端账号只重置同步水位，不修改 entries/history。
33. **locked 同步**：A 锁 key → 写 `op_type='meta'`、old=0/new=1 的 history 行并上传；
    B pull 后 `entry.locked=1`；B 解锁同理。快照 entries 带 `locked`；
    回放不做仲裁，按 `remote_id` 顺序取最新。
34. **云端实时表**：push 后云端 `profile_entries_v2` 的 value/locked 与
    「快照 JSON + history 回放」结果一致，且 `event_at` 为空/超前的行只进 history、不进实时表；
    删表后能从快照 + history 重建；客户端不直接写该表。
35. **拉推互不影响**：pull 写入的远端行占用本地 `local_id` 但不推进推送水位；
    本地未推送的旧行仍按 `local_id` 顺序上传；不要求「先拉后推」。

## 11. 实施顺序

1. 云端：v2 表（history / entries / snapshots）+ `/state`、`/push`、`/pull`、`/snapshot`、
   `/entries`、`/history`；push 事务内同步回放 `profile_entries_v2`，
   `/entries` 仅在 `as_of == head` 读实时表，更早水位折叠快照 + tail；
   首份 push 事务自动补 `snapshot_id=0` 的空快照，`/pull` 不以快照为前置；
   `/state` 无快照（仅 head=0）返回 `PROFILE_SNAPSHOT_REQUIRED`；
   网页只读 v2；v1 接口原样保留。
2. PC：v2 三张表 + entries/history 无损迁移脚本（单独 review）；旧库/旧表导出到
   `config/session/_profile-v1-backup-<timestamp>/`，用户自行删除。
3. Android：v2 三张表 + entries 迁移；不迁 history；旧表彻底放弃。
4. 客户端：v2 repository（entries/history/local_id）+ 同步循环 + 首份数据快照自动创建，
   后续手动刷新。
5. 网页：矩阵改 `/entries`；历史页直接读 v2 old/new；无 v2 数据时为空。
6. 端到端验证：PC 迁移行数与字段、Android 无旧历史、空基线自动补、首份数据快照、
   空账户文档仍同步、历史一一对应、过期/无效/未来行保留、重复上传、周期引擎闸门。
7. 清理 v1.5 死代码；保留 v1 接口与备份。

