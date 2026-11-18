# Profile 同步 v1.4：类型三分类、覆盖语义、水位门与 meta 锁传播

> 状态：已实现（2026-11-18，基线 v0.15.17）——v1.4 收敛周期派生值，本地 schema v13；
> v1.4 只改客户端产生时机与读时口径，同步协议与服务端不变。本轮补齐自定义 regen
> 关联目标写入，不以源码实现或本地验证代替实际发布、部署记录。
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
| `reset` | 配额跨周期后的**首次真实写入**时固化归零（与 regen 同构的两行模式第一行） | **周期边界时刻**（05:00 等），不是执行墙钟 | 0 |
| `regen` | 再生模型真实写入入口先固化恢复（有恢复量时） | 条目锚点：速率型取**写入时刻**，周期型取**跨过的边界时刻**；账本取物化时刻 | 恢复后的绝对值 |
| `meta` | 锁定/解锁传播 | 锁操作时刻 | 锁标记 0/1 |
| `delta` | 仅 v1.1 存量；新代码不产生 | — | — |

- 三种值类型一律按覆盖语义回放与物化，`new_value` 就是落地方的当前值；`meta` 例外：只改 `entries.locked`，不碰 value/event_at（meta 的时刻参与 value LWW 会让它压住之后的真实写入）。
- `delta` 不上行；服务端收到含 delta 的 push **整批 409** `client_update_required_for_delta`。
- **quota 的当前值一律读时派生**（`periods.quota_current_value`）：`event_at` 早于本周期边界就读作 0。
  显示、DSL、Android 工作台、写入基线共用这一个入口，都不写库。周一 05:00:00 一过，
  每个读面同时是 0，不依赖引擎在跑、设备在线或读了几次。
- **没有周期性后台写入**：引擎不再按周期落 `reset`，只在派生值变化时通知界面重画
  （并保证条目存在，缺失时建 0 值且不记账）。
- 再生型的当前值同样**读时实时推算**（速率型按速率累计、周期型按周期边界）——这是多端一致性的关键：同步面上不存在「基于陈旧基线的自动写」。
- 固化（两行模式）只发生在真实写入事务里，**不看本进程是否同步过、也不等首次拉取**：
  `reset` 行记「旧周期满值 → 0」（`event_at` = 边界），紧随的 `set` 从 0 起算；
  `regen` 行记恢复后的绝对值，紧随的业务行从恢复后基线起算。两条行各自满足
  `new = old + delta`，供撤销和按来源聚合使用；同步仍按绝对值覆盖，**不按 delta 相加**。当前值已经是 0 时
  **不落 0→0 的 `reset`**，直接写业务行。业务写入写的是现场真值（OCR 观测 / DSL /
  手动），不依赖远端基线；即使他端把某条系统行认成最新值，下一次观测写回的绝对真值
  会把它覆盖回来——账本按观测自愈，不靠拒绝本地落库来避险。

### 2.1 版本归属与实际物化边界

本文的 **v1.3** 是覆盖语义与取消同步状态闸门的收敛；`reset` 改为读时派生、写入时
物化已经记入 **v1.4**，本次自定义关联目标修复属于该实现的补齐。不改应用版本号、
本地 schema v13 或线上 `protocol_version: 2`，它们不是同一套版本编号。

- 读取 `quota` / `regen` 只算当前值，不因读取写历史、上传记录或把派生值反写条目。
  正常跨日、跨周或离线多个周期后再读写都受支持。
- 业务确实改变值且存在待固化变化时，`quota` 写 `reset(N→0)` 后写 `set(0→M)`；
  `regen` 写 `regen(N→有效值)` 后写 `set(有效值→M)`。归零/恢复行是机制，来源留空；
  业务行保留来源及关联来源，撤销业务行只反向抵消业务增减，不扣掉自动恢复量。
- 两行不是固定条数：原值已为 0 不加 `0→0 reset`，没有恢复量不加 `regen`；
  普通 quota 写入与派生值相同会短路。直接 regen 写入沿用“先固化恢复、再判业务变化”
  的顺序，因此无业务变化时可能仅有一条真实恢复行；本次不统一这些已有顺序。
  关联目标则在候选通过无变化/脚本准入检查后才固化，已满且不变的目标不新增行。
- **本次只补自定义 regen 关联目标**：正式支持的 `sync_targets` 指向 regen 时，
  与直接写该项一样先固化恢复，再落业务行；源码复用现有恢复物化函数和同一个 SQLite
  事务。业务落库失败时，源项、恢复行、目标业务行一并回滚，不新增事务或协调机制。
  系统自带规则的关联目标原本均为 stock，体力等直接写入原本正常，不能将此描述成
  默认游戏流程普遍出错。

### 2.2 设计限制与非目标

以下是 **v1.3 / v1.4 主动接受的边界**，不是待修复清单，不因本轮核验新增补偿机制。

1. **不处理一次写入恰好横跨重置瞬间的精确时间一致性。** 不为此统一全链路时钟，
   不引入事务时钟、周期协调或全局调度。游戏账本中这种瞬间竞态的收益不足以抵偿
   实现复杂度、运行状态与长期维护成本。这里排除的是单次操作内部恰跨重置点，
   **不是不支持正常跨日使用**；重置之后读取、继续计数仍按本周期有效值处理。
