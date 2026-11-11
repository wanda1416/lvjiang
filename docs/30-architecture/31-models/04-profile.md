# Profile 共享模块

Profile 是主引擎自带的用户数据模块，不属于任何具体 app。它提供
quota / regen / stock / note 四类定义、SQLite 持久化、后台周期计算、
DSL 读写函数，以及「用户总览」和「用户信息」两个通用页面。

## 模块边界

| 责任 | 归属 |
|------|------|
| 数据模型、YAML schema、SQLite repository、计算引擎、同步和 DSL 函数 | `core/profile/` 与 `workflows/builtins/profile.py` |
| 用户总览、用户信息、定义编辑器 | `ui/profile/` |
| 每日、每周、每月边界 | core 内建 |
| 赛季、半赛季等业务边界 | app 通过 `AppHooks.profile_period_modules` 注册 |
| 备战方案、调律管理等游戏界面 | yysls app |

主窗口右侧 Tab 的固定顺序是：「运行日志」→「用户总览」→
「用户信息」→ app hook 注册的页面。yysls 只在此后追加「备战方案」
和「调律管理」。

## 模块与数据流

| 模块 | 职责 |
|---|---|
| `core/profile/models.py` | quota / regen / stock / note 四类 `KeyDef` 与解析 |
| `core/profile/schema.py` | `profile.yaml` 加载、校验与写回（定义是用户资产，只增改不删除） |
| `core/profile/store.py` | 可见结构（`profile.json` 分组/列/列宽）与 `interface.json` 的 `alert_history`、活跃分组 |
| `core/profile/layout.py` | `profile.json` 读写、v1 迁移、列宽折算与 clamp |
| `core/profile/repository.py` | `profile.db` 存储层与 `_ensure_schema` 逐版迁移 |
| `core/profile/engine.py` / `regen.py` / `periods.py` | 周期重置、再生计算与周期注册表 |
| `core/profile/service.py` / `sync.py` / `triggers.py` | 统一读写语义、`sync_targets` 同步链与变更脚本 |
| `core/profile/snapshot_sync.py` / `key_rename.py` | 账本同步、全用户改名与引用维护 |
| `ui/profile/` | 用户总览、用户信息、定义编辑器、变更记录 |
| `workflows/builtins/profile.py` | `profile_action` / `profile_read` 等 DSL 函数 |
| `apps/yysls/core/season_periods.py` | 燕云的 season / half_season 周期边界 |

数据流：场景截图 → 业务工作流（`subcall/profile_registry.wf` 先声明所需 key）→ OCR/识别 →
`profile_action` 写入 `profile.db`（附变更记录）→ 引擎做周期重置与再生推算 → 用户总览 /
用户信息渲染 → 定义与账本按各自的通道同步（`profile_definition`、`profile_layout`、
账本行）。

## 存储契约

以下为当前契约，数据模型数据库使用 **schema v10**：

- `config/session/profile.yaml` 仍以四个模型的列表保存 key。每个 `KeyDef` 带
  `group`；旧条目缺失或空白时归入稳定 key `default`，下次保存显式写出。
- `config/session/profile.db` 当前 schema v10，按 `schema_version` 顺序升级；逐版迁移
  写在 `core/profile/repository.py` 的 `_ensure_schema`（v8 加 locked、v9 把历史改为同步
  账本并删审计表、v10 给史行加源实例标识）。升级前用 SQLite backup 保存 `profile.before-
  vN.db`，含 WAL 中已提交内容。
- `profile_entries` 仍以 `(username, type, key)` 为复合主键。
- `profile_history` 保留 ID、时间、用户、模型、key、数值/文本旧新值和来源；
  `detail` 替换为 `delta_value REAL NULL` 与 `sync_from TEXT NULL`。
  同步来源使用 `model:key`，变动量记录 clamp 后实际变化，不能拿入库整数相减
  代替连续再生的语义变动。NULL 表示未知，不等于零。
- `change_type` 为 `action`、`override`、`tick`、`reset`；`tick` 只表示自动恢复，
  周期清零使用 `reset`，传给变更脚本的事件类型也一致。
- `profile_key_renames` 保存全用户重命名时间、操作 ID、模型、旧新 key，以及
  entries/history/sync_from 更新数量；独立的 key 重命名记录对话框按链显示曾用名，
  不混入数值历史页。
- `interface.json` 只留 `alert_history`（提醒去重）与 `ui_state.profile_active_group`
  （本机活跃分组）；分组、列与列宽已迁到 `profile.json`（见下节）。

