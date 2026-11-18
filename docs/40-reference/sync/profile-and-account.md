# 契约 C：Profile 与账户同步

本文是 Profile 账本同步与账户同步状态的权威契约，只写当前事实。实现主源：
`src/lvjiang/core/profile/snapshot_sync.py`（客户端一轮编排与回放）、
`server/src/profile_sync.mjs`（服务端 push/pull/快照）、`server/src/profile_state.mjs`
（同步状态与清空）。需求侧叙述见 `docs/30-design/decisions/profile/08-profile-sync.md`。

## 1. 编号与行身份

- 本地账本 `profile_history.local_id` 与云端 `remote_id` 是**两套编号**，互不可比。
  `local_id` 是本机自增顺序；`remote_id` 是云端提交顺序（服务端插入后按账号连续编号）。
  见 `src/lvjiang/core/profile/snapshot_sync.py`、`server/src/profile_sync.mjs`。
- 账本行的身份是 `(instance_id, local_id)`——「哪台设备的第几号操作」，与本地自增
  id 无关；库重建后依然对得上，重复回放天然幂等（`server/src/profile_sync.mjs`）。
- `op_id` 是行的稳定锚点：本机写入生成 uuid（`src/lvjiang/core/profile/ledger.py`），
  缺失时服务端按 `legacy:{instance_id}:{local_id}` 兜底。回放判重按 `op_id`
  （`src/lvjiang/core/profile/repository.py` 的 `db_op_id_seen`），重复上行按
  `(account_id, op_id)` 幂等（`server/src/profile_sync.mjs`）。

## 2. 操作类型全集

上行与下行只接受四种 `op`：`set`、`reset`、`regen`、`meta`
（`src/lvjiang/core/profile/snapshot_sync.py` `_apply_op`；
`server/src/profile_protocol.mjs` `validSetOp`）。语义要点：

- `set` / `reset` / `regen` 三种的值语义都是**覆盖**：`new_value` 是绝对值，任一端
  回放落地后即为该端当前值。回放裁决按 `(event_at, 到达序)`：时间新者胜；同一秒比
  `remote_id`（`src/lvjiang/core/profile/arbitrate.py`）。`op_id` 是随机 uuid，
  不参与判先后。
- `meta` 是锁状态传播：只改 `entries.locked`，`new_value` 的 0/1 永不进 value，
  `event_at` 也不参与 value 裁决（`src/lvjiang/core/profile/snapshot_sync.py`）。
- `delta` 是 v1.1 遗留类型，新代码不产生；上行含 delta 时服务端整批 409
  `client_update_required_for_delta`（`server/src/profile_sync.mjs`）。
- 回放遇到上述白名单之外的 `op` 直接忽略；未知的 `op_type` 落库时按 `set` 兜底
  （`server/src/profile_sync.mjs`）。

## 3. 一轮同步的顺序

`sync_all`（`src/lvjiang/core/profile/snapshot_sync.py`）：

1. 确认协议版本 `protocol_version == 2`；拿到响应且不符时阻断，一个 op 都不发
   （网络失败不阻断，由真正的同步调用报错）。
2. 下行 `pull_and_replay`：必要时先合并最新快照（bootstrap），再从本地水位
   `remote_history_id` 起按 `remote_id` 升序分页回放。
3. 上行 `push_local`：上传基线之上的本机写入（先拉后推，与快照上传前的强制拉取
   同一约束）。
4. 云端一份快照都没有而本机有数据时，`_ensure_baseline` 自动建立首份基线。

Android 镜像端同序（`src/lvjiang/core/ondevice/account_api.py` `_sync_round`），
但定义只拉不推；**PC 端镜像模式整体跳过上行**——手机 db 是 PC 的镜像，推了必污染；
云端模式下手机照常 push 本机产生的行。

## 4. 上行门（基线与单写设备）

- 上行只扫 `remote_id IS NULL AND instance_id = 本机 AND local_id > 基线`
  （`src/lvjiang/core/profile/sync_log.py` `db_list_unacked`）。作者过滤保证：
  **每个角色的每台设备只写自己产生的行**，跨端回放落地的行（`instance_id` 是
  来源设备）永不被代传——这是「单角色单写设备」的实现方式，也是回声不存在的根本原因。
- 基线两级：主路径是 account 状态里的 `local_history_id`（快照上传/落地那一刻推进）；
  状态丢失时从账本推导 `MAX(local_id) WHERE remote_id > 0`（引导回放的行落在账本
  尾部，天然把存量旧行挡在基线之下）。低于基线的行永不扫描、永不重判
  （`src/lvjiang/core/profile/snapshot_sync.py` `push_local`）。

## 5. 首次基线与历史推送为零

- 云端首份基线（快照）可以由任何登录端创建，包括空列表播种：空快照的水位
  **无条件绑 0**，不读 head（`server/src/profile_snapshot.mjs`）。初始播种走
  `claim_init` 锁（10 分钟 TTL），只有锁主才播种，避免多端并发各建一份
  （`server/src/profile_snapshot.mjs`、`src/lvjiang/core/sync/initial.py`）。
