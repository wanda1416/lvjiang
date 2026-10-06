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

## 存储契约

以下为 v0.13.12 起的当前契约，数据模型数据库使用 schema v7：

- `config/session/profile.yaml` 仍以四个模型的列表保存 key。每个 `KeyDef` 带
  `group`；旧条目缺失或空白时归入稳定 key `default`，下次保存显式写出。
- `config/session/profile.db` 为 schema v7，按 `schema_version` 顺序升级。
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
- `session.json.profile` 中的分组、活跃分组和告警历史节点不变。

定义分组不另建顶层或模型内的 `groups` 节点。编辑器从一个模型的完整 `KeyDef`
序列按 `group` 首次出现顺序派生分组，组内保持 key 的相对顺序；没有 key 的组随即
消失。界面中的分组筛选是编辑草稿状态，保存必须写回该模型的全部草稿，不能只写
当前可见组。分组使用带滚动按钮的 Tab 导航，中文界面将 `default` 显示为「默认」。
表格右键菜单通过可编辑下拉框选择已有分组或输入新分组，并将结果一次应用到全部
选中行；双击分组列不进入编辑。

`session.json.profile.overview_groups` 继续由用户总览拥有，负责用户可见列和布局。
它与 `KeyDef.group` 没有同步、重命名或默认选择关系。

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
设备任务仍在运行、Profile 脚本队列忙碌或处于只读实例时拒绝重命名；普通数据
写入和周期 tick 通过进程内维护锁与重命名串行，不引入用户执行锁。

配置文件与 SQLite 不能共用物理事务。操作开始前创建临时恢复日志，保存所涉及
文件的旧内容；SQL 更新、审计与文件保存都成功后才提交。失败或异常退出时，
以审计中的 operation_id 判断 SQL 是否已提交，未提交则恢复文件；下次数据库
初始化也会完成恢复。恢复完成清理日志并重载缓存，不永久维护旧 key 别名。

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
实际落库值发生变化后，写入方只冻结并提交事件；应用级
`ProfileScriptRunner` 按 FIFO 顺序执行，提交方立即返回。事件向脚本注入
`origin_key`、`origin_model`、`key`、`model`、`old_value`、`new_value`、
`delta`、`source` 和 `change_type`。

Profile 脚本每次使用新的轻量 `WorkflowEngine`，加载该用户的 Session 读取快照，
但不装配截图、OCR、输入、布局和 Session 保存回调。它调用无锁执行入口，既不
检查也不等待用户执行锁；因此不能用整份 Session 快照回写，否则会覆盖并行设备
任务的更新。Profile 写入仍走 Profile repository 的独立原子管线。

脚本再次写 Profile 时自动透传原始触发 key 和内部触发路径。目标节点已经位于
路径中时，在落库前拒绝该次写入，覆盖直接回环和多节点回环。单个脚本失败只写
日志，不中断后续队列。应用关闭时停止接收并丢弃未执行事件，队列不持久化。

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
