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

此次模块升格只改变代码归属，不迁移、不改写现有数据：

- `config/session/profile.yaml` 的四模型结构不变。
- `config/session/profile.db` 仍为 schema v5。
- `profile_entries` 仍以 `(username, type, key)` 为复合主键。
- `profile_history` 表结构不变。
- `session.json.profile` 中的分组、活跃分组和告警历史节点不变。

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
