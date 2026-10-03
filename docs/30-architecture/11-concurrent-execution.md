# 多目标并发执行架构

> 状态：目标架构已确认，尚未实现。
>
> 产品语义见
> [多执行目标并发任务](../20-requirements/21-concurrent-target-execution.md)。本文只描述
> 运行时所有权、状态边界和依赖方向，不把规划能力写成当前已可用功能。

## 1. 当前结构与根因

0.13.10 的连接层已经允许一个 Windows 窗口和多台 Android 设备共存，
`ExecutionTargetRegistry` 也能保存多个目标。但运行层仍由主窗口持有一组全局字段：

```text
_current_worker
_current_engine
_run_state
_stop_requested
_pause_event
_ui_helper
_running_target_id
_running_target_snapshot
```

因此现有“执行目标”只是下一次任务的选择器，并不是并发调度单元。调律进度也通过
`reconnect(hub)` 把唯一控件切到最新任务，实时日志则把所有 loguru 文本直接写进同一个
文本框。并发不能通过放宽 `_running` 判断实现，否则不同任务会覆盖停止状态、引擎引用、
交互对话框、自动保存目标和调律 Hub。

## 2. 身份模型

### 2.1 两个公开身份

架构只使用两个公开身份：

```text
target_id   = 执行端身份
task_run_id = 任务运行身份
```

不建立领域级 `connection_id`。重连只替换目标当前的资源绑定，不改变执行端身份；一次任务
无论经历多少次断连和重连，都只有一个 `task_run_id`。

### 2.2 target_id 稳定性

- Windows 逻辑目标固定为 `window`。
- Android 的 `target_id` 必须代表逻辑设备，传输 serial、USB/无线连接方式和地址属于可变
  元数据。
- 当前 `android:<serial>` 只有在 serial 稳定时才能直接沿用，不能把无线 ADB 的
  `IP:端口` 直接用作目标身份。发现层应提供 `DeviceIdentity`：优先使用设备端稳定标识或
  USB/无线均一致的设备属性，并把各 transport 映射到该身份。
- 同一物理设备经 USB 和无线同时出现时必须归并为一个 TargetRuntime；稳定标识缺失或冲突
  时进入待确认状态，由用户选择已有目标进行重绑定，不允许两个候选同时进入可执行状态。
- hwnd、窗口矩形、设备 serial 和 Agent 端口不得进入目标主键。
- `target_label` 与主键分离。默认名称使用型号加稳定短标识，允许用户设置别名；历史只保存
  启动时名称快照，不能让两台同型号设备显示成无法区分的同名目标。

## 3. 分层与所有权

```text
ExecutionTargetRegistry             连接资源层
  └─ TargetRuntime(target_id)
       ├─ 状态与展示信息
       ├─ ResourceBinding
       └─ TargetHandle

ExecutionRunManager                 运行调度层
  ├─ runs[task_run_id] -> ExecutionRunContext
  └─ occupied[target_id] -> task_run_id

ExecutionRunContext                 单次任务所有者
  ├─ task_run_id / target_id
  ├─ 业务配置快照
  ├─ engine / worker
  ├─ stop / pause / target-ready gates
  ├─ history / log context
  └─ optional tuning progress hub

MainWindow / plugin pages           展示与命令层
  ├─ selected_target_id
  ├─ per-target launch drafts
  └─ 订阅 RunManager 事件
```

依赖只能向下：页面向 RunManager 发命令；RunManager 占用 TargetRuntime；工作流通过自己的
RunContext 使用目标句柄。连接注册表不得反向读取当前页面，工作流也不得读取主窗口的
“当前目标”来决定自己操作哪个执行端。

## 4. TargetRuntime 与资源重绑定

### 4.1 稳定句柄

任务不再永久持有启动时 capture/input 的裸引用，而是持有稳定的 `TargetHandle`。句柄内部
指向当前 `ResourceBinding`：

```text
ResourceBinding
  generation
  capture
  input_ctrl
  device / agent
  window geometry
  resume primitive
```

业务配置仍按启动时冻结；只有被明确声明为可恢复的 I/O 资源允许动态重绑定。这是
“运行快照”原则的窄例外，不能借此让用户、布局、参数或活动配置在运行中漂移。

