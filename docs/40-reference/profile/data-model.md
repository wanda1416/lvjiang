# Profile 数据字段模型

本文列出 Profile 持久化与同步协议的全部字段：稳定 key、类型与语义。只写当前事实
（本地 schema v13，见 `src/lvjiang/core/profile/migrations.py`）；示例数据一律不列。
列清单常量在 `src/lvjiang/core/profile/rows.py`（`ENTRY_COLUMNS`）与
`src/lvjiang/core/profile/history_sql.py`（`HISTORY_COLUMNS`）。

## 1. 模型与 key 定义（`config/session/profile.yaml`）

四个模型类型：`quota`（配额）、`regen`（再生）、`stock`（存量）、`note`（笔记）
（`src/lvjiang/core/profile/models.py`）。稳定 key 是全名空间共享的字符串
（无 app 隔离）；`label`、`group`、`description` 只是展示文本，不参与身份。

各模型的定义字段（`src/lvjiang/core/profile/models.py`）：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `key` | str | 稳定标识，entries/history/引用都以它定位 |
| `group` | str | 定义分组；缺失归入 `default` |
| `label` | str | 展示名 |
| `description` | str | 说明文本 |
| `cap` | int/None | 上限；None 无上限 |
| `soft` | bool | True 软上限（仅提醒），False 硬上限（截断） |
| `show_cap` | bool | 总览是否展示 value/cap |
| `decimal` | bool | 是否允许小数 |
| `sources` / `uses` | list[str] | 增加/减少操作来源词表 |
| `sync_targets` | list[SyncTargetDef] | 同步目标边（`key`/`ratio`/`direction`/`source`/`lock_on_cap`） |
| `change_script` | str | 变更脚本路径（相对 `workflows/`） |
| `change_script_sync` | bool | True 时同步脚本在当前线程执行 |
| `period`（quota） | str | 周期名，须已注册 |
| `reset_time` / `reset_day`（quota/regen） | str/int | 周期边界时刻与日 |
| `steps`（quota/regen/stock） | list[StepDef] | 自定义步进（`value`/`source`） |
| `increment_only`（quota） | bool | 只记增量 |
| `regen_type`（regen） | str | `realtime`（按速率）或 `boundary`（按周期跳变） |
| `regen_rate_value` / `regen_rate_unit`（regen） | float/str | 速率与单位 |
| `regen_amount` / `regen_period`（regen） | float/str | boundary 的跳变量与周期 |
| `alert_orange` / `alert_red`（regen） | int/None | 告警阈值 |

## 2. `profile_entries`（当前值，9 列）

复合主键 `(username, type, key)`；dataclass `Entry`
（`src/lvjiang/core/profile/rows.py`）：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `username` | str | 内部用户名 |
| `type` | str | 模型类型（quota/regen/stock/note） |
| `key` | str | 稳定 key |
| `value` | float | 数值当前值；再生型条目读时实时推算，落库值是最近一次固化的底数 |
| `value_text` | str | note 模型的文本；其他模型为空 |
| `event_at` | str | 当前值的源写入时刻（ISO 秒级）；回放落地取源 op 时间而非本机墙钟 |
| `created_at` | str | 行首次创建时刻（审计） |
| `op_id` | str | 写成当前值的那条账本行的锚点，同秒裁决时据此查到达序 |
| `locked` | bool | 满值/手动锁定；锁定时写入与周期重置被拒，只由 `meta` 行维护 |

## 3. `profile_history`（账本，17 列）

主键 `local_id`；dataclass `HistoryRow`
（`src/lvjiang/core/profile/rows.py`）。行既是本机操作日志也是跨端回放账本：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `local_id` | int | 本地自增顺序；上行基线与待上传集合的游标 |
| `event_at` | str | 操作发生时刻（源写入钟面）；聚合与周期裁决的时间轴 |
| `username` / `type` / `key` | str | 归属（同 entries） |
| `old_value` | float/None | 变更前值；未知为 NULL，不等于零 |
| `new_value` | float | 变更后值；set/reset/regen 一律是覆盖后的绝对值 |
| `op_type` | str | 机制分类：`set`/`reset`/`regen`/`meta`（旧库残留只读的 `delta`）；与云端协议白名单一致 |
| `source` | str | 业务语境（为什么变）；系统行（reset/regen/meta）留空 |
| `old_value_text` / `value_text` | str | note 模型变更前后文本 |
| `delta_value` | float/None | 实际变动量（clamp 后）；NULL 表示未知，聚合时回退 `new_value - old_value` |
| `sync_from` | str/None | 声明式同步来源 `model:key` |
| `instance_id` | str | 写入方实例标识；回放行取来源设备，上行作者过滤的依据 |
| `op_id` | str | 行身份锚点；判重与幂等键 |
| `remote_id` | int/NULL | 云端提交序号；NULL = 本机待上传；同秒裁决的到达序 |
| `created_at` | str | 本机落库时刻（审计，不参与裁决） |