2. **不为跨周期长期陈旧编辑额外升级 CAS 语义。** 上周期值在本周期按定义归零，
   以本周期有效值计算业务变更，不要求因旧周期快照的差异再额外拒绝该操作。
   同周期现有写入冲突保护继续保留；不将更严格的跨周期编辑策略列为数据修复任务。
3. **同步不是来源历史的逐字段复制。** `set` / `reset` / `regen` 同步绝对值，
   并沿用接收端构建回放账本的语义；不保证来源数值 `old_value` / `delta_value`
   逐字段相同。当前上行不带这两个数值字段，接收端按已有条目重建变化前值；
   网页历史展示类型与绝对值。正常按序同步、同秒连续两行和重复拉取必须保持正确，
   读端先计算出派生值不等于已经物化或额外写了一行。
   不以人为拼装的乱序接收状态要求增加多主冲突协调或完整历史复制协议：
   现有单写使用场景没有这项需求，扩展协议与历史状态只会增加维护负担。

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

- `POST /api/sync/state`：`protocol_version: 2`；各通道水位与「有没有」（0/1）、快照摘要与定义/布局 `sha256`——全部按表查（见 §4），版本行本身只存 `documents_head` 与 `users_rev`
- `POST /api/sync/profile/push`：接受 set/reset/regen/meta；按 `(account_id, op_id)` 幂等；含 delta 整批 409
- `POST /api/sync/profile/pull`：下发四种类型，`since_remote_id` 增量
- `POST /api/sync/profile/snapshot`：清单/详情/上传（空体=空基线播种）
- `GET /api/v2/profile/entries`：物化视图（state 三态 + entries 含 locked）
- `POST /api/sync/profile/history`：分页（`remote_id` 降序）；可按 `username` / `instance_id` / `op_types`（多选，白名单） / `type`+`key` 精确筛选，`total` 按同一条件 `COUNT(*)`（只在第一页算）。筛选列各有索引：`(account_id, op_type, remote_id)` 与 `(account_id, type, key, remote_id)`
- 错误信封：`{error: name}`；`client_update_required_for_delta` = 409 提示升级

## 7. 展示

- 桌面历史页类型标签：更新/重置/恢复/锁定/增量；撤销只认 set/delta
- 桌面总览 / 用户信息：quota 显示 `periods.quota_current_value` 派生的当前值，与 DSL 同口径
- 网页总览：entries 矩阵（锁定条目显示 `[锁定]`）；再生型（速率/准点两种）与 quota 的周期归零都在
  前端读时推算（`web/src/regen.ts`、`web/src/quota.ts`，规则逐条对齐 `periods.py` / `regen.py`）。
  内置周期 `day`/`week`/`month` 就地实现；燕云插件周期（`season`/`half_season`）由
  `web/src/yysls/periods.ts` 按赛季表判定——网页端是「自带燕云插件的包装」，自带的是**派生**
  出来的赛季日期表（`web/src/yysls/seasons.ts`，由 `npm run seasons` 从主工程
  `config/system/yysls/game_config/seasons.yaml` 生成，`npm run seasons:check` 是漂移门禁；
  PC 侧仍是插件注册解析器，业务与框架分离不变）。
  两层都算不出来时（表里没有的周期名、赛季表之外、缺时间戳）退回存储值，不凭空清零。
  写入仍只由客户端完成，网页当前只读。
- 网页历史页：`remote_id` 降序、meta→锁定
- Android：数据项锁定显示 `[锁定]`，历史行带类型标签，时间字段为 `event_at`；当前值与桌面共用
  同一份 Python 读入口（`ondevice/workbench.py` → `profile_read_all`）

## 8. 版本历史

- **v1.4**（2026-11-18）：`reset` 与 `regen` 统一为**周期派生值**——读时计算、不落库、
  不进账本；只有真实写入时才固化成 `reset` + `set` 两行（当前值为 0 时不落 0→0 行）。
  引擎取消周期性 reset 写入，只保留条目初始化与派生值变化时的界面刷新信号；桌面总览 /
  用户信息改用派生值显示，调度把 tick 对齐到周期边界，05:00:00 当秒归零。
  网页总览同步跟上：quota 周期归零与再生（速率/准点）都在前端读时推算，与桌面同一套公式；
  燕云赛季周期由网页自带的派生赛季表解析（`npm run seasons` 生成、`seasons:check` 漂移门禁）。
  同期补齐自定义 regen 关联目标的恢复固化与业务撤销；直接写入、stock/quota 关联规则不变。
- **v1.3**（2026-11-15）：写明覆盖语义契约（`set`/`reset`/`regen` 的 `new_value` 被任一端
  承认为最新值，`meta` 例外）；删除「首次干净拉取前不推算周期恢复」的写入闸门——周期
  自动写已取消，固化随写入入口发生，不再依赖同步状态；纯读取不固化。
  后续 v1.4 补齐 quota 读时归零；两版共用的主动取舍见[设计限制与非目标](#22-设计限制与非目标)。
- **v1.2**（本文，2026-11-14）：类型三分类、水位门、meta 锁传播、dataclass、原表原地迁移
- **v1.1**（[07-set-only-v1.1.md](07-set-only-v1.1.md)，2026-11-12）：set-only 数据模型（历史方案）
- 更早：delta 双向同步（已废弃）