工作流引擎可以通过稳定代理访问 capture/input；代理在每次动作边界解析当前 binding。
已经进入某个后端调用的动作不强行热替换，失败后进入目标等待，再从工作流既有重试或下一
动作边界继续。

Windows 的窗口几何属于 ResourceBinding，而不是冻结的业务配置。坐标代理在每个输入动作
开始时读取一次当前几何并完成坐标换算；动作执行期间使用同一份几何。刷新位置或重新定位
只影响后续动作，不改变布局、参数或任务身份。

### 4.2 generation

每次资源绑定或重绑定递增内部 `generation`。截图回调、Agent 断线、流式帧和后台连接结果
必须携带启动时 generation；回调抵达时若不是当前代次则丢弃。

generation：

- 只用于防止旧连接的延迟事件污染新绑定；
- 不展示给用户；
- 不写任务历史；
- 不生成新的目标或任务；
- 不是 `connection_id` 的替代业务概念。

### 4.3 正交运行门

运行上下文至少维护两个独立许可条件：

1. **operator gate**：用户暂停/恢复；
2. **target-ready gate**：目标连接是否可执行。

任务只有在两者同时开放时继续。目标重连只打开 target-ready gate，不得自动撤销用户暂停。
停止请求独立于两者，并会唤醒所有等待，使线程有机会安全退出。

等待目标恢复仍属于活动 RunContext：目标占用、用户执行锁和 Lv1 并发计数均不释放。只有
任务安全结束后才能释放这些资源。

### 4.4 输入资源域

目标互斥之外还要尊重输入后端的物理资源域：

```text
SendInput             -> desktop_foreground_input（进程内全局独占）
PostMessage(hwnd)      -> window:<hwnd>（理论上按窗口隔离）
ADB / Agent(device)    -> android:<target_id>
```

SendInput 注入系统前台键鼠，无法保证两个窗口并发时不串输入。当前产品只登记一个 Windows
逻辑目标，因此不为多窗口实现额外资源调度；它可以与 Android 目标并发。若未来开放多个
Windows 目标，RunManager 必须同时占用 target_id 与 input resource key，且只有经实机验证
可靠的 PostMessage 后端才能按 hwnd 并发，不能靠反复激活窗口模拟隔离。

## 5. ExecutionRunManager

RunManager 是所有任务入口的唯一并发裁决者，职责包括：

- 为每次启动创建 `task_run_id` 和 `ExecutionRunContext`；
- 原子检查并占用 `target_id`；
- 执行统一启动判定，并按运行类型协调用户执行锁；
- 在第二个及后续并发任务启动时执行 Lv1 门禁；
- 发布创建、状态变化、完成和销毁事件；
- 任务收尾后释放目标占用，但保留历史和 UI 所需的不可变结果摘要；
- 应用退出时停止并等待全部上下文。

建议状态机：

```text
starting -> running -> pausing -> paused -> running
                    -> waiting_target -> running/paused
                    -> stopping -> completed/interrupted/failed
```

`paused` 与 `waiting_target` 在内部可以由正交 gate 表达，展示层按原因组合出准确文案，避免
用单个枚举覆盖用户暂停状态。

目标占用必须覆盖完整生命周期：启动准备、用户 Session 加载、工作流执行、自动保存、历史
收尾和资源释放。不能在线程刚返回结果时就提前释放目标。

RunManager 对 UI 和所有启动入口提供同一份结构化判定，而不是让调用方拼布尔条件：

```text
can_start(request) -> StartDecision
  allowed
  denial_code
  reason
  conflicting_task_run_id / target_id

concurrency_summary() -> 运行数量与各目标占用摘要
```

单任务在创建 RunContext 前取得用户锁，冲突即拒绝。批量任务的目标占用由 RunManager 管理，
但用户锁仍按批次条目取得；冲突条目沿用跳过并继续的语义，并记录占用目标和运行 ID。属性
单元继续在进入准备工作流前锁定全部成员。Profile 触发器等不使用物理目标的纯数据执行器
不纳入该用户执行规则。

## 6. 授权门禁

RunManager 的启动规则是能力真源：

```text
目标已占用                         -> 永远拒绝
没有其他运行实例                   -> 允许
已有其他运行实例且 has_feature(lv1) -> 允许
已有其他运行实例且无 lv1           -> 拒绝并返回可展示原因
```