定义分组不另建顶层或模型内的 `groups` 节点。编辑器从一个模型的完整 `KeyDef`
序列按 `group` 首次出现顺序派生分组，组内保持 key 的相对顺序；没有 key 的组随即
消失。界面中的分组筛选是编辑草稿状态，保存必须写回该模型的全部草稿，不能只写
当前可见组。分组使用带滚动按钮的 Tab 导航，中文界面将 `default` 显示为「默认」。
表格右键菜单通过可编辑下拉框选择已有分组或输入新分组，并将结果一次应用到全部
选中行；双击分组列不进入编辑。

用户总览的可见结构（分组、列顺序、合计开关、列宽）不在 `profile.yaml` 里，也不在
`interface.json` 里，而是独立文件 `config/session/profile.json`——细节见下面的
「页面结构文件」。它与 `KeyDef.group`（定义分组）没有同步、重命名或默认选择关系。

## 页面结构文件（`profile.json`）

定义管「有哪些数据」，可见结构管「怎么看这些数据」。两者对称地各占一个文件：

| 文件 | 内容 | 同步通道 |
|---|---|---|
| `config/session/profile.yaml` | key 定义：模型、标签、上限、周期、同步目标 | `profile_definition` 表，随常规同步 |
| `config/session/profile.json` | 分组、列顺序、合计开关、列宽 | `profile_layout` 表，**只有用户点按钮才走** |

结构文件的形状：`version` + `metric` + `overview{group_order, groups{name: columns /
show_totals / name_column_width / column_widths}}`。

四条硬约定：

- **列宽按列 key 存**（不按下标数组），用户名列宽全局一份；因此增删列不需要搬数组，
  分组改名或换字段时宽度跟着走。
- **活跃分组不进文件**：它留在 `interface.json` 的 `ui_state.profile_active_group`，
  属于本机视图状态。
- **失效列保留**：`profile.yaml` 里已删的 key 继续留在 `columns` 里，渲染层跳过。
- **文件不带机器信息**：写入来源由服务端按认证会话记录（`origin_*` 三列），客户端据此
  判断「远端是不是本实例写的」，据此决定上传时是否提醒覆盖。

列宽是像素值，跨机器使用时按 `metric`（字体像素、设备缩放）折算后再按当前字体实测的
表头宽度 clamp，折算结果**不回写**文件——只有用户拖动列宽才更新记录。

旧版本的 `interface.json.profile.overview_groups` 与 `ui_state.profile_overview` 在启动
首次访问时一次性迁入（带 `_backup-<时间戳>/` 备份并清理旧节点），迁移后不存在双源。
实现见 `core/profile/layout.py`，跨端搬运见 `core/sync/layout_sync.py`；
需求与确认流程见 [用户总览页面结构](../../20-requirements/40-profile/06-overview-layout-sync.md)。

## 周期恢复的同步闸门

准点恢复（`regen_type=boundary`，如体力每日 05:00 +450、乌木每周五 05:00 +5）仍按
原设计物化成 `set` 账本行并随上行通道同步——它是真实发生的历史。派生值与真实观测的
区别靠「先同步、再推算」保证：`ProfileEngine.tick` 与 `profile_action` 的 regen 固化
在本次进程完成首次成功 pull 之前一律跳过（`core/profile/regen_gate.py`）。

- 幂等由条目锚点承担：`compute_boundary_value` 数的是「锚点到 now 跨过几个边界」，
  同步到位后锚点已落在本周期，边界数为 0、值不变即不写，因此不需要额外的周期幂等表。
- 「完成」指 `pull_and_replay` 无错误、无待决且回放行已落地；单条 op 回放失败只进
  `report.errors`、游标照旧前移，所以不能以 HTTP 成功作为判据。
- 未登录（**没有凭据，因此不会上行**）时放行本地推进，否则不登录的用户永远不会恢复。
  已登录但断网、首次拉取失败或尚未建立同步时，周期恢复**暂停**——这是有意的取舍：
  宁可让恢复停一会儿，也不写坏跨端数据。
- 登录态走三值探针（`account.service.stored_session_state`）：只有确无凭据才放行；
  凭据库读不到时按「可能有登录态」拦住（fail-closed，避免内存里仍有可上行令牌却误放行）。
  登录 / 恢复登录 / 登出统一经 `_store_refresh_token` 主动作废闸门的观测缓存；换账号
  会重置闸门，要求新账号先完成首次干净拉取。
