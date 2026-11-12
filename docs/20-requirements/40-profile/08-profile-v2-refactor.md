# Profile v2 重构：本地/云端两套表与 v2 接口

> 状态：规划中（未实施）。最后更新：2026-11-12。
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
本地：entries（当前值） + history（全部记录） + sync（水位）
云端：history（全部 set） + snapshots（基线） + 只读的 entries 折叠
```

**云端不做业务裁决，只接受数据、分配提交序号、存快照。**
**本地回放只做一条规则：`(event_at, op_id)` 更大的更新 entries，其余只进 history。**

## 2. 核心模型

### 2.1 只有 set

- 所有写入都是绝对值 `set`。
- 不再有 `delta` op、不再有 tick/reset 上传、不再有周期物化写入。
- regen/quota 的周期变化继续是读时函数；只有真实写入才进 history。

### 2.2 提交序号用 `history.id`，不用 `remote_id`

v2 云端 `profile_history_v2.id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`，由 SQLite 在
**插入时**分配，代表提交顺序：

- `id <= W` 的行都已经提交可见；
- 不会出现更低的 `id` 在之后才可见；
- 快照绑定 `snapshot_history_id = W`，客户端只拉 `id > W`；
- 空洞无害，`id` 不要求连续。

v1 的 `remote_id` 是「领号时分配」，领号和插入是两个事务，低号可能晚于高号提交；因此
`snapshot_remote_id + 后续行` 不保证覆盖全部数据。v2 彻底去掉 `remote_id`。

### 2.3 本地 entries 的裁决

```text
拉到/写入的每一行都进 history
if (event_at, op_id) > entry 的 (event_at, op_id):
    entry 更新
else:
    entry 不动，只保留记录