UI 根据同一查询结果展示按钮状态和原因，但不能自行复制判定。授权在启动时读取，已经创建的
RunContext 不因授权缓存刷新而被终止。
授权判定在 RunManager 所属控制线程完成，不要求工作线程并发刷新进程级授权缓存。

## 7. UI 状态投影

主窗口保存 `selected_target_id`，它只表示观察和命令对象。连接注册表的活动目标概念应逐步
收缩为 UI 选择，不再承担“全局运行目标”含义。

每个目标拥有 `LaunchDraft`：用户、任务、环境、布局、方案和参数。草稿属于连接会话内编辑
状态；持久化配置仍提供初始值，运行快照由草稿显式生成。程序化切换目标时必须阻断会写
配置的控件信号。

运行目标的执行区显示冻结快照并保持只读；空闲目标才显示可编辑草稿。初始版本不在同一组
控件中编辑“下次运行”，避免编辑态和当前运行态再次混为一体。

RunManager 向 UI 投影：

- 每目标是否占用、任务名称、用户和运行状态；
- 当前目标可执行的 start/pause/resume/stop 命令；
- 全局运行数量与退出阻塞状态。

目标列表不能因为任一任务运行就整体禁用。只对当前目标会破坏运行的重新绑定、主动断开等
操作设置限制；扫描和连接其他目标继续可用。

## 8. 日志路由

引入结构化 `RunLogEvent`，至少包含：

```text
task_run_id | target_id | target_label | level | timestamp | message
```

每个 RunContext 创建显式 `RunLogger`，框架、引擎和可控的派生任务均通过它写入
`extra.task_run_id/target_id`。QThread 入口可以设置当前运行上下文，但不得假定 loguru 的
contextvars 会自动传播到新线程；新线程必须显式接收 RunLogger 或使用带运行信封的包装器。
任务独立日志 sink 从“线程 ID 相等”迁移为“`extra.task_run_id` 相等”，否则同一任务的
辅助线程会丢日志，而线程复用也可能产生错误归属。

UI 日志模型存事件，不存已经拼好前缀的文本：

- 全部视角按时间排序并渲染目标/任务前缀；
- 当前目标视角按 `target_id` 过滤；
- 当前任务视角按 `task_run_id` 过滤；
- 无运行上下文的系统日志只进入全部视角。

日志级别切换只重绘当前过滤结果，不重新解析正文。

## 9. 历史持久化

`daily_history.db.task_runs` 增加目标快照列：`target_id`、`target_kind`、`target_label`，以及
确有查询价值的环境/布局字段。`batch_runs` 记录批次绑定目标，批次子任务继续各自记录。

这是持久数据 Schema 变化，必须使用显式、幂等的数据库版本迁移；旧记录目标字段为空并
展示“未记录”，不能依据日志或参数启发式回填。

`daily_history.db` 当前没有版本迁移框架，因此新增列以前先建立 `schema_version` 和按版本
顺序执行的幂等迁移；不能依赖 `CREATE TABLE IF NOT EXISTS` 修改已有数据库。调律历史已有
迁移机制，继续沿用其独立版本序列。

调律历史运行表同步增加目标快照列。两个历史库只共享字段语义，不相互引用，也不把
`task_run_id` 强行改成调律领域 `run_id`；自动调律启动时显式保存二者关联即可。

不持久化 generation、capture/input 类型对象、hwnd 或当前 transport 地址。

## 10. 调律管理多页模型

`TuningManagementWidget` 从单一 `progress_widget + reconnect(hub)` 改为任务页注册表：

```text
pages[task_run_id] -> TuningRunPage
```

每个页面创建时绑定自己的 `TuningProgressHub` 和 `TuningResultStore`，之后不切换 Hub。
历史页固定为索引 0；运行页根据创建顺序追加。完成事件只更新本页状态并通知历史页刷新，
不清空、复用或删除其他页面。

页面标题是目标名称、用户和状态的投影，页面身份始终是 `task_run_id`。目标断线或从连接
列表移除后，标题使用启动时快照，页面仍可查看。应用重启后不恢复瞬态页面，持久结果由
调律历史负责。

活动页不允许关闭；完成、失败或中断页提供手动关闭操作。关闭时断开页面信号并销毁该页，
不删除调律历史，从而避免长会话无限累积已完成控件。