- 闸门关闭时准点恢复尚未物化，`profile_action` 的写入基线取**存储值**而非计算值，
  避免「未落库的恢复量」被并进消耗行（`new != old + delta`）。实时恢复没有独立账本行，
  闸门只挡落库、计算基线照旧，小数进度由锚点回拨保留。
- 已知残差：两台设备都离线跨过同一周期仍会各写一条 `set`，靠接收端的旧 `set` 时间
  护栏与后续 pull 收敛；彻底消除需要 op 携带前置基线并做 CAS，属于后续议题。

## 历史升级与身份维护

v7 升级前通过 SQLite backup 接口保存 `profile.before-v7.db`，包括 WAL 中已提交
的数据。升级与版本登记处于一个 `BEGIN IMMEDIATE` 事务；失败回滚，成功后不再
解释旧字符串。`delta:`、`regen:` 解析实际变动量；`tick + reset:0` 改为 `reset`。
`sync_from:` 解析来源，模型能唯一确定时补齐命名空间；旧同步记录中的连续再生
变动无法准确反推时留 NULL。无法理解的非空原文按历史 ID 存入
`profile_history_legacy`，只供迁移核对，不参与业务读写。

数据模型定义和总览列的「编辑当前列」共用 `open_key_editor()`。既有 key 输入框
默认禁用，必须先点击 key 行的「编辑」按钮，才能聚焦、选择与修改。
单 key 对话框右下角统一为「保存」「取消」；左下角「查看 key 重命名记录」打开
独立审计页，未保存的新定义禁用该按钮。查看始终针对已保存的身份，不使用正在
输入的新名称。内层「保存」立即保存该定义；重命名调用
`key_rename.save_renamed_definitions()`，取消内层不修改数据库。
目标 key 已有定义、当前值或历史时拒绝，不自动合并。
重命名只更新身份，保留数值、文本、更新时间及历史时间，不触发变更脚本。

维护更新所有用户的 entries.key、history.key、history.sync_from，并同步定义的
sync_targets、总览列、告警去重标识、批量指定排序及生命周期参数 profile_key。
每次内层保存单独生成重命名审计，不在外层确认时延迟或合并操作。外层同步更新
草稿与基线；外层取消不会撤销已保存的修改。外层「保存」只合并尚未保存的删除、
分组和排序等变化，不以旧的定义快照覆盖最新标签、上限或重命名结果。
设备任务仍在运行或 Profile 脚本队列忙碌时拒绝重命名；普通数据
写入和周期 tick 通过进程内维护锁与重命名串行，不引入用户执行锁。

配置文件与 SQLite 不能共用物理事务。操作开始前把 `(model, old, new)` 写入
恢复日志。重放先从磁盘重新加载 `profile.yaml`，再按这份列表改写定义自身的
key、同步引用和数据库里的旧 key。磁盘已经是新 key、或库里已经没有旧 key 时，
重放保持现状。失败或异常退出后，下次数据库初始化完成同一次重放。恢复完成
清理日志并重载缓存，不永久维护旧 key 别名。

DSL 文件不扫描、不自动全文替换。确认窗口只提示用户自行核对引用；用户
必须自行修改脚本、声明注册表及其他自由参数，否则脚本可能继续写入旧 key。
真实 session 的一次性复核使用 `scripts/one_off/verify_profile_key_rename.py`，只在
临时副本中运行，不属于 CI，且不会输出用户或 key 原文。

「用户信息」页的便利贴不属于 Profile：它不需要预先在
`profile.yaml` 定义 key，也不参与 DSL、周期计算或变更历史。便利贴
由 `core/user_notes.py` 按用户独立存储在
`config/session/users/{username}.notes.json`，避免被工作流的
`{username}.json` session 快照整体回写覆盖。

用户头像同样不属于 Profile。裁剪后的 512×512 PNG 统一存放在
`config/session/avatars/`，形成可复用的历史头像库；`session.json.users`
中的每个用户只保存安全的头像文件名。删除用户只移除引用，不删除头像库资产，
因为同一头像可以被多个用户选择。

Profile 是全局共享的用户数据。不增加 `app_id`，不做 app 分库或租户隔离。
多个 app 可以同时读写一份 Profile；key 位于共享命名空间，应由定义者
自行避免冲突。

## 变更脚本

每个 `KeyDef` 可通过 `change_script` 关联一个相对于 `workflows/` 的 DSL 文件。
定义的 `change_script_sync` 默认 `false`，未声明时保持异步；勾选“同步修改”后
设为 `true`，清除脚本时同时清除此选择。计算候选变化后冻结事件，
包括脚本路径、执行方式及变化快照。同步脚本在主字段与历史落盘前执行，
通过后才写入；它使用调用线程，不入队等候。异步事件暂存在当前事务中，
只有事务提交后才进入应用级 `ProfileScriptRunner` FIFO 队列，提交方不等待执行。
事件向脚本注入
`origin_key`、`origin_model`、`key`、`model`、`old_value`、`new_value`、
`delta`、`source` 和 `change_type`。

