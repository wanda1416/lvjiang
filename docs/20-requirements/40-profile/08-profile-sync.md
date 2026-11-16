# Profile 同步 v1.3：类型三分类、覆盖语义、水位门与 meta 锁传播

> 状态：已实现（2026-11-14，基线 v0.15.13）——已上线，本地 schema v13，服务端全量部署。
> 本文档描述**当前线上与客户端的真实实现**，取代早期两个过程方案：
> [07-set-only-v1.1.md](07-set-only-v1.1.md)（v1.1：set-only 数据模型）与
> 已废弃的「v2 双表重构」规划（该规划的核心主张已按本文所述方式落地，双表/新命名空间被否决）。

## 1. 概念分层（v1.2 的核心）

三个正交概念，绝不混用：

| 层 | 含义 | 取值 |
| --- | --- | --- |
| **行为** | 值语义：`set` / `reset` / `regen` 三种一律「覆盖」——`new_value` 是绝对值，按 `(event_at, 到达序)` LWW 裁决（同一秒内比 `remote_id`；`op_id` 是随机 uuid，不参与判先后） | 三种相同 |
| **op_type** | 机制分类：谁干的 | `set` 更新 / `reset` 重置 / `regen` 恢复 / `meta` 锁传播 /（`delta` 增量，v1.1 遗留） |
| **source** | 业务语境：为什么变 | 只属于人的写入；系统行（reset/regen/meta）一律留空 |

「覆盖」是行为描述，不再是某个类型的名字——UI 里 `set` 显示为「更新」。

**覆盖语义是契约**：`set` / `reset` / `regen` 三种行的 `new_value`，任何一端回放落地后
就是该端的当前值，不因类型而降低效力、不按周期折算、不二次加工。类型只决定历史页
怎么展示、撤销认不认（只认 `set` / `delta`），不影响"这是不是最新值"的判断。
唯一例外是 `meta`：它只改 `entries.locked`，`new_value` 的 0/1 永远不进 value。

## 2. 各类型语义

| 类型 | 产生时机 | event_at | value |
| --- | --- | --- | --- |
| `set` | 人的写入（OCR/DSL/手动） | 真实写入时刻 | 绝对值 |
| `reset` | 配额跨周期归零（engine/service） | **周期边界时刻**（05:00 等），不是执行墙钟 | 0 |
| `regen` | 速率型再生写入时先固化恢复（两行模式第一行） | 速率型取**写入时刻**，周期型取**跨过的边界时刻** | 恢复后的绝对值 |
| `meta` | 锁定/解锁传播 | 锁操作时刻 | 锁标记 0/1 |
| `delta` | 仅 v1.1 存量；新代码不产生 | — | — |

- 三种值类型一律按覆盖语义回放与物化，`new_value` 就是落地方的当前值；`meta` 例外：只改 `entries.locked`，不碰 value/event_at（meta 的时刻参与 value LWW 会让它压住之后的真实写入）。
- `delta` 不上行；服务端收到含 delta 的 push **整批 409** `client_update_required_for_delta`。
- 再生型的当前值一律**读时实时推算**（速率型按速率累计、周期型按周期边界），不再有周期触发时的自动固化写入——这是多端一致性的关键：同步面上不存在「基于陈旧基线的自动写」。
- 固化（两行模式的 `regen` 行）只发生在真实写入事务里，**不看本进程是否同步过、也不等首次拉取**。业务写入写的是现场真值（OCR 观测 / DSL / 手动），不依赖远端基线；即使他端把这条 `regen` 行认成最新值，下一次观测写回的绝对真值会把它覆盖回来——账本按观测自愈，不靠拒绝本地落库来避险。

## 3. 本地表（原表原地迁移，无 v2 表）

本地 schema v13（迁移链：v12 统一列名与云端同构、v13 tick→regen）。本地是云端的子集。

- `profile_entries`（9 列）：username/type/key/value/value_text/event_at/created_at/locked/op_id
- `profile_history`（17 列）：local_id 主键 + event_at/username/type/key/old_value/new_value/op_type/source/old_value_text/value_text/delta_value/sync_from/instance_id/op_id/remote_id/created_at
- 行身份 = `op_id`；`local_id` 是本地自增顺序，`remote_id` 是云端提交顺序（两者不可互比）
- 账本记录一律 dataclass 传递（`rows.py`：Entry/HistoryRow/PendingOp/RemoteOp），dict 只存在于 SQLite 与 HTTP 边界

## 4. 云端表（D1）