## 11. 工作流、Session 与共享服务

每个 RunContext 创建独立 WorkflowEngine。以下当前单例运行字段必须迁移进上下文或改为
显式参数：engine、worker、stop/pause、UI helper、执行用户、自动保存目标和调律 Hub。

- OCR 若实现不保证并发安全，则每个 RunContext 使用独立实例；不能默认共享主窗口 `_ocr`。
- Layout、ReferenceDatabase 等共享只读配置可复用不可变快照，运行中不读取活动 UI 状态。
- SessionManager 继续使用增量 CAS；用户执行锁阻止同一用户任务并发，跨用户保存按现有
  文件锁和数据库事务处理。
- 每个 RunContext 持有独立 UI helper 和待处理请求集合。请求携带
  `task_run_id/target_id`，UI 可以统一排队展示，但队列项仍归原 RunContext；一个任务结束
  只能关闭并完成自己的请求。停止、退出和交互取消都必须 set 对应等待事件，不能留下无限
  等待的工作流线程。
- capture/input 的物理所有者是 TargetRuntime，不是主窗口当前选择，也不是插件页面。

## 12. 批量任务

批量运行整体占用一个目标，并拥有一个顶层 RunContext。其 prepare、task、finish 和恢复
工作流全部复用同一 TargetHandle 与运行快照。批次内部仍按原调度顺序串行，不因多目标
能力自动拆散到多个设备。

用户可以在不同目标分别启动不同批次，但每个批次都经过目标占用和 Lv1 判定；用户执行锁
仍按条目获取。遇到已被其他目标运行实例占用的用户时继续跳过该条目，但进度、报告和日志
必须写明占用目标和运行实例，不能只显示通用 `ST_SKIPPED`。
批量页面的进度模型最终也应按 `task_run_id` 归属，不能继续写主窗口唯一 worker。

## 13. 断线和目标生命周期

- 非运行目标掉线：更新该目标状态，不影响任何任务。
- 运行目标掉线：关闭 target-ready gate，任务进入等待恢复；其他任务继续。
- 等待恢复期间：RunContext 继续占用目标、用户锁和并发名额；停止动作会唤醒等待并收尾。
- 同目标重连：原子替换 ResourceBinding、递增 generation、打开 target-ready gate。
- Windows 刷新位置：更新同一 binding 的几何状态，不更换 `target_id/task_run_id`。
- 主动断开运行目标：由 UI 阻止，或先显式停止任务；不能直接删除 TargetRuntime。
- 任务结束后断开目标：可以释放连接资源；已经完成的任务页和历史快照不依赖目标存活。
- 应用退出：先禁止新启动，再并行请求所有 RunContext 停止并完成各自交互等待；所有
  QThread 结束后才能释放 TargetRuntime。协作停止超时时列出具体未结束任务，保持事件循环，
  由用户选择继续等待或明确强制退出；不能静默销毁仍运行的 QThread。

## 14. 兼容迁移策略

改造期间允许主窗口保留“当前选中目标”的兼容投影，但有严格边界：

- 兼容字段只服务尚未迁移的空闲 UI，不得被运行线程读取；
- 运行中的 capture/input/engine 必须来自 RunContext；
- 已迁移入口不得同时写新上下文和旧全局字段；
- 未迁移入口继续执行单任务门禁，并明确记录原因；
- 完成迁移后删除 `_current_worker/_current_engine` 等全局运行兼容字段，不能永久双轨。

删除兼容字段以前必须完成调用点清单并逐项迁移，至少包括：运行按钮和热键控制、调律 Tab、
菜单工具、完成回调的输出用户名与结果路径、Session 自动保存、专用任务启动，以及应用退出
对 worker 的等待。任何完成回调都不得再通过主窗口 `_current_engine` 反查本次任务所有者。

## 15. 全量影响面

并发改造不是 `run_control` 内部重构。下表是实施前必须逐项迁移或确认不受影响的完整边界；
任何一项继续读取“全局当前任务”，都可能造成跨目标误停、状态覆盖或结果写错用户。