Profile 脚本每次使用新的轻量 `WorkflowEngine`，加载该用户的 Session 读取快照，
但不装配截图、OCR、输入、布局和 Session 保存回调。它调用无锁执行入口，既不
检查也不等待用户执行锁；因此不能用整份 Session 快照回写，否则会覆盖并行设备
任务的更新。Profile 写入仍走 Profile repository 的独立原子管线。

脚本再次写 Profile 时自动透传原始触发 key 和内部触发路径。目标节点已经位于
路径中时，在落库前拒绝该次写入，覆盖直接回环和多节点回环。一次外部写入及其同步
脚本链共用 SQLite `BEGIN IMMEDIATE` 事务与连接；所有关联数值、文本、历史和声明式
同步都由最外层提交。拒绝、脚本异常、停止或 CAS 冲突回滚整条当前同步链，
暂存的异步事件一并丢弃。即使 DSL 捕获拒绝异常，也不能把已拒绝事务提交。
异步脚本执行时开启自己的事务；失败只回滚它自己的写入，不回滚此前已提交的父事件。
用户执行锁不参与事务；进程内维护锁与 SQLite 写锁共同保证读累计和写入不会交错。
应用关闭时停止接收并丢弃未执行事件，队列不持久化。

每个目标按自己的执行方式调度：同步父节点等待同步子节点，异步子节点只提交队列；
异步父节点执行时可以调用同步子节点。不等待此前无关异步事件，不继承父节点模式。
同步脚本读 `profile_get` 能看到此前暂存的子写入，但主字段尚未写入，候选值使用
`new_value` / `delta`。异步事件的变量仍是提交时快照。
同步脚本、主字段写入、声明式 `sync_targets` 依次进行；异步事件在提交后派发。
普通同值写入不触发脚本，quota 周期重置的 `0 → 0` 仍保留 `reset` 事件。
数值 delta 为钳位后的实际变化量；文本 delta 为空，事件变量不是执行时的最新 DB 值。

Profile 写入持有可重入维护锁，同步脚本在同一线程重入写入，不获取用户执行锁，
不能通过“入异步队列再等待”实现同步，否则持锁方会与队列线程互等。
同步执行计入脚本忙碌状态，定义维护不能绕过正在执行的同步脚本。
事务只管理 Profile DB，不包括配置文件；事务内不能用 `profile_declare` 新建模型定义，
依赖定义应在任务开始前声明。UI 新来源词表也只在数据提交成功后登记。
存在同步脚本定义时，总览的手动写入放在后台串行执行，完成或失败后在 UI 线程处理刷新
和提示。同步脚本应保持短小，不使用长等待、弹窗或设备操作。
local 的 `workflows/profile/example.wf` 提供通用事件、混合链、防环及异常示例，
不自动创建定义或绑定真实数据。它不是随发行版预置的任务。
手动复核示例运行 `python scripts/one_off/verify_profile_change_example.py`，
验证进程的配置根、修订缓存、用户目录与 DB 均使用临时目录；循环与异常日志是预期结果。

### 历史聚合与拒绝

`profile_history_aggregate(key, operation="sum", source=null, change_types=null,
direction="all", since=null, until=null)` 聚合当前执行用户的历史变动量。
key 可以写成 `stock:key`；operation 支持 sum/count/min/max/avg，空结果为 0。
direction 为 all/pos/neg；类型可用字符串或列表；时间范围为 `[since, until)`。
查询使用参数化 SQL，事务内可见已暂存的历史，不受历史对话框分页上限影响。
缺少 delta 的旧记录按新旧值之差读取，不修改原历史。
`profile_reject(message)` 仅用于同步脚本，拒绝整个当前事务并给出可行动原因。
拒绝抛出的 `ProfileWriteRejected` 属于预期结果：日志只记一行 WARNING，不打印
堆栈；DSL `try/catch` 仍可捕获它，但捕获后事务照样回滚，不会提交半套写入。

local 的 `profile/chengyinshi_limit.wf` 校验承音石获取：天赋领奖 4、等级提升 4、
不肝商店 13、赛季不肝 8、武墓证道 3；脚本顶部参数区可修改上限。
正向 action/override 按来源累计，消耗不退额度，未知获取来源拒绝；
`历史累计 + 候选实际增量 > 上限` 时拒绝，不按当前余额或自动周期重置。