- `profile_history`（19 列 = 本地 17 + account_id + id）：`remote_id` 由插入后一条批量 `UPDATE … ROW_NUMBER()` 按账号顺延赋值（先以 `remote_id=0` 落占位行，重推被 `DO NOTHING` 跳过因而不占号，所以无空洞）；索引：op_id 唯一（部分索引，查询须显式 `op_id <> ''` 蕴含谓词）、(account_id, remote_id)、(account_id, instance_id, local_id)
- `profile_entries`（~1320 行）：物化缓存，value 只由 set/reset/regen 推进（meta 的 0/1 不得进入），locked 由最新 meta 行维护；可随时从快照+账本重建
- `profile_snapshot`：账户级基线（含 locked），保留 20 份；上传时整表替换 entries
- `account_sync_version`（每账号一行，只剩两列：`documents_head` 文档发号基准、`users_rev` 名册版本号）：**其余状态全部按表查**——`history_head`/条数 = `MAX(remote_id)`（`profile_history_pull` 的 max 反向 seek，1 行；账本无空洞，所以 head 即条数，历史页第一页另做 `COUNT(*)`）、`document_head`/`documents` = `documents_head`（缺行时回 `MAX(documents.revision)`）、快照摘要 = `profile_snapshot` 最新一行、`snapshot_count`/`users` = 有没有快照行 / 有没有名册行（0/1，`EXISTS` 各 1 行）、定义/页面结构摘要 = 两张专表的 `sha256` 点查
- 旧的 `account_remote_seq` 序号表，以及 `history_head` / `history_rows` / `documents_count` / `snapshot_id` / `snapshot_remote_id` / `snapshot_origin` / `snapshot_created_at` / `snapshot_entries` / `snapshot_count` / `definition_sha256` / `layout_sha256` / `users_count` 这些派生列均已下线：报告值与准确值都来自索引表查询，不再有「缓存副本与真实数据漂移」的两处状态

## 5. 同步流程

- **上行（水位门两级）**：只扫 `remote_id IS NULL AND local_id > 基线`。
  基线 = `state["local_history_id"]`（快照上传/落地时推进）；状态丢失时从账本推导
  `MAX(local_id) WHERE remote_id > 0`（回放行落在账本尾部，天然盖过存量）。
  低于基线的行永不扫描、永不重判——没有放弃/重开机器。
- **下行**：`remote_id > since` 增量；回放按覆盖语义，meta 走 `db_apply_meta_locked`。
  同一秒内的两条写入（物化 `regen` 行 + 紧随的 `set`）按到达序 `remote_id` 判先后——随机
  `op_id` 分不出同一台设备连写的顺序，还会与云端物化（取到达最晚的一条）不一致。
- **快照**：已完成一次干净 pull+push 后可上传（全部类型+locked）；新设备/状态丢失时用最新快照落地。
- **锁定传播**：锁/解锁写 meta 行 → 上行 → 他端回放改 `entries.locked`；撤销守卫只认 set/delta，meta 不可撤销。

## 6. 接口（legacy 路径，行为即 v1.2）

- `POST /api/sync/state`：`protocol_version: 2`；各通道计数与 sha
- `POST /api/sync/profile/push`：接受 set/reset/regen/meta；按 `(account_id, op_id)` 幂等；含 delta 整批 409
- `POST /api/sync/profile/pull`：下发四种类型，`since_remote_id` 增量
- `POST /api/sync/profile/snapshot`：清单/详情/上传（空体=空基线播种）
- `GET /api/v2/profile/entries`：物化视图（state 三态 + entries 含 locked）
- `POST /api/sync/profile/history`：分页（`remote_id` 降序）；可按 `username` / `instance_id` / `op_types`（多选，白名单） / `type`+`key` 精确筛选，`total` 按同一条件 `COUNT(*)`（只在第一页算）。筛选列各有索引：`(account_id, op_type, remote_id)` 与 `(account_id, type, key, remote_id)`
- 错误信封：`{error: name}`；`client_update_required_for_delta` = 409 提示升级

## 7. 展示

- 桌面历史页类型标签：更新/重置/恢复/锁定/增量；撤销只认 set/delta
- 网页总览：entries 矩阵（锁定条目显示 `[锁定]`）、再生型全部读时实时推算
- 网页历史页：`remote_id` 降序、meta→锁定
- Android：数据项锁定显示 `[锁定]`，历史行带类型标签，时间字段为 `event_at`

## 8. 版本历史

- **v1.3**（2026-11-15）：写明覆盖语义契约（`set`/`reset`/`regen` 的 `new_value` 被任一端
  承认为最新值，`meta` 例外）；删除「首次干净拉取前不推算周期恢复」的写入闸门——周期
  自动写已取消，固化只随真实写入发生，不再依赖同步状态，只读实例同样正常固化。
- **v1.2**（本文，2026-11-14）：类型三分类、水位门、meta 锁传播、dataclass、原表原地迁移
- **v1.1**（[07-set-only-v1.1.md](07-set-only-v1.1.md)，2026-11-12）：set-only 数据模型（历史方案）
- 更早：delta 双向同步（已废弃）