| 影响面 | 当前单例假设 | 目标架构 |
|--------|--------------|----------|
| 连接注册表 | `active_target_id` 同时被当作预览与运行目标 | 仅表示 UI 选中目标；运行归属由 RunContext 冻结 |
| 连接信息注册 | `_active_connection_platform/_connected_apps` 维护第二套活动目标 | 并入或派生自 TargetRuntime，不再独立决定运行归属 |
| 工作流启动/结束 | `_begin_automation/_end_automation` 改一组全局字段 | RunManager 创建、收尾并释放指定 RunContext |
| Worker/Engine | `_current_worker/_current_engine` 唯一 | 每个 `task_run_id` 独立持有 |
| 停止/暂停 | `_stop_requested/_pause_event/_run_state` 唯一 | 命令显式指定 target/run，状态归 RunContext |
| 启动与结束守卫 | `guarded_launch/guarded_finish` 按全局 running 判定 | 改为目标占用、用户锁、授权与上下文生命周期守卫 |
| 业务插件宿主 API | `is_running/request_stop/request_pause_resume` 无目标参数 | 提供按目标/运行查询和命令；另保留明确的聚合查询 |
| 宿主状态信号 | `automation_state_changed(str)` 只表达一个状态 | 事件携带 `task_run_id/target_id/state`，另发全局摘要 |
| AppEvent | 部分工作流事件只有业务 payload | 工作流发往 UI 的事件补运行信封，接收者按 run 路由 |
| 用户与参数选择器 | 全局控件既是草稿又是运行信息 | 每目标 LaunchDraft，启动后生成不可变快照 |
| 方案/环境/布局/图库 | 从当前控件或活动值读取 | 启动前解析并冻结；执行中不跟随 UI 切换 |
| OCR | 主窗口 `_ocr` 共享 | 证明线程安全后共享，否则每 RunContext 独立实例 |
| capture/input | 主窗口 `_capture/_input/_backend` 投影 | 仅供选中目标 UI；任务只经 TargetHandle 使用 |
| Android forward | scrcpy 固定本地端口并清理全部 forward | 每实例动态端口，只删除自身规则，不影响 Agent 或其他设备 |
| ADB 断线提示 | 全局 `_adb_banner` 和恢复按钮 | 目标行状态 + 当前目标提示；恢复命令携带 target_id |
| UI 交互 helper | `_ui_helper` 唯一 | 每 RunContext 独立，关闭任务只关闭自己的交互 |
| 主运行按钮 | 全局开始/停止三态 | 当前选中目标的 start/pause/resume/stop 投影 |
| 热键 | 直接控制全局状态 | 控制当前选中目标，并在 UI 明示目标 |
| 托盘 | 单个 `automation_state_changed` 决定图标 | 汇总运行数量/最严重状态，菜单不得误停任意任务 |
| 状态栏 | 只显示唯一任务 | 显示当前目标状态，同时保留全局运行数量 |
| 窗口标定框 | 颜色跟随全局运行 | 只反映唯一 Windows 目标自身任务状态 |
| 预览 | 跟随 `active_target_id` | 继续跟随 UI 选中目标，不随后台任务抢占 |
| 截屏工具 | 使用主窗口当前 capture | 明确读取选中目标；不借用任一任务 RunContext |
| 屏幕录制 | `_screen_recorder` + 当前活动目标 | 开始时固定 target_id；切换观察目标不改录制来源 |
| 输出 JSON | 从 `_current_engine.run_username` 推导 | 从完成回调携带的 RunContext 推导用户和路径 |
| Session 自动保存 | 保存 `_current_engine` 的 Session | 每个 RunContext 保存自己的用户名和 Session 快照 |
| 独立任务日志 | 按线程 ID 过滤 | 按 task_run_id 过滤，覆盖任务派生线程 |
| 任务历史 | 无执行端字段 | 记录目标快照并支持并发短事务 |
| 批量历史/报告 | 主窗口只保留一批 | 每个批次绑定独立 RunContext 和 target_id |
| 调律实时管理 | 最新 Hub 覆盖唯一当前页 | pages[task_run_id]，历史页固定 |
| 调律历史/遥测 | 不记录执行端 | 保存目标快照；上报白名单单独评审是否包含类型 |
| 批量 Tab | `host.is_running` 和唯一进度模型 | 按自身 run 查询与控制，不停止其他目标任务 |
| 调律 Tab | 读取 `_current_engine`、全局状态信号 | 按启动返回的 task_run_id 绑定 Hub 和控制命令 |
| 菜单工具 | 个别入口读取 `_current_engine` | 要么绑定当前目标 RunContext，要么明确属于独立工具 |
| 应用关闭 | 等待一个 `_current_worker` | 禁止新任务，停止并等待全部 RunContext |
| 崩溃残留 | 最多一条 running 历史 | 各 run 独立保留 running，启动后按既有规则识别异常退出 |
| Lv1 授权 | 当前没有能力消费者 | UI 提示 + RunManager 强制门禁，共用 `lv1` 等级 |

