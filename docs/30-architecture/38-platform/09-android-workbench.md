# Android 工作台架构

状态：功能已实现，定向回归和构建已通过，隔离 UI 与真机游戏验收分别记录。目标需求见 [独立任务工作台](../../20-requirements/50-platform/15-android-workbench.md)。

## 责任划分

MainActivity 保留权限/诊断和自检兼容入口，增加四页导航；工作台控制器负责页面展示，
任务参数继续使用 TaskSettingsActivity，资料编辑继续使用 UserSettingsActivity。
Kotlin 不解释 require、同步规则或 Profile 数值算法。PyBridge 暴露只读视图和明确命令，
Python ondevice 层从公共任务发现、参数和 Profile 服务构造展示数据。

界面采用 Material 3 DayNight 主题；四个一级入口使用原生 BottomNavigationView 的
图标、标签和选中指示器，二级页面保留设置入口选中并提供返回操作。键盘弹出时隐藏
导航栏，为输入保留空间；关闭键盘后恢复。卡片、搜索和主次按钮采用 Material 组件，
颜色按 surface/onSurface 等角色随深浅色切换；设置入口使用列表行。
工作台把运行快照、活动用户选择和任务准备入口分别展示。活动用户由既有 select_user
命令写入 Session；运行中的用户名仍来自启动快照。工作台选择用户成功后，同步任务页
的参数用户；点击选择任务也带入活动用户。浏览资料、切换参数用户仍不写活动选择。
自定义对话框统一使用 SettingsUi 的 dp 内边距和滚动容器，不以像素值模拟边距。
用户/任务选择入口下方的悬浮窗主按钮通过宿主回调复用 MainActivity 的原有启停操作，
状态仍读取 FloatService；不创建第二份运行标志。执行中禁用该入口，当前任务卡保留
暂停和结束；电脑控制期间提示在电脑端操作。

旧路径：打开悬浮窗 → 选择用户/任务 → 启动。
新增路径：应用任务页 → 选择执行用户/检查参数 → 准备悬浮任务 → 回游戏后显式启动。
旧路径继续可用；普通页面、FloatService、AgentServer 使用同一个 Python 运行状态。
准备仅预选任务，不能启动输入线程；所有真正启动仍经过现有服务准备和执行互斥门禁。

## 状态所有者

| 状态 | 所有者 | 生命周期 |
|---|---|---|
| 参数定义、用户覆盖 | 既有配置与参数服务 | 持久化；编辑器仅合并所拥有字段 |
| 编辑用户、参数草稿 | TaskSettingsActivity | 页面/重建；不改变活动用户 |
| 浏览用户、搜索与分组 | 工作台页面 | 页面/重建；不写运行配置 |
| 活动执行用户 | 既有 Session active.user | 仅明确选择或启动时变更 |
| 运行用户、任务与参数 | task_runner 的启动快照 | 一次任务，执行期间冻结 |
| 待确认请求 | _TaskState | 单次请求 ID，回复后消费，停止时释放 |
| 任务历史 | TaskHistoryRepository | 持久化，来源区分 PC 与本机独立运行 |

## 交互协议

get_status 增加 busy（包括线程收尾）、运行用户名和 interaction（id/kind/message）。confirm 请求保持任务占用，
普通 resume 拒绝绕过确认；respond_interaction(id, accepted) 校验 ID 后消费回复并唤醒。
任务线程接收布尔值；停止唤醒时优先抛 WorkflowAbort。刷新与重建只读请求。
工作台、悬浮窗共用请求语义，AgentServer 的旧 resume 也不能隐式确认。

## 历史与用户数据

复用 daily_history 的任务记录、日志归档和终态写入，不创建第二份历史 schema。
ondevice 适配器负责筛选本机来源、结果摘要与有界日志预览，以及启动时恢复中断状态。
历史记录引用任务启动时用户；结束后修改活动用户不影响旧记录。
最终参数由引擎解析后更新到 running 记录；终态使用既有 completed/failed/stopped。
线程收尾仍占用执行槽，避免结束画面已出现但日志尚未归档时启动下一轮。
记录列表限定 source=ondevice、最近 100 条；日志预览最多 32KB，结果文件限制展示规模。
Profile 视图调用共享读取管线和仓储，按配置定义形成卡片；读取不初始化业务数值，
历史按用户/模型/key 分页。展示时使用 label，桥接和路由使用稳定 key。

## 公开内容与异步页面

公告与二维码继续由 core.announcement/core.group_qrcode 校验、获取和缓存，
桌面 QThread 适配器分别移到 ui.notices.announcement_checker/qrcode_refresher；
全部桌面调用点随迁移调整。core.version_info 提供不依赖 Qt 的版本解析和网络提示，
core.update 保持原版本函数导出。公开发布地址由 core.public_links 共同维护。
手机只在手动操作时联网，失败保留上次可用内容，展示错误原因。

WorkbenchUi 用页面代次丢弃切页/销毁后的异步结果；实时状态只刷新运行区域，
不会重建搜索框或用户列表。停止/暂停/确认使用独立控制执行器，不排在内容下载后面。
MainActivity 在重建时恢复导航、浏览用户、参数用户、搜索与分组，系统安全区覆盖键盘、
状态栏和导航栏；不把页面选择写成持久化活动用户。

## 并行工作与验证

不修改当前并行的配置加载优化及 Android 预置包构建逻辑，不提升发布版本。
协议测试使用临时配置和数据库，重点保护确认回复、停止竞争、历史用户归属及只读浏览。
Kotlin 编译和 UI 验证单独执行；构建成功不代表真实游戏任务或内存边界已验收。
