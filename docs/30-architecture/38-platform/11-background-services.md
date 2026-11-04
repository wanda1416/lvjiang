# 公共服务与桌面适配

- `core.update.fetch_latest_release` 提供更新结果与错误，Qt 检查线程位于 `ui/background/update.py`。
- `core.config.remote.run_sync` 提供同步服务，Qt 包装位于 `ui/background/remote_config_sync.py`。
- `core.telemetry.reporter.run_report` 返回上报结果，Qt 包装位于 `ui/background/telemetry.py`，
  桌面仍在主线程构造 job 并应用记账结果。
- `core.profile.engine.ProfileProcessor.tick` 保留周期处理、同步脚本与提醒持久化；
  `ui/profile/engine.py` 管理桌面线程、信号和生命周期。key 重命名通过无 GUI 更新通知
  发布已提交数据的变化，Qt 订阅在线程停止时解除。

业务处理不依赖 Qt；线程、信号与关闭等待由桌面层承担。
插件注册及配置策略装配见[插件系统](../30-overview/02-plugin-system.md)。

## 运行时线程全景

桌面客户端登录运行期间的全部执行单元。设计原则：**节拍用主线程 QTimer、网络在
临时 QThread**——空闲时除两个常驻业务线程外零线程开销。

### 常驻线程（主窗口启动即存在，退出时停止）

| 线程 | 类型 | 职责 |
|------|------|------|
| 主线程 | Qt 事件循环 | 全部 UI；所有 QTimer 节拍都在这里 |
| ProfileEngine | QThread（`ui/profile/engine.py`） | 总览引擎 tick：quota 周期重置、regen 边界物化、阈值告警；`data_updated` 信号驱动 UI |
| ProfileScriptRunner | 守护 Thread（`core/profile/triggers.py`） | profile 变更触发脚本串行队列，与写事务解耦 |

### 主线程节拍（QTimer，无线程）

| Timer | 间隔 | 职责 |
|-------|------|------|
| 账户同步节拍（`AccountHolder._sync_timer`） | 60s | 自动派发一轮完整同步（四通道串行，见下） |
| 账户心跳节拍（`AccountHolder._heartbeat_timer`） | 10min | 在线上报 + 会话健康探测；设备在线判定窗口 20 分钟 |
| 各页面私有 QTimer | 防抖级 | 参数保存防抖、装备卡刷新等，随页面存活 |

### 启动期一次性线程（跑完自灭）

公告检查、更新检查、在线配置同步、统计上报：启动时各起一个 QThread
（`ui/main/startup_ops.py`），完成后自删；公告/更新可手动再触发。

### 按需临时线程（用完即毁）

- `_AccountWorker`（`ui/account_dialog.py`）：登录/恢复/改密/同步/心跳各起一个，
  同类互斥（operation 与 sync 各一把锁，心跳独立），由应用级 `AccountHolder` 持有——
  **绝不做对话框子对象**，关窗不杀线程。
- `WorkflowWorker`（运行工作流）、`BatchWorker`（批量执行）、
  `DesktopCapture`/`raw_input` 后台线程（PC 截屏与后台输入）、
  scrcpy 解码与 adb 系列线程（手机对话框/设备任务）、录屏、外部 Agent 线程。

### 同步的执行模型

「四通道」（定义 → 名册 → 文档 → 总览）是**单个 `_AccountWorker` 线程内的串行
分段**，不是并发线程；通道只是分步进度（`sync_progress` 信号）的展示分段。
汇总由 `_CombinedReport` 在一轮结束后统一渲染与记日志（零变更降 debug）。

### Android 差异

主线程为 Kotlin UI + Chaquopy Python；常驻差异：`account-heartbeat` 守护线程
（前台期间每分钟同步、每第 10 拍心跳，`onPause` 停止）、任务/批量运行线程；
无 ProfileEngine 与 ScriptRunner（手机不做 regen 计算与触发器）。