### 15.1 插件宿主 API

主窗口对插件暴露的接口不能再用无参“当前任务”语义。目标接口至少要表达：

```text
is_any_running()
run_for_target(target_id)
request_stop(task_run_id | target_id)
request_pause_resume(task_run_id | target_id)
run_workflow_implementation(..., target_id) -> task_run_id
```

是否保留旧 `is_running` 属性取决于迁移期需要；若保留，只能表示“是否存在任意运行实例”，
不得被插件用来决定自己按钮控制哪一个任务。调律和批量页面必须保存自己启动得到的
`task_run_id`，不能在点击停止时重新查询主窗口当前选择。

`automation_state_changed` 应拆成运行实例事件与全局摘要事件。前者驱动具体页面，后者只
服务托盘、退出提示和“当前有 N 个任务”这类汇总展示。

### 15.2 预览、截屏与录屏

预览和工具采集属于观察面，不属于运行实例：

- 预览继续跟随 `selected_target_id`，后台任务不得抢走预览；
- 手动截屏读取当前选中目标的资源句柄；目标未就绪时只报该目标问题；
- 录屏启动时冻结录制 target_id，之后切换 UI 目标不改变来源；
- 录制目标重绑定截图后端时，沿用现有“先安全结束当前录制”的数据保护规则，不能把
  两次 binding 的帧静默拼成一个视频；
- scrcpy 帧以 target_id + generation 路由，非预览目标仍可供自己的任务取最新帧，但不
  更新主预览，也不能喂给另一个目标的录屏器。
- scrcpy 使用每实例独占的动态本地端口，保存自己创建的 forward，并在停止时精确删除；
  禁止使用 `forward --remove-all`，否则会破坏同设备 Agent 和其他采集会话。

### 15.3 状态栏、托盘、横幅与标定框

这些控件只有一个物理实例，因此只能展示聚合状态或当前目标状态：

- 状态栏主文案跟随当前目标，并附加“另有 N 个任务运行”；
- 托盘图标采用聚合状态，优先级建议为失败/等待处理、停止中、暂停、运行、空闲；
- 托盘停止动作若保留，必须打开任务选择或明确标为“停止全部”，不能无声停止当前 UI
  未展示的任务；
- ADB 断连横幅仅在当前目标需要恢复时显示，其他设备断连在目标行提示；
- Windows 标定框只对应唯一窗口目标，颜色取该目标任务状态，不取任意 Android 任务。

### 15.4 输出、Session 与文件归属

完成回调不能通过主窗口 `_current_engine` 查用户。RunContext 在创建时确定：

- 输出用户名与目录；
- 输出文件的 `task_run_id`；
- SessionSnapshot 及保存策略；
- TaskRunSession 与批量/调律运行关联。

现状单任务结果只有任务名与微秒时间，批量报告只有秒级时间；并发后不能沿用。单任务结果
文件名必须包含 `task_run_id`，批量报告必须包含 `batch_run_id`，时间只用于可读性。用户执行
锁阻止同一用户的两个物理任务并发，但 profile 触发器、UI 编辑和外部实例仍可能同时写数据，
因此 Session CAS、装备文件锁和 SQLite 短事务不能因进程内目标互斥而移除。

`daily_history.db`、调律历史库和 profile 数据库均要核对 WAL、busy timeout 与事务长度；
工作线程不能持有数据库连接跨 OCR、等待用户或网络调用。

### 15.5 非目标型执行器

以下后台工作不占用游戏执行目标，也不计入 Lv1 并发数量：

- profile 变动脚本队列；
- 纯数据计算与毕业率后台分析；
- 更新、公告、遥测补传；
- 不使用游戏 capture/input 的配置校验。