- 非空快照上传前**必须先完成一次干净 pull**：快照绑定的 `snapshot_remote_id`
  语义是「该 id 及之前的变更都已体现在这份 entries 里」，不先拉会把别设备刚推的行
  吞掉（`src/lvjiang/core/profile/snapshot_sync.py` `refresh_snapshot`）。
- 历史推送为零：快照落地/刷新时把 `local_history_id` 推进到当时的本地最大
  `local_id`，**快照之前积累的全部本地历史行都不上行**——它们已经整体体现在快照
  entries 里。账本上云只发生在快照之后，历史边界即快照时刻
  （`src/lvjiang/core/profile/snapshot_sync.py`）。
- 服务端有基线后才物化 `profile_entries`；无基线时只进账本，等快照上传后整表覆盖
  （`server/src/profile_sync.mjs`、`server/src/profile_snapshot.mjs`）。

## 6. 账户版本行与空轮读取策略

- 云端 `account_sync_version` 每账号一行，只存两样**无法从数据派生**的东西：
  `documents_head`（文档发号基准）与 `users_rev`（名册版本号）。**写入方只有两个**：
  文档发号（`server/src/documents.mjs`）与名册真实变化时的 `+1`
  （`server/src/sync_version.mjs` `syncVersionUsers`）。Profile 通道**从不写**版本行。
- 其余状态全部按表查：`history_head` = `MAX(remote_id)`（索引反向 seek，账本编号
  无空洞故 head 即条数）、快照摘要 = `profile_snapshot` 最新一行、定义/布局摘要 =
  两张专表的 `sha256` 点查、`documents/snapshot_count/users` 三个「有没有」只答 0/1
  （`server/src/profile_state.mjs` `syncState`）。没有任何缓存副本与真实数据漂移。
- **读取时机**：客户端每轮同步开头经 `/api/sync/state` 取一次版本行，轮内共享缓存
  （`src/lvjiang/core/sync/round_state.py`）；本轮任何写请求成功后 `invalidate()`
  丢弃缓存，再取时重新请求。
- **空轮**：空闲轮唯一的网络请求就是这次 `/api/sync/state`。各通道拿版本行与本地
  记录比较，相同就整条跳过拉取——profile 的空轮判断是「云端 `history_head` 与本地
  水位相同」（`src/lvjiang/core/profile/snapshot_sync.py` `pull_and_replay`）。
  版本比较一律用「不相等」而不是「大于」：清除同步、换库会让云端归零或回退，
  不等就拉，宁可多拉一次也不漏。版本行字段缺失（老服务端）或请求失败时**照常拉取**，
  绝不把「问不到」当「没变化」或「明确为空」。
- 上行侧同理：本批无待上行行时 `push_local` 直接返回，不发请求。

## 7. 进度校验与切账户重新确认

- 同步进度（水位、快照 id、基线）记在**本机** `account.json` 的 `profile_sync`
  （`src/lvjiang/core/account/service.py`）；丢了由 `_recover_state` 从云端摘要与
  本地账本重新推导，不需要重放或按快照重建
  （`src/lvjiang/core/profile/snapshot_sync.py`）。
- 只在 `remote_history_id > 云端 head` 时报「进度比云端新」（云端可能被重建或换库），
  提示清除同步后重新登录同步；`local_history_id` 是本地编号，不参与该比较
  （`src/lvjiang/core/profile/snapshot_sync.py` `state_problem`）。
- **同账号重新登录保留运行态**；只有切换到不同账号才换一份干净的
  `account.json`（含 `profile_sync`）——旧账号的水位对新账号毫无意义，切账户后
  下轮同步必然走 bootstrap / 覆盖确认重新对齐
  （`src/lvjiang/core/account/service.py` `login`）。
- 覆盖（跟随云端快照整库改写本地）从来不是静默的：快照来自别的设备且本地有数据，
  或本地存在晚于快照创建时间的本机写入时，都会列入需确认的清单并先备份
  （`src/lvjiang/core/profile/snapshot_sync.py` `overwrite_warnings`、
  `src/lvjiang/core/sync/initial.py` `initial_sync` 的二次确认）。
- 服务端 `/api/sync/clear` 清空账号全部同步数据前验密码，返回「明确为空」状态；
  客户端必须把「请求失败」与「明确为空」分开（`server/src/profile_state.mjs`）。

## 8. 时间语义

- 上行 `happened_at` 是客户端本地钟面。服务端把它按 UTC 解析钟面数字再减去
  客户端随批上报的时区偏移得到真实时刻；只拒绝无法解析或明显超前的行
  （超前 2 天内可收，`server/src/profile_protocol.mjs`、`server/src/profile_sync.mjs`）。
- 回放落地时条目 `event_at` 取源 op 的 `happened_at` 而不是本机墙钟——两台设备的
  时钟互不可信，否则回放后的条目会永久压住此后到达的远端写入
  （`src/lvjiang/core/profile/snapshot_sync.py` `_apply_op`）。
- 配额周期边界是本地钟面（如每天 05:00）；比较周期归属时带偏移的时间先去掉时区
  保留钟面，不换算到本机时区（`src/lvjiang/core/profile/snapshot_sync.py`
  `_wall_clock`）。