`profile_key_renames` 另存全用户改名审计（时间、操作 ID、模型、旧新 key、各表更新
条数），见 `src/lvjiang/core/profile/key_rename.py`。

## 4. 上行载荷（`PendingOp.payload`）

`remote_id IS NULL` 的账本行转为上行 dict，字段名与云端协议一致
（`src/lvjiang/core/profile/rows.py`、`server/src/profile_protocol.mjs` `validSetOp`）：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `op_id` | str | 幂等键（8–80 字符，不得以 `unscoped:` 开头） |
| `local_id` | int | 本机序号，与 `instance_id` 组成行身份 |
| `username` / `type` / `key` | str | 归属 |
| `op` | str | `set`/`reset`/`regen`/`meta` 四选一 |
| `value` | float | 覆盖值（meta 为锁标记 0/1） |
| `value_text` | str | note 文本（≤4000 字符） |
| `source` / `sync_from` | str | 业务语境与同步来源（≤200 字符） |
| `old_value_text` | str | note 变更前文本 |
| `happened_at` | str | 源写入钟面（服务端按时区偏移还原真实时刻） |

## 5. 下行 op（`RemoteOp`）

`POST /api/sync/profile/pull` 下发的行（`server/src/profile_sync.mjs`），
`src/lvjiang/core/profile/rows.py` `RemoteOp.from_json`：`remote_id`、`instance_id`、
`local_id`、`username`、`type`、`key`、`op`、`value`、`delta`（遗留列，恒空）、
`value_text`、`source`、`happened_at`、`old_value`、`op_id`、`old_value_text`、
`sync_from`——语义同上行载荷，外加 `remote_id`（回放水位与到达序）。

## 6. 快照条目与云端专表

- 快照 entries 元素（`src/lvjiang/core/profile/snapshot_sync.py` `_snapshot_entries`）：
  `username`/`type`/`key`/`value`/`value_text`/`event_at`/`op_id`/`locked`——只带
  `profile.yaml` 里有定义的条目，无定义残留只过滤不删除。
- 云端快照行 `profile_snapshot`（`server/src/profile_snapshot.mjs`）：`snapshot_id`、
  `watermark_remote_id`（绑定水位）、`instance_id`/`device_name`（来源）、
  `created_at`、`entry_count`、`entries`（整份 JSON，≤5000 条）。
- 云端物化表 `profile_entries` 在本地 9 列基础上多 `account_id` 主键成分
  （`server/migrations/0012_profile_entries.sql`）；value 只由 set/reset/regen 推进，
  `locked` 由最新 meta 行维护。
- `account_sync_version` 每账号一行两列：`documents_head`、`users_rev`
  （`server/src/sync_version.mjs`）；profile 通道不写它，其余状态按表查。

## 7. 本机同步状态（`account.json` → `profile_sync`）

`src/lvjiang/core/account/service.py` `profile_sync_state` 与
`src/lvjiang/core/profile/snapshot_sync.py` `_state`：

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `remote_history_id` | int | 已应用的远端水位（远端编号，与 local_id 不可互比） |
| `local_history_id` | int | 上行基线：快照上传/落地时的本地最大 local_id |
| `profile_snapshot_id` | int | 本设备跟随的账户级快照行 id（单一整数，非按用户映射） |
| `snapshot_remote_id` | int | 当前快照绑定的云端水位 |
| `cloud_has_snapshot` | bool | 上一轮拉取时云端有无快照（免空轮探测） |
| `last_pull_ok_at` | str | 最近一次干净拉取时间（诊断） |
