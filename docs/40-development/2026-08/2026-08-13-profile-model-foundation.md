# 开发日志 2026-08-13（七）

> 接续本日《配置三层架构落地与打包体系从 0 到 1》。
> 本轮主题：**玩家数据模型重构（上）——四模型体系、后台引擎、SQLite 迁移**。

---

## 一、元数据与列配置

- 玩家信息元数据定义与档案总览交互式列配置（`72e6ed2`）。

## 二、四模型体系

- 玩家数据模型重构——四模型体系 + 后台引擎 + 定义面板：新建 `profile_models.py`（`KeyDef`/`DailyKeyDef`/`RealtimeKeyDef`/`ResourceKeyDef`/`ActivityKeyDef`），重写 `user_profile.py`（`ProfileSchema` 替代旧 `ProfileConfig`，提取共享 IO 函数），新建 `profile_engine.py`（QThread 后台引擎，60s tick，周期重置/实时回复/阈值提醒），重写 `profile_settings_dialog.py`（四模型 Tab 定义面板，`UserRole` 存完整 `KeyDef`），重写 `profile_tab.py`（按模型分区显示，`SessionManager` 统一写入通道），`main_window.py` 新增 `register_cleanup` 机制与公共 `user_manager`/`session_manager` 属性，新增 65 个测试（`548a550`）；
- 总览分组增强——拖拽排序/列宽持久化/重置日/活动模型简化（`25c0a20`）；
- 修复拖拽列头排序递归触发导致顺序混乱（`18d0817`）；
- `profile_store` 统一接口 + `material_grid` 合并入 settings（`9592088`）；
- 总览页列宽同步持久化、Tab 页签记忆、文件写入降级及 UI 修复（`0cbedae`）；
- 修复列拖拽时多列联动错位 + 单元格悬停提示与角色名列保护（`0e1c388`）；
- 玩家数据模型三化重构及 UI 增强（`f3c3819`）。

## 三、并发安全与迁移

- `SessionManager` 并发安全改造与 `ProfileEngine` 写入优化——新增 `_session_file_lock` 跨进程文件锁（Windows msvcrt / Unix fcntl），`_save_unlocked` 改用 `mkstemp` + `os.replace` 原子写入修复 fd 泄漏，新增 `update()` 原子 RMW 方法（失败抛异常而非静默吞掉），`ProfileEngine._tick_user` 消除双重写入仅通过 mutate 回调写磁盘，`ProfileTab` 使用 QTimer 防抖刷新（`fc757dc`）；
- Profile 数据迁移到 SQLite（`b6956dd`）；
- UI 精简与赛季配置支持——移除四个 Tab 的冗余标题 Label（档案总览/角色详情/装备数据/其他信息）及 `EquipStatusPanel` 冗余标题和刷新按钮，新增角色详情 Tab 占位（`89cbd03`）。

---

## 结果

- 玩家数据模型从无到有搭建出四模型体系（daily/realtime/resource/activity）+ 后台引擎 + 定义面板，并完成 SQLite 迁移；
- 本篇工作到此阶段末，版本推进到 0.2.0（详见本日《项目整理、mypy/ruff 清零与版本发布记录》一篇的发布记录汇总）；
- 后续三模型改名与写入语义精细化见下一篇《玩家数据模型重构（下）》。

---

## 关键设计决策（用户确认）

1. **四模型体系**：daily（每日）/realtime（实时）/resource（资源）/activity（活动）分区管理玩家数据，各自独立 `KeyDef`。
2. **后台引擎独立线程**：`ProfileEngine` 以 QThread + 60s tick 运行周期重置/实时回复/阈值提醒，与 UI 线程解耦。
3. **SessionManager 原子写入 + 跨进程文件锁**：解决多写入方并发下的数据丢失/fd 泄漏问题。
4. **数据持久化迁移到 SQLite**：替代原先纯 JSON/session 文件存储。