### 满值锁定

`SyncTargetDef.lock_on_cap` 默认开启，挂在同步目标这一条边上：该目标达到硬上限时，
把源 key 在总览显示为 `—` 并禁止编辑，避免用户继续记录无法传导的进度。开关属于边而
不属于源 key——同步目标是多源的，同一个目标满了不代表别的源也该停用。真实资源
（心力）和派生计数器（每周消耗中转）应取消该边的勾选，此时目标满只表示增量不再写入
它，本项照常展示与记账。多条边逐条判断，豁免一条不会放过同一 key 的另一条满额边。

目标侧写入始终由 clamp 拒绝，锁定只是提示，不是数据完整性手段。单元格显示与右键菜单
共用 `locked_sync_targets()` 判定，不得只在其中一处拦截，否则会出现「显示为 — 但仍能
增减」的自相矛盾状态。

### 历史查询与筛选

变更记录对话框（`ui/profile/history_dialog.py` + `history_filters.py`）与三处入口
之间只有一份契约：`core/profile/history_query.py` 的 `HistoryQuery`。条件分两类
——精确集合（用户 / 键 / 变动类型 / 来源 / 同步来源）与模糊词（关键词、旧值、
新值），另有时间区间和变动量方向；空集合表示"不限"，空字符串是真实取值
（表达"未记录来源"），两者不可混用。

仓储层的 `_history_where` 是唯一的 WHERE 构造点，`history_page` / `history_count` /
`history_facets` / `history_facet_total` / `history_delta_totals` 全部从它生成 SQL，
排序走白名单。`HistoryQuery.relaxed(dimension)` 返回"去掉该维度条件"的副本，
聚合候选据此实现"按其余条件统计"，避免勾选后候选塌缩成只剩自己。旧的
`get_history` / `count_history`（单用户 / 单键）是便捷包装，不再各自拼条件。

变动量方向使用 `CASE WHEN delta_value IS NOT NULL ... WHEN old_value IS NOT NULL
THEN new_value - old_value END`：起点未知时结果为 NULL，既不算增加也不算减少，
与「变动量」列显示的「—」保持同一口径。历史累计额度另有一套
`COALESCE(delta_value, new_value - COALESCE(old_value, 0))` 口径，两者不共用。

界面层把"布局稳定"当契约：列集合与列宽固定（条件变化不隐藏列、不重排列宽），
合计与页码使用 `history_filters.ElidedLabel`（`sizeHint` 按全文、`minimumSizeHint`
为 0），条件标签单条限宽省略。否则随数据变长的文字会拖着对话框最小宽度上下跳。

### 历史删除

历史对话框每行提供撤销和删除。删除需二次确认，仅按 id 和用户/模型/key
精确归属删除这一行，不改余额、不触发脚本、不追加删除历史。
删除后重新计算分页与按钮状态，不把删除伪装成数值变更通知。
删除获取记录、修改来源会改变历史累计额度；删除确认明确提示此影响。
撤销不可用时的原因（不是最新记录、记录关联变更脚本、会带走同步数据等）以
`ProfileOperationRefused` 抛出，和历史写入冲突一样按预期结果记账：弹框提示原因，
日志只记一行 WARNING，不打印堆栈。

工作流运行时只提供两种 Builder：设备 Builder 固定装配完整资源并使用用户锁，
Profile Builder 固定装配轻量无锁环境。新增第三类运行边界时应增加新的 Builder，
不得继续扩张一个可任意组合权限和资源的构造入口。

## 周期扩展

quota 的 `period` 必须在加载 `profile.yaml` 前已注册。core 默认注册
`day`、`week`、`month`。app 注册的 resolver 接收
`(reset_time, now, reset_day)` 并返回本周期边界时间。

```python
register_profile_period(
    "season",
    resolve_season_boundary,
    label="赛季",
)
```

未注册的周期会在 schema 加载或保存时报错，避免后台 tick 到运行时
才反复失败。周期名也位于全局命名空间，重复注册会报错，不允许
静默覆盖。

历史撤销使用原记录新旧值之差判断同步方向，与同步引擎共同调用
`SyncTargetDef.matches_delta()` 检查非零倍率和方向条件，避免两处规则漂移。
不满足任何目标条件的直接变更可撤销；同步来源、变更脚本、连续恢复、最新记录及
当前值校验保持原有边界。撤销通过数据库事务直接恢复并追加 undo 审计，不进入同步管线。