```

- 过期 set 也允许上传、允许入库；它只影响 history，不影响 entries。
- `event_at` 为空/无法解析，或明显超前（> 24 小时）的行：**仍然进 history**，
  但不更新 entries；避免坏钟压住 key，同时不丢历史。
- 同一用户不会并发 `inc -1`（游戏前提），所以不需要分布式计数器。
- 同一 key 的两台设备并发 `set`：后者覆盖，history 两条都保留。

### 2.4 快照

- 快照 = 某个 `history.id` 水位上的全量 entries，用于新设备/长离线设备快速到最新。
- 快照不参与裁决；它只是「从 W 开始回放」的基线。
- 任意已认证客户端（PC / Android，能力对齐）在完成一次干净 pull + push 后上传快照：
  `snapshot_history_id = 已应用到的最大 history.id`。
- **快照管状态，水位管历史**：
  - 新设备（本地 history 为空）：选择基线。
    - 用最新快照：最快到最新，但该快照水位之前的历史不可得；
    - 用最早保留的快照：可拿到该水位之后的全量历史，但要多回放。
  - 已有本地 history 的设备：**不得用快照推进 `history_id`**，继续从本地水位 pull；
    快照条目最多作为 entries 的候选合并，不改变历史水位。
- 快照水位之前的 history 可按保留期清理，但只有在不承诺「最早快照之后全量历史」时才允许；
  否则只清理更早的行。

### 2.5 周期引擎

- `ProfileEngine` 只在**本次进程完成一次干净 pull + push** 之后执行周期处理。
- 周期值仍然读时计算，不写 entries、不写 history。
- 没有 `profile_inc` 写闸门；离线写入照常，进入本地 outbox。

## 3. 本地数据库 v2

旧表不删除，保留为 `_v1` 备份；新代码只读写 v2 表。

### 3.1 `profile_entries_v2`（当前值）

```sql
CREATE TABLE profile_entries_v2 (
    account_id   TEXT NOT NULL,
    username     TEXT NOT NULL,
    type         TEXT NOT NULL,            -- quota / regen / stock / note
    key          TEXT NOT NULL,
    value        NOT NULL DEFAULT 0,       -- 无类型声明：数值存 REAL，note 存 TEXT
    event_at     TEXT NOT NULL DEFAULT '', -- 胜出写入的真实事件时间
    op_id        TEXT NOT NULL DEFAULT '', -- 胜出写入的 op_id
    locked       INTEGER NOT NULL DEFAULT 0, -- 本机锁，不同步
    updated_time TEXT NOT NULL DEFAULT '',   -- 本机落库时间，仅诊断
    PRIMARY KEY (account_id, username, type, key)
);
```

- `locked` 是每台设备自己的物理禁用，永远不进云端。
- `value` 是**无类型列**：数值模型存 REAL，note 存 TEXT；不要改成 `REAL` 或 `TEXT`。
- 没有 `value_text`；note 的文本就是 `value`。
- 没有 `origin` 列；来源只在 history 行里。

### 3.2 `profile_history_v2`（全部记录 + 本地 outbox）

```sql
CREATE TABLE profile_history_v2 (
    local_id        INTEGER PRIMARY KEY AUTOINCREMENT, -- 本地顺序
    account_id      TEXT NOT NULL,
    op_id           TEXT NOT NULL,                     -- 客户端 uuid，幂等键
    username        TEXT NOT NULL,
    type            TEXT NOT NULL,
    key             TEXT NOT NULL,
    kind            TEXT NOT NULL DEFAULT 'set',       -- 展示：set/delta/tick/reset（旧行）
    old_value,                                         -- 无类型：数值 REAL / note TEXT
    new_value,                                         -- 同上
    delta_value     REAL,                              -- 展示用；note 为 NULL
    source          TEXT NOT NULL DEFAULT '',
    sync_from       TEXT NOT NULL DEFAULT '',          -- 旧版同步来源，仅展示
    device_id       TEXT NOT NULL DEFAULT '',
    event_at        TEXT NOT NULL,                     -- 真实事件时间，无时区秒串
    recorded_at     TEXT NOT NULL,                     -- 本机落库时间
    cloud_id        INTEGER,                           -- 云端 history.id；NULL = 待上传
    upload_state    TEXT NOT NULL DEFAULT 'pending',   -- pending / synced / rejected / local_only
    last_error      TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX profile_history_v2_op
    ON profile_history_v2(account_id, op_id);
CREATE INDEX profile_history_v2_pending
    ON profile_history_v2(account_id, local_id)
    WHERE cloud_id IS NULL AND upload_state = 'pending';
CREATE INDEX profile_history_v2_key
    ON profile_history_v2(account_id, username, type, key, local_id DESC);
```

- 本地 `local_id` 是本地顺序，`cloud_id` 是云端 `profile_history_v2.id`，两者不是同一个东西。
- 本地 history 同时是 outbox：`cloud_id IS NULL AND upload_state='pending'` 即待上传。
- `upload_state`：`pending`（待上传）/ `synced`（已 ack）/ `rejected`（形状不合法）/
  `local_only`（旧 tick/reset/unscoped，只保留历史、不上传）。
- `device_id = 本机` 的行才上传；远端回放行 `cloud_id` 已有值，不会被代传。
- `upload_state='rejected'` 保留行和错误，不删除；用户修正后可手动重试。
- rejected 行的 `op_id` 语义：
  - 只修格式问题（例如 `event_at` 串）→ 复用原 `op_id` 重试；
  - 改内容（value/old/new/key/type）→ 视为一次新写入，生成新 `op_id`、新行；
    原 rejected 行保留为本地历史（`local_only`），不再上传。
- `old_value/new_value` 对本地写入由写入前的 entry 计算；对远端行直接使用云端上传的值。
  数值模型是 REAL，note 是 TEXT。

### 3.3 `profile_sync_v2`（水位与快照）

```sql
CREATE TABLE profile_sync_v2 (
    account_id          TEXT PRIMARY KEY,
    history_id          INTEGER NOT NULL DEFAULT 0, -- 已应用到的云端 history.id
    snapshot_id         INTEGER NOT NULL DEFAULT 0, -- 最近应用的快照主键
    snapshot_history_id INTEGER NOT NULL DEFAULT 0, -- 该快照覆盖到的 history.id
    protocol_version    INTEGER NOT NULL DEFAULT 2,
    last_sync_at        TEXT NOT NULL DEFAULT ''
);
```

- 只有 `history_id` 一个拉取水位；没有 `remote_id`、没有 `local_history_id`。
- `snapshot_history_id` 用于判断是否需要先落地快照。
- 不再把同步状态放 `account.json`；与 history 同库，掉电不会出现「行写了、水位没写」。

## 4. 云端数据库 v2

v2 只新增两张数据表 + 一张账户版本元数据表；不新建 entries 表。

### 4.1 `profile_history_v2`（只追加的 set 日志）

```sql
CREATE TABLE profile_history_v2 (
    id             INTEGER PRIMARY KEY AUTOINCREMENT, -- 提交顺序 / 拉取水位
    account_id     TEXT NOT NULL,
    op_id          TEXT NOT NULL,
    username       TEXT NOT NULL,
    type           TEXT NOT NULL,
    key            TEXT NOT NULL,
    kind           TEXT NOT NULL DEFAULT 'set',  -- 展示：set/delta/tick/reset（旧行）
    new_value,                                  -- 无类型：数值 REAL / note TEXT
    old_value,                                  -- 同上
    delta_value    REAL,                        -- 展示用；note 为 NULL
    source         TEXT NOT NULL DEFAULT '',
    sync_from      TEXT NOT NULL DEFAULT '',    -- 旧版同步来源，仅展示
    device_id      TEXT NOT NULL DEFAULT '',
    event_at       TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX profile_history_v2_op
    ON profile_history_v2(account_id, op_id);
CREATE INDEX profile_history_v2_pull
    ON profile_history_v2(account_id, id);
CREATE INDEX profile_history_v2_key
    ON profile_history_v2(account_id, username, type, key, id);
```

- 没有 `op` 列：每行都是 set。
- 没有 `remote_id`、没有 `delta` 同步语义；`delta_value` 仅展示。
- `new_value/old_value` 是无类型列：数值模型存 REAL，note 存 TEXT。
- 服务端只做：形状校验、`op_id` 幂等、插入时分配 `id`。
- **所有形状合法的行都接受**：过期、`event_at` 为空/非法、未来时间都不拒绝，原样入库；
  能否解析成事件时间由回放端判断。
- `event_at` 能解析就存 canonical 秒串；不能解析存空串；空串在回放端视为 history-only。
- `device_id` 取会话设备，不由客户端正文决定。

### 4.2 `profile_snapshots_v2`（基线）

```sql
CREATE TABLE profile_snapshots_v2 (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          TEXT NOT NULL,
    snapshot_history_id INTEGER NOT NULL, -- 覆盖到的 profile_history_v2.id
    origin_device       TEXT NOT NULL DEFAULT '',
    origin_name         TEXT NOT NULL DEFAULT '',
    entry_count         INTEGER NOT NULL DEFAULT 0,
    entries             TEXT NOT NULL,    -- JSON 数组
    created_at          TEXT NOT NULL
);
CREATE INDEX profile_snapshots_v2_latest
    ON profile_snapshots_v2(account_id, id DESC);
```

`entries` 每条：

```json
{
  "username": "甲",
  "type": "stock",
  "key": "钱袋",
  "value": 123.5,
  "event_at": "2026-11-12T12:00:00",
  "op_id": "uuid"
}
```

note 的 `value` 是字符串：

```json
{"username": "甲", "type": "note", "key": "备注", "value": "主玩会心双刀",
 "event_at": "2026-11-12T12:00:00", "op_id": "uuid"}
```

- `snapshot_history_id` 必须是上传方已经拉到的 `id`，服务端校验 `<= 当前 head`。
- 快照由客户端生成；服务端不折叠、不仲裁。
- 保留最近 20 份，旧快照可清理。

### 4.3 `profile_accounts_v2`（账户版本）

```sql
CREATE TABLE profile_accounts_v2 (
    account_id          TEXT PRIMARY KEY,
    version             INTEGER NOT NULL DEFAULT 2,
    baseline_snapshot_id INTEGER NOT NULL DEFAULT 0,
    migrated_at         TEXT NOT NULL
);
```

- 用于判断账户是否已经完成 v2 迁移。
- v1 接口保持原样，**不主动停用**；新客户端只走 v2，用户只保留一个真源。
- 不做 v1/v2 双写，避免两套数据分叉。

### 4.4 为什么云端没有 `entries` 表

v2 的云端只负责「接受全部 set + 存基线」，不维护当前值：

- `GET /api/v2/profile/entries` 是**只读折叠**：取最新快照，再折叠
  `id > snapshot_history_id` 的行，按 `(event_at, op_id)` 取每 key 最新。
- 快照足够新时，折叠的尾巴很小；不需要云端写 entries。
- 如果以后网页需要更低延迟，可以把折叠结果缓存成 `profile_entries_v2`，那是纯读模型优化，
  不改变写入协议。

### 4.5 history 字段对齐

本地与云端的 `profile_history_v2` 共享同一组业务字段，差异只在「行身份 / 时钟 / outbox」：

| 分组 | 字段 | 说明 |
| --- | --- | --- |
| 共享 | `op_id` | 客户端 uuid，幂等键 |
| 共享 | `username` / `type` / `key` | 定位 |
| 共享 | `kind` | 展示标签：set/delta/tick/reset；新写入恒为 set，不参与仲裁 |
| 共享 | `old_value` / `new_value` | 旧值 / 新值；数值是 REAL，note 是 TEXT |
| 共享 | `delta_value` | 展示用变动量；note 为 NULL |
| 共享 | `source` / `sync_from` / `device_id` / `event_at` | 来源、旧同步来源、设备、真实事件时间 |
| 本地专有 | `local_id` | 本地自增顺序 |
| 本地专有 | `cloud_id` | 对应的云端 `profile_history_v2.id` |
| 本地专有 | `upload_state` / `last_error` | outbox 状态与拒绝原因 |
| 本地专有 | `recorded_at` | 客户端落库时间 |
| 云端专有 | `id` | 服务端提交顺序 / 拉取水位 |
| 云端专有 | `created_at` | 服务端接收时间 |
| 两边都有 | `account_id` | 云端单独维护，不参与对齐 |

注意：

- `local_id` 不是云端 `id`；同步映射只认 `cloud_id`。
- `recorded_at` 与 `created_at` 是不同时钟，不能互相覆盖。
- 快照 entries 是「当前值」，使用单 `value`；history 行使用
  `old_value/new_value`，两者不要混用。
- v2 push/pull 的业务字段统一叫 `new_value`；note 的文本也放在 `new_value`。

### 4.6 单 value 的数据库约束

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
6. 增加启动/迁移自检：插入一个 float 和一个字符串，断言 `typeof` 分别是
   `real`/`text`，JSON 输出分别是 number/string。

## 5. v2 接口

统一前缀 `/api/v2/profile`；认证沿用现有 Bearer 会话。v1 `/api/sync/profile/*` 保持原样，
但新客户端只走 v2，不做 v1/v2 双写。

### 5.0 通用约定

- 认证：`Authorization: Bearer <access_token>`；缺失/过期 → `401 unauthorized`。
- 请求/响应：`application/json; charset=utf-8`；未知字段忽略。
- 账户：`account_id` 从会话取，正文不得传；`device_id` 从会话取。
- 时间：`event_at` / `created_at` / `server_time` 为 `YYYY-MM-DDTHH:MM:SS` 无时区秒串。
- ID：`head_id` / `oldest_history_id` / `history_id` / `snapshot_id` 为整数；
  `snapshot_history_id` 为整数；`latest_snapshot_id` 可为 `null`。
- 分页：`limit` 为整数；pull 默认 500、最大 1000；history 默认 100、最大 1000。
- 错误信封：

```json
{"error": "snapshot_expired", "error_code": "PROFILE_SNAPSHOT_EXPIRED",
 "message": "快照已被清理", "details": {"latest_snapshot_id": 8}}
```

  关键业务字段（`latest_snapshot_id` / `oldest_history_id` / `head_id`）同时放在顶层，
  便于客户端直接读取。

| HTTP | `error` | `error_code` | 场景 |
| --- | --- | --- | --- |
| 400 | `invalid_request` | `PROFILE_INVALID_REQUEST` | 参数/形状不合法 |
| 400 | `invalid_as_of` | `PROFILE_INVALID_AS_OF` | `as_of` 超前/非整数 |
| 401 | `unauthorized` | `PROFILE_UNAUTHORIZED` | 未登录/令牌失效 |
| 409 | `snapshot_required` | `PROFILE_SNAPSHOT_REQUIRED` | 云端还没有可用快照 |
| 409 | `history_gap` | `PROFILE_HISTORY_GAP` | 客户端水位落入已清理区间 |
| 409 | `snapshot_too_old` | `PROFILE_SNAPSHOT_TOO_OLD` | `as_of` 早于所有有效快照 |
| 410 | `snapshot_expired` | `PROFILE_SNAPSHOT_EXPIRED` | 快照不存在或尾部历史已清理 |
| 413 | `payload_too_large` | `PROFILE_PAYLOAD_TOO_LARGE` | 批量/快照超过限制 |
| 429 | `rate_limited` | `PROFILE_RATE_LIMITED` | 频率限制 |
| 500 | `internal` | `PROFILE_INTERNAL` | 服务端异常 |

- 幂等：push 按 `(account_id, op_id)`；snapshot 按 `(snapshot_history_id, entries 哈希)`。
- `snapshot_stale` 不是 HTTP 错误：`/entries` 返回 200 + `stale=true`，见 §5.6。


### 5.1 `GET /api/v2/profile/state`

```json
{
  "protocol_version": 2,
  "head_id": 12345,
  "oldest_history_id": 1,
  "latest_snapshot_id": 7,
  "snapshot_history_id": 12000,
  "snapshot_required": false,
  "snapshot_state": "ready",
  "error_code": null,
  "next_action": "pull",
  "server_time": "2026-11-12T12:00:00"
}
```

客户端先读它；`protocol_version != 2` 或账户未迁移时停留在 v1。

- `oldest_history_id`：云端仍保留的最小 `profile_history_v2.id`；0 表示没有历史行。
  客户端用它检测自己的水位是否已经落到已清理区间。
- `snapshot_required` / `snapshot_state`：与 `/pull` 的 409 使用同一业务码。新客户端先看这里：
  - `true` / `none`：没有可用快照，必须先 `POST /snapshot`（有本地 entries 用本地，
    没有则空快照），**不要直接 pull**；
  - `false` / `ready`：先 `GET /snapshot` 取基线，再 pull。

请求参数：无。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `protocol_version` | int | 固定 2；不是 2 时客户端停止 Profile 同步 |
| `head_id` | int | 当前最大 `history.id`；0 表示无历史 |
| `oldest_history_id` | int | 仍保留的最小 `history.id`；0 表示无历史 |
| `latest_snapshot_id` | int \| null | 最新快照主键 |
| `snapshot_history_id` | int \| null | 最新快照覆盖的 `history.id` |
| `snapshot_required` | bool | `true` 表示没有可用快照 |
| `snapshot_state` | `"ready"` \| `"none"` | 快照状态 |
| `error_code` | string \| null | 无快照时为 `PROFILE_SNAPSHOT_REQUIRED` |
| `next_action` | `"pull"` \| `"create_snapshot"` | 客户端下一步 |
| `server_time` | string | 服务端当前秒串 |

错误：`401 unauthorized`、`500 internal`。不存在 v2 时返回 404，客户端不得回退 v1。

### 5.1.1 首次启动无快照的契约

`/state` 本身始终返回 **HTTP 200**（它是状态查询，不是失败），但用业务错误码明确引导：

```json
{
  "protocol_version": 2,
  "head_id": 0,
  "oldest_history_id": 0,
  "latest_snapshot_id": null,
  "snapshot_history_id": null,
  "snapshot_required": true,
  "snapshot_state": "none",
  "error_code": "PROFILE_SNAPSHOT_REQUIRED",
  "next_action": "create_snapshot"
}
```

错误码 / 引导：

| 字段 | 值 | 含义 |
| --- | --- | --- |
| `error_code` | `PROFILE_SNAPSHOT_REQUIRED` | 云端还没有任何可用快照 |
| `next_action` | `create_snapshot` | 客户端必须先创建快照 |
| `snapshot_required` | `true` | 等价信号，便于旧逻辑兼容 |
| `latest_snapshot_id` | `null` | 没有可拉取的基线 |

客户端流程：

1. `GET /state` → 看到 `error_code=PROFILE_SNAPSHOT_REQUIRED`。
2. 本机有 entries：先 push 本机 pending history（push 不受快照缺失阻塞），
   把 `history_id` 推进到本机行返回的最大 `cloud_id`；然后 `POST /snapshot`，
   `snapshot_history_id = history_id`，entries 用当前值。本机没有 entries：提交空 entries 快照。
3. `POST /snapshot` 返回：
   - `reused=false`：本机成为首份快照创建者；
   - `reused=true`：其他客户端已经先创建，直接使用返回的 `id` / `snapshot_history_id`。
4. 用返回的快照设置本地 `snapshot_id` / `snapshot_history_id` / `history_id`，然后 `/pull`。
5. 如果 `/pull` 仍返回 `409 snapshot_required`（并发窗口），重新拉 `/state` 并复用最新快照，
   不要反复创建。

`/pull` 在无快照时返回 **HTTP 409**，与 `/state` 使用同一个 `error_code`：

```json
{"error": "snapshot_required", "error_code": "PROFILE_SNAPSHOT_REQUIRED",
 "latest_snapshot_id": null, "snapshot_state": "none"}
```

### 5.2 `POST /api/v2/profile/push`

```json
{
  "ops": [
    {
      "op_id": "uuid",
      "username": "甲",
      "type": "stock",
      "key": "钱袋",
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
- 服务端按 `(account_id, op_id)` 幂等；重复返回已有 `history_id` 和 `duplicate`。
- 每批响应：

```json
{
  "results": [
    {"op_id": "uuid", "history_id": 12346, "status": "accepted"},
    {"op_id": "uuid2", "history_id": 12000, "status": "duplicate"}
  ],
  "head_id": 12346
}
```

- 不要求先拉、不要求快照、不检查 base。过期 set 照常 accepted。
- `event_at` 为空/非法/未来**不是拒绝理由**：照常入库，由回放端决定是否更新 entries。
- 只有形状不合法（缺 `op_id`/`key`/`type`，或 value 既非有限数字也非文本）才拒绝；
  这类行无法形成 history 记录，客户端保留本地并标 `rejected`。
- PC 迁移出的历史会带 `kind` / `sync_from` 展示字段；新写入 `kind='set'`、`sync_from=''`。
  这两个字段不参与仲裁。
- `event_at` 严格按 `YYYY-MM-DDTHH:MM:SS` 解析；不合格式时存空串（history-only），
  不保存原始非法串。
- `delta_value` 不校验是否等于 `new_value - old_value`；它是展示字段，由客户端负责。
- `op_id` 去重只在对应行仍存在时生效：行被清理后，同一 `op_id` 重推会按新行接受、
  分配新 `history_id`；客户端以本地唯一 `op_id` 去重，云端允许出现重复内容行。

请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `ops` | array | 是 | 1..200 条 |
| `op_id` | string | 是 | 8..80；账户内唯一；不以 `unscoped:` 开头 |
| `username` | string | 是 | 1..80 |
| `type` | string | 是 | 1..20；`quota/regen/stock/note` |
| `key` | string | 是 | 1..120 |
| `new_value` | number \| string | 是 | 数值模型为有限 number；note 为 string；不接受 NaN/Inf |
| `old_value` | number \| string \| null | 否 | 展示用；note 为 string |
| `delta_value` | number \| null | 否 | 展示用；note 为 null；不校验 `new-old` |
| `kind` | string | 否 | 默认 `set`；展示标签 `set/delta/tick/reset` |
| `source` | string | 否 | 0..200 |
| `sync_from` | string | 否 | 0..200；展示用 |
| `event_at` | string | 是 | 19 位秒串或空串；非法/空存空串（history-only） |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `results` | array | 与请求 `ops` 一一对应 |
| `results[].op_id` | string | 对应请求 |
| `results[].history_id` | int \| null | accepted/duplicate 为正整数；rejected 为 null |
| `results[].status` | `"accepted"` \| `"duplicate"` \| `"rejected"` | 结果 |
| `results[].error` | string | 仅 rejected；`invalid_shape` / `invalid_op_id` / `invalid_value` |
| `head_id` | int | 本批处理后的最大 `history.id` |

HTTP：整批 200 即返回逐条结果；整批形状错误 400；未登录 401；超大 413；限流 429。
`accepted` 与 `duplicate` 都必须带可用 `history_id`；客户端据此写 `cloud_id`。
同一批内重复 `op_id`：第一条 accepted，后续 duplicate；`results` 与请求 `ops` 同序。

### 5.3 `GET /api/v2/profile/pull?since_id=&snapshot_id=&limit=`

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `since_id` | int | 否 | 默认 0；>= 0 |
| `snapshot_id` | int | 否 | 本地快照主键；用于失效校验 |
| `limit` | int | 否 | 1..1000，默认 500 |

- 新客户端没有本地基线时**不应直接调用 pull**；先 `/state` → `/snapshot`。
  如果仍然调用且云端没有快照，返回 `409 snapshot_required`（见 §5.1.1）。
- `snapshot_id` 可选：客户端带上本地快照 id 做有效性校验。
  - 快照不存在，或 `snapshot_history_id < oldest_history_id - 1`（尾部历史已被清理）→
    `410 snapshot_expired`：
    ```json
    {"error": "snapshot_expired", "latest_snapshot_id": 8, "head_id": 12345,
     "oldest_history_id": 1}
    ```
    客户端自动切换到最新快照，设置新的 `snapshot_id` / `snapshot_history_id`，
    把 `history_id` 推进到该快照水位，记录历史缺口，然后继续 pull。
  - 快照有效 → 正常返回增量。

```json
{
  "ops": [
    {
      "id": 12346,
      "op_id": "uuid",
      "username": "甲",
      "type": "stock",
      "key": "钱袋",
      "new_value": 123.5,
      "old_value": 120,
      "delta_value": 3.5,
      "source": "OCR 观测",
      "device_id": "v2415A",
      "event_at": "2026-11-12T11:59:00",
      "created_at": "2026-11-12T12:00:00"
    }
  ],
  "last_id": 12346,
  "snapshot_required": false,
  "snapshot_state": "ready",
  "latest_snapshot_id": 7
}
```

- 按 `id` 升序，`limit` 默认 500，最大 1000。
- 客户端每行都写本地 history；只有更新时才改 entries。
- `last_id` 为最后一条的 `id`；没有新行时等于 `since_id`。
- **快照信号**：
  - 云端还没有任何快照时，pull 返回 `409 snapshot_required`（见 §5.1.1），
    不是 200 业务标记；客户端必须先创建/复用快照。
  - 已有快照时正常 200，`snapshot_required=false`、`snapshot_state="ready"`、
    `latest_snapshot_id=该快照 id`。
  - 多个客户端同时首启时以 `/state` 的 `latest_snapshot_id` 为准拉取，
    不假设 first-wins；`POST /snapshot` 只在 entries 完全相同且水位相同时 reused。
- **漏拉检测**：如果 `since_id < oldest_history_id - 1`，说明客户端水位落在已清理区间，
  pull 返回 `409 history_gap`：
  ```json
  {"error": "history_gap", "oldest_history_id": 1, "head_id": 12345,
   "latest_snapshot_id": 7}
  ```
  客户端必须停止增量拉取，改用快照重建基线，并记录「该区间的历史已不可得」；
  不得把 `history_id` 静默跳到 head。
- `since_id == oldest_history_id - 1` 时不报错，从 `oldest_history_id` 开始拉全。

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ops` | array | `id > since_id` 的行，按 `id` 升序 |
| `ops[].id` | int | `history.id`；客户端用它推进水位 |
| `ops[].op_id` | string | 幂等键 |
| `ops[].username/type/key` | string | 定位 |
| `ops[].kind` | string | 展示标签 |
| `ops[].new_value/old_value` | number \| string \| null | 数值或 note 文本 |
| `ops[].delta_value` | number \| null | 展示用 |
| `ops[].source/sync_from/device_id` | string | 展示/来源 |
| `ops[].event_at/created_at` | string | 事件时间/服务端接收时间 |
| `last_id` | int | 本页最后一条 `id`；无新行时等于 `since_id` |
| `snapshot_required` | bool | 有快照时为 false |
| `snapshot_state` | `"ready"` | 有快照时 |
| `latest_snapshot_id` | int | 最新快照主键 |

错误：`400 invalid_request`、`401 unauthorized`、`409 snapshot_required`、
`409 history_gap`、`410 snapshot_expired`。

### 5.4 `POST /api/v2/profile/snapshot`

```json
{
  "snapshot_history_id": 12345,
  "origin_name": "DESKTOP-PC",
  "entries": [
    {"username": "甲", "type": "stock", "key": "钱袋",
     "value": 123.5,
     "event_at": "2026-11-12T12:00:00", "op_id": "uuid"}
  ]
}
```

- 上传方必须已经 pull 到 `snapshot_history_id`，并完成本地 pending 的上传/合并。
- 服务端只做形状校验，不做业务范围校验：
  1. `snapshot_history_id` 是整数且 `<= head_id`；
  2. `entries` 数量 `<= 5000`；`username/type/key` 非空且在长度限制内；
  3. `value` 是有限数字或字符串；`type='note'` 允许字符串，数值模型要求有限数字；
     不接受 NaN/Inf；
  4. 同一份 entries 内 `(username, type, key)` 不重复；
  5. `origin_name` 在长度限制内。
- 不校验 cap、周期、业务范围；这些属于客户端/展示层。
- 返回 `{id, snapshot_history_id, entry_count, reused}`：
  - `reused=true` 仅当同一 `snapshot_history_id` 且 entries 按 `(username,type,key)` 排序后
  哈希完全一致；
  - 否则插入一份新快照，`reused=false`，以最新 id 为准；允许有数据的客户端覆盖先前的空快照。

请求字段：

| 字段 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `snapshot_history_id` | int | 是 | 0..head_id |
| `origin_name` | string | 否 | 0..80 |
| `entries` | array | 是 | 0..5000 条 |
| `entries[].username` | string | 是 | 1..80 |
| `entries[].type` | string | 是 | 1..20 |
| `entries[].key` | string | 是 | 1..120 |
| `entries[].value` | number \| string | 是 | 数值模型有限 number；note string |
| `entries[].event_at` | string | 是 | 19 位秒串 |
| `entries[].op_id` | string | 否 | 胜出 op；空串在平局中输给非空 |

响应字段：`id`（快照主键）、`snapshot_history_id`、`entry_count`、`reused`。
错误：`400 invalid_request`、`401 unauthorized`、`413 payload_too_large`。
`origin_device` 从会话取，正文不传。

### 5.5 `GET /api/v2/profile/snapshot`

- 空 body：最新快照摘要（id、水位、条目数、来源、时间）。
- `?id=`：该快照完整 entries。
- 如果 `id` 不存在，或 `snapshot_history_id < oldest_history_id - 1`（该快照的尾部历史
  已被清理），返回 `410 snapshot_expired`，带 `latest_snapshot_id` 和 `head_id`。
- 新设备只取最新一份；要最早快照之后的全量历史，取仍有效的、最早的一份。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `scope` | `"latest"` \| `"earliest"` \| `"list"` | 否 | 默认 `latest` |
| `id` | int | 否 | 指定快照主键；与 `scope` 二选一 |

响应：

- `scope=latest`（默认）：最新快照摘要
  `{latest_snapshot_id, snapshot_history_id, entry_count, origin_name, created_at}`；
  没有任何快照时返回 200，`latest_snapshot_id=null`、`entry_count=0`。
- `scope=earliest`：仍有效的最早快照摘要
  `{snapshot_id, snapshot_history_id, entry_count, origin_name, created_at}`；
  没有则 `410 snapshot_expired`。
- `scope=list`：`{snapshots: [{id, snapshot_history_id, entry_count, origin_name, created_at}], latest_snapshot_id}`，按 `id DESC` 只列有效快照；没有时 `snapshots=[]`。
- `id=...`：完整详情
  `{id, snapshot_history_id, origin_device, origin_name, entry_count, entries, created_at}`。

错误：`400 invalid_request`、`401 unauthorized`、`410 snapshot_expired`
（不存在或 `snapshot_history_id < oldest_history_id - 1`）。

### 5.6 `GET /api/v2/profile/entries?as_of_id=`

- 只读折叠：最新快照 + `id > snapshot_history_id AND id <= as_of_id` 的 set，
  按 `(event_at, op_id)` 取每 key 最新。
- 返回：

```json
{
  "as_of_id": 12345,
  "snapshot_id": 7,
  "entries": [
    {"username": "甲", "type": "stock", "key": "钱袋",
     "value": 123.5,
     "event_at": "2026-11-12T12:00:00", "op_id": "uuid"}
  ]
}
```

- 网页矩阵直接用它，不再依赖「PC 最近有没有刷新快照」。
- `as_of_id` 缺省时等于 `head_id`。
- 如果 `as_of_id > head_id`，返回 `400 invalid_as_of`。
- 如果 `as_of_id < 最新快照的 snapshot_history_id`，取**不晚于** `as_of_id` 的最新一份快照；
  没有这样的快照时返回 `409 snapshot_too_old`。
- 如果尾巴过大（例如超过 5000 行），返回 **200 + `stale=true`**、
  `stale_reason="tail_too_large"`，并带 `head_id` / `latest_snapshot_id`；
  网页退化展示最新快照并标记「可能过期」，不应直接 500。
  任一在线客户端都可以手动创建新快照来消除 stale。
- 无快照时：
  - `head_id=0`：返回 200 空 entries（`entries=[]`、`snapshot_id=null`），网页显示空；
  - `head_id>0`：返回 `409 snapshot_required`，提示客户端先创建快照。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `as_of_id` | int | 否 | 默认 `head_id`；`0..head_id` |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `as_of_id` | int | 实际使用的历史水位 |
| `snapshot_id` | int \| null | 采用的快照主键；无快照且空账户时为 null |
| `snapshot_history_id` | int \| null | 快照覆盖水位；无快照且空账户时为 null |
| `head_id` | int | 当前最大 `history.id` |
| `stale` | bool | 尾巴过大时为 true |
| `stale_reason` | string \| null | `"tail_too_large"` |
| `entries` | array | `{username,type,key,value,event_at,op_id}` |

错误：`400 invalid_as_of`（超前/非整数）、`401 unauthorized`、
`409 snapshot_too_old`（`as_of` 早于所有有效快照）。

### 5.7 `GET /api/v2/profile/history?before_id=&limit=&username=&device_id=`

- 直接读 `profile_history_v2`，返回 `id/op_id/username/type/key/kind/new_value/old_value/
  delta_value/source/sync_from/device_id/event_at/created_at`。
- 网页历史页直接显示旧值/新值，不再做窗口回放。
- 旧值缺失时前端显示 `—`。

请求参数：

| 参数 | 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `before_id` | int | 否 | 仅返回 `id < before_id`；用于翻页 |
| `limit` | int | 否 | 1..1000，默认 100 |
| `username` | string | 否 | 0..80；过滤 |
| `device_id` | string | 否 | 0..64；过滤 |

响应字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `ops` | array | 按 `id` 倒序；字段同 pull，另含 `old_value/new_value/kind/sync_from` |
| `next_before` | int \| null | 本页满 `limit` 且还有更早行时为最后一条 `id`；否则 null |
| `total` | int | 当前过滤条件下的总行数 |

错误：`400 invalid_request`、`401 unauthorized`。

## 6. 同步流程

### 6.1 首次启动 / 新设备

> 本地升级后不再依赖 v1，直接走 v2：
> - 本机有 entries：按 §7.2 迁移，push 迁移出的 pending history，pull，创建快照；
> - 本机无 entries：拉取云端已有快照，或创建空快照作为基线；
> - 没有快照时，总览数据不视为已同步；文档/定义/用户照常同步。

1. `GET /state`，确认 `protocol_version=2`。
2. 选择基线：
   - **本地 history 为空（新设备/重装）**：
     - 先 push 本机 pending history（若有）；
     - 看 `error_code` / `snapshot_required`：`true` 时先创建快照；`false` 时先 `GET /snapshot` 取基线；
     - 设置好 `snapshot_id` / `snapshot_history_id` / `history_id` 后再 pull；
       - 只要最新状态：取最新快照，`history_id = snapshot_history_id`，历史从该水位开始；
       - 要最早快照之后的全量历史：取**最早有效**的快照，`history_id = 该水位`，
         然后从该水位逐页回放。
   - **本地 history 非空（已有设备）**：
     - 先校验本地快照 id：`GET /snapshot?id=本地快照`；
     - 若 `410 snapshot_expired`：自动切换最新快照，更新 `snapshot_id` /
       `snapshot_history_id`，`history_id` 推进到该快照水位，**记录历史缺口**，再继续 pull；
     - 快照有效：不使用快照推进 `history_id`，从本地 `history_id` 继续 pull，
       保证 6.6 的一一对应；快照条目只与 entries 按 `(event_at, op_id)` 合并。
3. `GET /pull?since_id=history_id&snapshot_id=本地快照id`，逐页：
   - 每行写本地 history（按 `op_id` 幂等）；
   - 仅当更新时改 entries；
   - `history_id` 推进到本页 `last_id`；
   - 本页应用与水位推进在同一个本地事务里，避免「水位写了、行没写完」。
   - 如果响应 `409 snapshot_required`：先 push 本机 pending history，创建/复用快照，
     再重新 pull。
   - 如果响应 `410 snapshot_expired`：按第 2 步的失效分支自动切最新快照，记录缺口，再 pull。
   - 如果响应 `409 history_gap`：同样重建基线并记录缺口，不得静默跳水位。
4. 上传 pending：
   - 取 `cloud_id IS NULL AND upload_state='pending' AND device_id=本机`；
   - 200 条/批 push；
   - accepted/duplicate 写 `cloud_id`、`upload_state='synced'`；
   - rejected 写 `upload_state='rejected'` 和 `last_error`，保留行。
5. 成功完成后允许 `ProfileEngine` 跑周期处理。

> 硬规则：已有本地 history 的设备，任何时候都不能因为云端有更新快照就跳过未拉取的行。
> 快照只用于「本地没有基线」的新设备；历史完整性由本地 `history_id` 保证。

### 6.2 正常同步

```text
pull → 应用 → push → 应用 ack
```

- 先拉后推；拉取失败只保留水位，下一轮重试。
- push 失败只保留 `cloud_id IS NULL`，下一轮重试。
- 没有快照前置、没有 base、没有「先清除本地数据」。

### 6.3 离线写入

- 本地直接写 entries + history（`cloud_id=NULL`）。
- 引擎周期处理在本次进程未完成干净同步前跳过；普通 set/inc 照常写。
- 联网后按 6.2 补传。
- 另一台设备已更新同一 key 时：本地 pending 上传后，pull 回来的行按 `(event_at, op_id)`
  决定 entries；旧行只留 history。

### 6.4 快照创建

- **任意已认证客户端都可以创建**（PC / Android 能力对齐），不再限制 PC。
- **第一份快照自动创建**：第一个连接服务器、`/state` 返回
  `error_code=PROFILE_SNAPSHOT_REQUIRED` 的客户端，自动用当前 entries（没有则空快照）
  创建快照；后续客户端拉取这份快照。
- **后续快照手动刷新**：不再自动周期创建；由用户在 PC 或 Android 上触发 `POST /snapshot`。
- 首次 v2 启动的顺序：push 本机 pending history → 确保基线（创建/拉取快照）→ pull。
  push 不要求快照存在，否则首份快照无法覆盖迁移历史。
- 没有快照时：**总览数据（profile entries）不视为已同步**；定义、文档、用户等其他通道
  照常同步，不影响初始同步。
- 快照幂等：同一 `snapshot_history_id` 且 entries 哈希一致才 `reused=true`；
  不同 entries 插入新快照（`reused=false`），以最新 id 为准，允许有数据的客户端覆盖空快照。
  多端同时首启时客户端以 `/state` 的 `latest_snapshot_id` 为准拉取，不假设 first-wins。
- 快照不修改 history，不删除旧行。

### 6.5 清理

- 先明确历史保证：
  - **全量历史模式**：保留 `id > 最早保留快照的 snapshot_history_id` 的全部行；
    只清理更早的行。**只有这个模式提供 6.7 的「不丢段」保证。**
  - **快速模式**：只保留 `id > 最新快照的 snapshot_history_id`；更早行可清理。
    此时历史保证只到最新快照水位，不承诺更早历史。
- 默认采用全量历史模式；存储成本换历史完整性。
- `/state` 必须返回 `oldest_history_id`；pull 用它检测水位是否落入已清理区间：
  `since_id < oldest_history_id - 1` 时报 `history_gap`，客户端重建基线并记录缺口，
  不得静默继续增量拉取。
- **快照清理必须跟随 history 清理**：
  - 快照有效条件：`snapshot_history_id >= oldest_history_id - 1`（它之后的历史完整保留）；
  - 清理 history 后，删除 `snapshot_history_id < oldest_history_id - 1` 的快照，
    避免出现「快照还在、后续历史已删」的不可用基线；
  - 因此「最早有效快照」会随清理前移；全量历史保证以当前的 `oldest_history_id` 为准。
- 本地：保留 pending / rejected 行；已 synced 的历史按 90 天清理。
- 快照保留最近 20 份；被 history 清理判定失效的快照优先删除。

### 6.6 历史一一对应的前提

在以下条件同时成立时，云端 `history.id > 最早保留快照水位` 的行与每个持续同步设备的
本地 history 一一对应：

1. 已有设备从不因云端新快照推进 `history_id`；
2. 云端不清理最早保留快照之后的 history 行；
3. pull 一页、写 history、推进 `history_id` 在同一个本地事务里；
4. 本地写入形状合法；过期/无效/未来时间也 accepted，只有形状不合法无法上传；
5. 一次写入一个 `op_id`，重试复用同一个，不复用给另一次写入；
6. 本地库在同步前没有丢失；
7. 设备 `history_id >= oldest_history_id - 1`；低于它时 pull 必须返回 `history_gap`，
   客户端重建基线并显式记录缺口，不能静默跳水位。

例外：

- 形状不合法（缺 `op_id`/`key`/`type`，或 value 既非有限数字也非文本）的行无法形成
  云端 history，只保留在本地并标 `rejected`；
- 本地库在同步前丢失，未上传行无法恢复；
- 设备水位低于 `oldest_history_id - 1`：该区间已被清理，pull 报 `history_gap`；
  客户端必须重建基线并记录「这段历史不可得」，这不是静默丢失；
- 重装/新设备从最新快照起步时，本地 history 从该快照水位开始，更早的历史不在本地；
  需要最早快照之后的全量历史，就从最早保留的快照起步。
- entries 是 LWW 结果，可能不包含旧行；**history 包含所有行**，两者不要混淆。

### 6.7 历史不丢失保证

在 6.6 的条件全部成立时：

> 云端 `history.id > W0`（W0 = `oldest_history_id - 1`，即仍可从云端连续拉取的起点）的
> 每一行，都会出现在每个已建立该基线且持续同步的设备本地 history 中。

如果设备水位低于 W0，pull 会返回 `history_gap`，客户端重建基线并报告缺口；
缺口本身无法恢复，但不会发生「以为拉全了、实际缺一段」的静默错误。

包含：

- 过期、旧周期、重复覆盖的行；
- `event_at` 为空/无法解析的行（存空串，history-only）；
- 未来时间的行（照常入库，回放端不用于 entries）；
- 被 LWW 淘汰、没有更新 entries 的行。

唯一例外：形状不合法的行、本地库在同步前物理丢失、手动删除、账号删除。

这不等于 entries 一一对应：entries 只保留每个 key 的 LWW 赢家，history 保留全部行。

## 7. v1 → v2 迁移

### 7.1 云端

1. 部署 v2 表、v2 接口；v1 表与 v1 接口只保留，**网页不再使用 v1**。
2. 网页只读 v2：矩阵读 `/entries`，历史读 `/history`；没有 v2 数据时直接为空，
   不回退 v1。
3. **云端不做 v1→v2 基线归约**，v2 表初始为空。第一个升级到 v2 的客户端负责：
   - 本地迁移（PC 全量 entries + history；Android 只 entries）；
   - push 迁移出的 pending history；
   - pull 到 head；
   - 创建第一份 v2 快照。
4. v1 表与接口保持原样，**不主动停用**；新客户端只走 v2。不做 v1→v2 双写或写入转换。
   用户只保留一个真源，旧客户端在 v2 基线之后的写入不会进入 v2。
   发布说明和旧客户端提示必须明确这一点：升级后继续用 v1 写入，数据只留在 v1，
   不会出现在 v2 网页/客户端；避免用户以为记录丢失。
5. 不做 v1/v2 双写。
6. 回滚：v1 表未动；切回 v1 前先停 v2 写入，验证后恢复；v2 数据保留。

### 7.2 本地

**PC：**

1. 迁移前把 v1 数据库/旧表导出到 `config/session/_profile-v1-backup-<timestamp>/`
   （下划线开头），**由用户自行删除**；迁移后主库不再读写旧表。
2. **entries 无损迁移**：见 §7.3。
3. **history 无损迁移**：全部行保留；`set/delta` 进入 v2 outbox（重新上传 v2），
   `tick/reset/unscoped` 标 `upload_state='local_only'`，不上传。
4. 首次 v2：push 迁移出的 pending history → pull；若 pull 返回 `409 snapshot_required`
   或 `/state` 为 `PROFILE_SNAPSHOT_REQUIRED`，用当前 entries 创建首份快照，然后重新 pull。
5. 不再读 v1；v1 同步引擎冻结。

**Android：**

1. 新建 v2 三张表；**彻底放弃旧表**，不保留 v1 同步路径。
2. **只迁移 entries（当前值）；不迁移 v1 history 表，直接丢弃**，避免复杂度堆积。
3. 首次 v2：
   - 云端已有快照：拉取并按 per-key LWW 合并；
   - 云端没有快照（`/state` 为 `PROFILE_SNAPSHOT_REQUIRED` 或 pull 409）：用本机 entries
     创建快照；本机也没有 entries 时创建空快照。
4. 能力与 PC 对齐：可建快照、可 push/pull、可迁移 entries；只是没有旧历史。

### 7.3 PC 迁移字段映射（迁移脚本单独 review）

entries：

| v1 列 | v2 列 | 说明 |
| --- | --- | --- |
| `value` / `value_text` | `value` | 合并为无类型列：数值 REAL，note TEXT |
| `updated_at` | `event_at` | 已是真实事件时间；realtime 还原小数 |
| `op_id` | `op_id` | 胜出 op 身份 |
| `locked` | `locked` | 本机锁原样 |
| `updated_time` | `updated_time` | 本机落库时间原样 |

history：

| v1 列 | v2 列 | 说明 |
| --- | --- | --- |
| `ts` | `event_at` | 真实事件时间 |
| `old_value` / `new_value` | `old_value` / `new_value` | 无类型列 |
| `old_value_text` / `new_value_text` | 合入 `old_value` / `new_value` | note 文本即值 |
| `change_type` | `kind` | set/delta/tick/reset，仅展示 |
| `delta_value` / `source` / `sync_from` / `device_id` | 同名 | 原样 |
| `origin_local_id` | — | 已编码在 `op_id` 的 `legacy:device:origin` 中 |
| `remote_id` | — | 不迁移；`cloud_id` 从 NULL 开始，重新上传 v2 |
| `set/delta` | `upload_state='pending'` | 进入 v2 outbox |
| `tick/reset/unscoped` | `upload_state='local_only'` | 保留历史，不上传 |

- v1 没有独立的 `recorded_at`；用 `ts` 或迁移时间填充。
- 所有 `set/delta` 都重新上传 v2，`cloud_id` 从 NULL 开始；v1 `remote_id` 不沿用。
- 若胜出 op 的 `op_id` 为空，entries 保留空串；它在同秒平局中输给任何非空 `op_id`，
  这是可接受的：历史行仍保留，新的真实写入优先；不要合成假 `op_id`。

迁移脚本 review 清单：

1. 行数：v1 entries/history 行数与 v2 一致（无静默丢弃）。
2. 字段抽查：value、old/new、kind、source、sync_from、device_id、event_at 逐条对比。
3. realtime 小数还原：迁移前后当前值误差 < 1e-6。
4. note 文本：进入单 `value` / `old_value` / `new_value`，没有被转成数字。
5. locked 原样；op_id 唯一；pending / local_only 计数正确。
6. 迁移可重入：重复执行不产生重复行；失败回滚、备份保留。
7. 迁移后不再读 v1 表。

### 7.4 客户端切换

1. 新客户端**不再回退 v1**；`GET /state` 不是 2 时提示服务端未升级，Profile 同步暂停，
   文档/定义/用户等其他通道照常同步。
2. 服务端 v2 可用后，客户端首次同步按 §6.1 建立快照。
3. v1 客户端可以继续存在，但不再是新客户端的真源；新客户端只走 v2。
   旧客户端提示必须明确：v1 写入不会进入 v2，需要继续同步请升级。
4. **没有快照只影响总览数据同步，不影响文档等其他数据**；客户端应自动创建第一份快照。

## 8. 从 v1.5 删除/冻结的内容

v2 上线后，以下不再进入新路径：

- `remote_id` 领段、水位、空洞处理；统一用 `history.id`。
- `snapshot_remote_id`、pre-snapshot 过滤、`stale_base`、base_remote_id。
- `op='delta'` 回放、`_period_relation`、周期归零/恢复写账本。
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
   - 服务端按 `id > snapshot_history_id` 折叠校验；或
   - 对快照上传加设备白名单。
2. **未来时间戳**：照常入库；回放时 `event_at > 当前时间 + 24h` 的行只进 history，
   不更新 entries。坏钟不会压住 key，历史也不会丢。
3. **同一 key 并发 set**：后到覆盖，history 保留两条；这是 set-only 的明确语义。
   同一用户不会并发 `inc`，因此不需要分布式计数器。
4. **v1/v2 并存**：v1 接口不主动停用，新客户端只走 v2；用户只保留一个真源。
   旧客户端在 v2 基线之后的写入不会进入 v2，也不做转换。
5. **`/entries` 折叠成本**：快照足够新时尾巴很小；尾巴过大返回 200 + `stale=true`，
   提示创建快照。后续可加云端 entries 缓存，不改变协议。
6. **快照新鲜度**：网页矩阵读 `/entries` 的折叠结果，不依赖 PC 快照的实时性；
   快照只影响折叠成本。

## 10. 验收标准

1. **提交序水位**：pull 用 `history.id`；并发 push 不会出现低 id 晚可见导致漏拉。
2. **全部接受**：过期、无效、未来 `event_at` 也 accepted 并入库；只有形状不合法才 rejected。
3. **本地回放**：所有行进 history；只有 `(event_at, op_id)` 更大且事件时间有效的行更新 entries。
4. **离线补传**：手机离线产生 200 条，联网后全部取得 `history_id`；PC/网页可查。
5. **快照重建**：新设备取快照 + `id > snapshot_history_id` 的 set，得到与源设备一致的 entries；
   取最新快照时历史从该水位开始，取最早保留快照时可得到该水位之后的全量历史。
6. **旧值展示**：网页历史页直接显示上传的 `old_value/new_value`，不依赖窗口回放。
7. **周期引擎**：未完成干净 pull + push 时，`ProfileEngine` 不执行周期处理。
8. **v1 保留**：v1 表/接口原样可用；新客户端只走 v2，不依赖 v1。
9. **无 v1.5 机制**：v2 路径不出现 `remote_id`、`delta`、`snapshot_remote_id`、`-1`、rebase。
10. **清理与漏拉检测**：全量历史模式保留最早快照水位之后的全部行；`/state` 返回
    `oldest_history_id`；pull 在 `since_id < oldest_history_id - 1` 时返回 `409 history_gap`，
    客户端重建基线并显式记录缺口，不静默跳水位。
    清理 history 时同步删除 `snapshot_history_id < oldest_history_id - 1` 的快照，
    不留下「快照还在、尾部历史已删」的不可用基线。
11. **重复上传**：同一 `op_id` 在对应行仍存在时返回 duplicate + 已有 `history_id`；
    行已被清理时按新行接受并分配新 `history_id`（允许重复内容，客户端按本地 `op_id` 去重）。
12. **单 value 类型**：数值写入后 `typeof(value)='real'`、JSON 是 number；note 写入后
    `typeof(value)='text'`、JSON 是 string；历史 old/new 同理。
13. **已有设备不跳水位**：云端出现新快照时，本地 `history_id` 只按 pull 页推进；
    本地 history 与云端 `id >= oldest_history_id` 的行一一对应（rejected/物理丢失除外）。
14. **原子性**：pull 一页、写 history、推进 `history_id` 在同一本地事务；崩溃重放不丢不重。
15. **rejected 例外**：只有形状不合法（缺 `op_id`/`key`/`type`，或 value 非有限数字/文本）
    才是 rejected；过期、无效、未来时间都不算。
16. **历史不丢段**：基线 W0 后，云端 `id > W0` 的每一行都出现在已建立基线设备的本地
    history，包含过期、无效、未来、LWW 淘汰行。
17. **网页 v2 化**：网页只读 v2；v1 接口保留但网页不再使用；没有 v2 数据时网页为空。
18. **PC 迁移无损**：v1 entries/history 全部迁入 v2；行数、old/new、kind、source、
    sync_from、device_id、event_at 抽查一致；tick/reset/unscoped 标 local_only。
19. **Android 迁移范围**：只迁 entries，不迁 v1 history；旧表彻底放弃；可建快照、
    可 push/pull，能力与 PC 对齐。
20. **快照优先与失效**：`/state` 始终 200，但无快照时返回
    `error_code=PROFILE_SNAPSHOT_REQUIRED` / `next_action=create_snapshot`；
    新客户端看到后先 push pending history、再创建/复用快照，然后 pull，不能先 pull。
    `/pull` 无快照返回 `409 snapshot_required`；快照失效返回 `410 snapshot_expired`，
    客户端自动切最新快照、记录历史缺口并继续。首份快照自动创建（`reused` 区分是否复用），
    后续手动刷新；没有快照只暂停总览同步，文档等照常同步。
21. **迁移脚本单独 review**：迁移脚本与同步逻辑分开评审；通过 §7.3 清单。
22. **PC 备份目录**：旧库/旧表导出到 `config/session/_profile-v1-backup-<timestamp>/`，
    由用户自行删除。
23. **Android 无历史迁移**：不迁 v1 history，只迁 entries；旧表彻底放弃。
24. **回滚**：v1 表未动；停写 v2 后可切回 v1，备份完整。
25. **as_of 语义**：`/entries` 缺省 `as_of_id=head`；超前返回 400；过旧且无可用快照返回
    409 `snapshot_too_old`；尾巴过大返回 200 + `stale=true`，网页退化为最新快照 + 过期标记。
26. **快照校验**：按 §5.4 的五项形状校验；不校验 cap/周期等业务范围。
27. **rejected 修正语义**：只修格式复用 `op_id`；改内容生成新 `op_id`、新行，原行保留
    `local_only`。
28. **旧客户端可见性**：v1 在 v2 基线后的写入只留在 v1，不进入 v2；发布说明/客户端提示
    必须明确。
29. **快照接口**：`/snapshot` 支持 `latest/earliest/list/id`；`reused` 仅同水位且 entries
    排序后哈希一致；不同 entries 新建快照。
30. **空账户**：`/entries` 在 `head=0` 且无快照时 200 空；`head>0` 且无快照时 409；
    stale 时 200 + `stale=true`。
31. **历史分页**：`/history` 的 `before_id/limit/username/device_id` 生效，
    `next_before` 正确。
32. **状态契约**：`/state` 无快照 200 + `PROFILE_SNAPSHOT_REQUIRED`；`/pull` 无快照 409。

## 11. 实施顺序

1. 云端：v2 表 + `/state`、`/push`、`/pull`、`/snapshot`、`/entries`、`/history`；
   `/state` 无快照返回 `PROFILE_SNAPSHOT_REQUIRED`，`/pull` 无快照返回 409；
   网页只读 v2；v1 接口原样保留。
2. PC：v2 三张表 + entries/history 无损迁移脚本（单独 review）；旧库/旧表导出到
   `config/session/_profile-v1-backup-<timestamp>/`，用户自行删除。
3. Android：v2 三张表 + entries 迁移；不迁 history；旧表彻底放弃。
4. 客户端：v2 repository（entries/history/outbox）+ 同步循环 + 首份快照自动创建，
   后续手动刷新。
5. 网页：矩阵改 `/entries`；历史页直接读 v2 old/new；无 v2 数据时为空。
6. 端到端验证：PC 迁移行数与字段、Android 无旧历史、首份快照、无快照时文档仍同步、
   历史一一对应、过期/无效/未来行保留、重复上传、周期引擎闸门。
7. 清理 v1.5 死代码；保留 v1 接口与备份。

## 12. 已确认决策

1. PC 旧库/旧表导出到 `config/session/_profile-v1-backup-<timestamp>/`，由用户自行删除。
2. Android 只迁 entries、不迁 v1 history，避免复杂度堆积。
3. 第一份快照由第一个连接服务器、`/state` 返回 `PROFILE_SNAPSHOT_REQUIRED` 的客户端
   自动创建；之后不再自动创建，由用户手动刷新。
4. `/state` 始终 200；无快照时返回 `error_code=PROFILE_SNAPSHOT_REQUIRED`、
   `next_action=create_snapshot`。客户端先 push pending history，再创建/复用快照，
   然后 pull；`/pull` 无快照返回 `409 snapshot_required`。
5. v1 接口保持原样，不主动停用；新客户端只走 v2，不做双写。
6. 清理后漏拉用 `oldest_history_id` 检测：`since_id < oldest_history_id - 1` 返回
   `409 history_gap`，客户端重建基线并记录缺口。
7. 已清理行的 `op_id` 重推按新行接受；客户端按本地 `op_id` 去重，云端允许重复内容行。
8. rejected 行只修格式复用 `op_id`；改内容生成新 `op_id`、新行。
9. `/entries` 的 `as_of` 与 stale 降级按 §5.6；快照校验按 §5.4 五项清单。
10. `delta_value` 不校验；`event_at` 严格解析，非法存空串（history-only）。
11. 旧客户端在 v2 基线后的 v1 写入不进入 v2；发布说明/客户端提示必须明确。
12. history 清理时同步删除失效快照；快照有效条件为
    `snapshot_history_id >= oldest_history_id - 1`。
13. pull 支持可选 `snapshot_id`；失效返回 `410 snapshot_expired`，客户端自动切最新快照、
    记录历史缺口并继续；新客户端先 `/state` → `/snapshot`，不直接 pull。
14. `/snapshot` 支持 `latest/earliest/list/id`；`reused` 仅同水位且 entries 排序后哈希一致；
    不同 entries 新快照。
15. `/entries` 空账户 200 空；无快照且 `head>0` 返回 409；尾巴过大 200 + `stale=true`。