场景编辑器和脚本工作台的试运行如果实际使用游戏 capture/input，则必须在后续接入目标
占用；在接入前继续保持现有限制，不能被误归类为“工具任务”而与正式任务争抢目标。
手机端独立执行的工作流属于另一个进程，不进入桌面 RunManager；它与桌面控制同一设备时
的互斥需要设备端协议另行提供，不能由本机 `target_id` 锁假装解决。

### 15.6 资源与性能

并发数量默认由已连接目标数自然限制，不另设固定线程上限。实现仍需保证：

- OCR 模型、模板库等大对象若可安全共享，只共享不可变数据和线程安全推理入口；
- 每目标流式截图保持自己的帧缓存，不能复制整段视频帧到每个 RunContext；
- UI 高频进度和日志事件需要按任务限频/批量刷新，避免多个设备把 Qt 事件队列淹没；
- 历史与日志落盘失败只影响对应任务的可观测性，不得拖垮其他运行实例；
- 一个任务 CPU 密集型计算不能在主线程执行，也不能阻塞其他 RunContext 的停止信号。

## 16. 实施依赖顺序

为避免新旧状态双写，建议按以下依赖顺序提交，而不是按页面逐个打补丁：

1. 建立 Android 稳定身份归并、scrcpy 动态端口、`daily_history` 迁移框架和 QThread 安全
   退出协议；
2. 建立 TargetRuntime/TargetHandle 与 RunContext/RunManager，不开放并发；
3. 把通用、专用和批量启动/收尾迁到 RunManager，删除运行线程对主窗口单例字段的读取；
4. 改造宿主 API、运行事件、输出、Session、独立日志和 UI 交互所有权；
5. 改造主页面目标草稿、控制按钮、快捷键、托盘与断线提示；
6. 改造任务历史和调律历史 Schema；
7. 改造调律管理、调律 Tab 和批量 Tab 的多运行投影；
8. 完成资源重绑定、批量锁冲突展示与断线恢复；
9. 最后接入 Lv1 并开放第二个并发任务。

在第 9 步以前即使内部已经支持多个 RunContext，产品入口仍保持单任务门禁，防止半迁移
状态进入用户环境。

## 17. 测试边界

架构测试至少覆盖：

- RunManager 对目标互斥、用户锁和 Lv1 的组合判定；
- USB 与无线发现同一 Android 设备时归并为同一 target_id，身份冲突不会产生两个可执行目标；
- 两个 scrcpy 会话使用不同本地端口，停止其中一个不删除另一个或 Agent 的 forward；
- 两个目标的暂停、停止、完成和异常互不影响；
- 断线重绑保持 `target_id/task_run_id`，旧 generation 回调被丢弃；
- 用户暂停后重连不会自动恢复；
- 切换 UI 目标不改变任一运行快照或持久化默认值；
- 日志事件、独立日志、输出和历史均按 task_run_id 归属；
- 两个调律 Hub 不串页，完成一个不会重置另一个；
- 同一用户跨目标并发仍被拒绝，不同用户 Session 保存互不覆盖；
- 批量任务遇到另一目标占用的用户时跳过并记录精确原因，后续条目继续执行；
- 两个 RunContext 同时等待交互时互不覆盖，停止任一任务都会释放自己的等待事件；
- 单任务结果和批量报告以运行 ID 区分，同一时刻完成也不会覆盖；
- 应用关闭会等待所有 worker，不出现 `QThread: Destroyed while thread is still running`。

交互测试继续遵守测试环境禁止真实模态窗口的门禁，使用 fake interaction port、非模态替身
或直接驱动请求状态机；不能为了并发用例放开全局弹窗限制。

还要增加跨 UI 子系统的集成用例：

- 调律 Tab A 停止自己的任务时不影响批量 Tab B；
- 当前预览切到设备 B 后，设备 A 的任务仍使用 A，A 上已经开始的录屏来源不漂移；
- 非当前设备断线不覆盖当前目标状态栏或 ADB 横幅；
- 托盘与全局摘要能正确表示一运行、一暂停和一等待重连的组合；
- 任务 A 完成触发 Session 保存和历史刷新时，不清空任务 B 的 engine、日志或调律页；
- 未激活用户在已有 profile 数据脚本运行时仍能启动第一个物理目标任务，说明授权计数没有
  错把非目标执行器算进去。
