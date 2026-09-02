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
| 备战方案、调律进度等游戏界面 | yysls app |

主窗口右侧 Tab 的固定顺序是：「运行日志」→「用户总览」→
「用户信息」→ app hook 注册的页面。yysls 只在此后追加「备战方案」
和「调律进度」。

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
