# 设备端代理协议（PC ↔ 律匠 app）

> Layer owner：L7 Contract（PC 与设备 app 之间的线协议）
> Feeds/Affects：L8 `src/lvjiang/core/android/agent.py`、`android/.../AgentServer.kt`；L6 ADB 模式的截图/输入后端选择
> Stability：稳定（协议版本 3；两端实现必须同步改）

## 为什么要有它

PC 端 ADB 模式原先只有两条路控制手机：`adb shell input tap/swipe` 与 `adb exec-out screencap -p`。
它们的硬伤：

| 旧通道 | 问题 |
|---|---|
| `input tap/swipe` | 发完就返回，手势未落地就截图会截到旧画面；`swipe` 表达不了"推到位停住"（推摇杆） |
| `screencap -p` | 每帧起一次 adb 子进程 + PNG 编解码，300–800 ms/帧 |

手机上的律匠 app 已经有无障碍服务（`takeScreenshot` / `dispatchGesture` / `performGlobalAction`）
和 Shizuku shell 两条通道（设备端独立执行用）。代理协议把这两条通道借给 PC 端：PC 连上 app，
截图与手势由 app 在设备上落地。**无障碍是主通道**（开一次开关长期有效），Shizuku 是可选通道。

## 传输

- 设备端：`AgentServer`（Kotlin `object`）监听 abstract 命名空间的 `LocalServerSocket("lvjiang-agent")`。
  随 `App.onCreate` 启动，生命周期跟进程走；无障碍开关开着时系统常驻绑定进程，
  所以「开了无障碍 = 代理在线」，用户不需要额外操作。
- PC 端：`adb forward tcp:<27300–27399 随机> localabstract:lvjiang-agent`，TCP 连 `127.0.0.1:<port>`。
- 安全：服务端校验对端 uid，只接受 adbd（shell 2000 / root 0）与本进程；其它 app 的连接直接断开。
- 一个连接上请求串行，一问一答；服务端对所有 op 加全局锁，跨连接也串行。
- PC 侧握手不仅校验协议，还要求无障碍或 Shizuku 至少一条输入通道可用；App 在线但输入
  通道均未就绪时拒绝代理并回退 ADB。传输失败（断线/超时）就地重连一次，再失败抛
  `AgentTransportError`；设备端返回 `ok=false` 也向工作流传播，不能把未执行的手势当成功。
- App 主页每秒刷新“辅助 / PC 连接 / 最近指令”状态。普通连接和同步不隐藏悬浮球；
  PC 工作流取得 `control_begin` 租约时隐藏，`control_end` 或持有者断线时恢复。

## 帧格式

```
请求：  [4 字节大端长度][UTF-8 JSON {"op": "...", ...}]
响应：  [4 字节大端长度][UTF-8 JSON 头]([二进制负载，长度 = 头.bin])
```

响应头公共字段：`ok: bool`；失败时 `error: str`，可重试的失败另带 `retryable: true`。
请求体上限 1 MiB；负载（整屏 RGBA 十几 MB）另计。

## 通道选择 `via`

每个手势/截图 op 可带 `via`：

| 值 | 含义 |
|---|---|
| `"auto"`（缺省） | 无障碍已连接走无障碍；否则 Shizuku 已授权走 shell；都没有 → `ok=false` |
| `"a11y"` | 强制无障碍，未连接报错 |
| `"shell"` | 强制 Shizuku `input` / `screencap`，未授权报错 |

响应头回带实际用的 `via`。

## op 一览

| op | 请求字段 | 响应 |
|---|---|---|
| `ping` / `status` | — | `protocol`、`app`（versionName）、`sdk`、`a11y`、`shizuku`（在跑）、`shizuku_granted`、`calib_identity`、`screen{w,h,rotation}`，以及仅供 App/诊断展示的 `pc_connected`、`pc_connections`、`pc_connected_since_ms`、`last_op`、`last_op_ok`、`last_op_at_ms` |
| `screenshot` | `via`、`timeout_ms`(5000) | a11y：`fmt:"rgba"`, `w`, `h` + RGBA 裸字节；shell：`fmt:"png"` + PNG。节流失败 `retryable:true` |
| `tap` | `x`,`y`,`duration_ms`(50) | — |
| `long_press` | `x`,`y`,`duration_ms`(800) | — |
| `swipe` | `x1`,`y1`,`x2`,`y2`,`duration_ms`(300) | — |
| `hold_move` | `x1`,`y1`,`x2`,`y2`,`move_ms`,`hold_ms` | a11y 两段 stroke 真正停住；shell 通道把 hold 合并进 swipe 时长（匀速插值，推满只在结尾生效） |
| `gesture` | `strokes:[{start_ms, move_ms, hold_ms, points:[[x,y],...]}]` | 多路触点一次并发下发（输入时间线）。**只走 a11y**：shell 的 `input` 单指、无法交错两个 pointer，强制 `via="shell"` 直接报错而不是拆成顺序注入。整块一个 `GestureDescription`，超出本机 `getMaxStrokeCount()` 或`getMaxGestureDuration()` 时返回 `ok=false` 并点明是哪条限制 |
| `key` | `name:"BACK"/"HOME"` 或 `keycode:int` | BACK/HOME 无障碍用 `performGlobalAction`；其它 keycode 只有 Shizuku 能发（`auto` 下自动转 shell，没 Shizuku 报错不静默降级） |
| `shell` | `cmd:[...]` | Shizuku 执行，stdout 作二进制负载 |
| `calib_get` | — | `key`（机型_WxH）、`screen{w,h,rotation}`、`calib{sx,ox,sy,oy}`、`identity`、`stored`、`overlay{w,h}\|null` |
| `calib_set` | `sx`(1),`ox`(0),`sy`(1),`oy`(0) | 保存当前朝向分辨率的屏幕映射；全恒等等价于删文件。返回同 `calib_get` |
| `calib_clear` | — | 删掉当前朝向分辨率的映射文件 |
| `calib_mark` | `x`,`y`,`tap`(false),`via` | 在 (x,y) **经映射后**的像素画准星（需悬浮窗权限，没有则 `ok=false`）；`tap=true` 同点再点一下。返回 `px{x,y}` + `calib_get` 字段 |
| `calib_hide` | — | 撤掉准星覆盖层 |
| `float_icon` | `hidden`(true) | 动态显隐悬浮球（截图/标定前藏起来，免得被截进画面）。返回 `running`（悬浮服务是否在跑）、`hidden` |

坐标都是设备截图坐标系的像素（与 `screencap` / 无障碍截图一致），PC 端不做旋转变换。

### 输入时间线的落地细节

`gesture` 的每条 stroke：起终点相同是按住不动；不同则是「用 `move_ms` 推到位，再保持
`hold_ms`」。

保持段的实现是**贴着终点沿同一轴的往复微动**（振幅几像素），不是真的静止。一条
`StrokeDescription` 只能按弧长匀速走完整条 path，时间正比于路程，所以"停住"只能用
与时长成比例的路程来换：微动路程取推进路程的 `hold/move` 倍，推进段就正好占
`move_ms`。`continueStroke` 的续接段必须另起一次 `dispatchGesture`（`hold_move` 就是
那么做的），放进同一个 `GestureDescription` 就不是并发了，所以这条路走不通。

**不能退化成"直接按在终点上"**：游戏的摇杆没有固定区域，第一个触点即中心、方向看之后
手势往哪动。零位移的触点只给了中心没给方向，游戏会拿下一个触点（比如跳跃键）去猜方向
——实测表现成"往前跳"，而脚本要的是后退。

真机上还有三件事要实测（框架层支持是明确的，这三条与设备/游戏有关）：本机
`getMaxStrokeCount()` 的实际值、游戏是否接受注入的多点触控、真实触摸取消整组手势
的表现。探针：

```bash
python -m lvjiang.core.android.gesture_probe [-s SERIAL] [--hold 2.0]
```

它发两路并发触点（左下角按住、期间右下角点两次），回报设备端是否完成。**回报成功
只说明系统接受了注入**，游戏是否响应要靠眼睛看。

### 屏幕映射（ScreenMap）

设备端在**手势注入口**（`A11yBridge` / `ShellBridge` 的 tap/swipe/longPress/holdMove）统一施加一层
逐轴仿射 `input% = shot% * s + o`，按机型 + 当前朝向分辨率存 `filesDir/lvjiang/calib/<型号_WxH>.json`，
无文件即恒等。绝大多数设备截图网格 == 触摸网格，恒等就对；挖孔处理、截图缩放、黑边不同的机器用它补。
PC 侧 `python -m lvjiang.core.android.calib -s <serial> probe [--apply]` 自动量：清映射 → 对角两点
`calib_mark` → 截图里按色相找准星（Android 12+ 把悬浮窗按 ~0.8 不透明度合成，准星颜色会变暗，
不能按精确 RGB 找）→ 拟合 → 写回 → 第三点验证。PC 端与设备端 Python 通道都经过同一注入口，
标定一次两边生效。

它只管"截图像素 → 触摸像素"。**换机后游戏内容区位置不同**（挖孔安全区 / 宽高比留黑）是另一层问题，
由布局画布解决：app 内「屏幕标定」页，见 `core/screen_calib.py` 与开发日志 2026-08-23。

## PC 端使用

```python
from lvjiang.core.android import AdbDevice, connect_agent, create_capture_backend, create_input_backend

device = AdbDevice(serial)
agent = connect_agent(device)            # 连不上返回 None（原因已记日志）
capture = create_capture_backend(device, "screencap")  # 截图选择与输入代理独立
inp = create_input_backend(device, input_sim, agent=agent)   # 有代理 → AgentInput，否则 AdbInput
```

主窗口连接流程（`ui/main/window_ops.py::_DeviceWorker._do_connect`）按用户配置
`android_input_method = "device_gesture"`
（设置页「安卓输入」/ 主窗口「设备端执行」勾选）决定是否尝试输入代理。
截图始终独立服从「安卓截图」中的 `scrcpy` / `ADB screencap` 选择：

- 连上：输入走 `AgentInput`；截图仍按设置走 `AdbCapture` 或 `AndroidStreamCapture`
- 连不上：日志提示一行，整体回退 `AdbInput` + screencap/scrcpy，**不算连接失败**
- 代理只替代 `adb shell input`，不会因为启用设备端执行而切换截图后端

## 两端改动约定

- 不兼容的协议修改必须同时改 `AgentServer.kt` 与 `agent.py`，并 bump 两边的 `PROTOCOL_VERSION`
  （v1 基础 op；v2 加 `calib_*` 与 status 的 `calib_identity` / `screen`）；
  PC 端握手时版本不一致直接拒绝（提示升级手机 app），避免静默错位。
- PC 侧单测 `tests/core/test_device_agent.py` 用本地假服务端覆盖线协议与后端行为；
  Kotlin 侧可用 `kotlinc -cp android.jar` 做编译检查（见开发日志 2026-08-22）。

## 离线执行扩展（offline_protocol = 1）

基础输入协议保持 v3，新增能力通过握手字段 `offline_protocol:1` 探测；旧 APK 不发送
控制租约或离线 op。`pc_controlling` 表示实际占用，区别于诊断连接数。

| op | 请求字段 | 响应 |
|---|---|---|
| `control_begin` / `control_end` | — | 取得/释放本连接的 PC 输入控制权；手机活跃任务拒绝取得 |
| `offline_status` | — | 任务状态、原因、日志、输出及最近同步 |
| `offline_tasks` | — | 仅暴露支持 android 的任务，包括类实现以 `DEVICE_VISIBLE` 明确开放的专用任务；未同步提示先同步 |
| `offline_users` | — | 已同步且资料有效的用户名册和手机活动用户 |
| `offline_start` | `task_id`、可选 `username` | 在同一控制锁内绑定已同步用户并启动；省略用户时沿用手机活动用户；必须先开启悬浮服务 |
| `offline_pause` / `offline_resume` / `offline_stop` | — | 请求暂停、继续或结束 |
| `offline_diagnostics` | 可选 `screen_repetitions`（0～10，默认 0） | `report`：依赖/插件/引擎、OCR 和内存；同步后可仅采集真实屏幕连续推理，不注入游戏动作；手机执行中拒绝检查 |
| `offline_sync_begin` | `size`,`sha256` | 建立暂存上传，最大 256 MiB |
| `offline_sync_chunk` | `offset`,`data`（Base64） | 顺序写入，重复块内容相同才允许重试 |
| `offline_sync_commit` | — | 验证完整包和逐文件哈希、版本、DB 后交换 config；重载失败回滚 |

手机任务状态为 idle/running/pausing/paused/stopping/done/failed/stopped。
设备端截图后端自行满足无障碍 `takeScreenshot` 的框架限流（AOSP <=333ms 判定间隔
过短，按服务连接计时）：请求前补齐节拍，并通过 `CaptureBackend.min_capture_interval`
把下限告知采样循环；PC 后端声明 0，取 max 后行为不变。
手机 OCR 使用 C++ 后端完成检测、分类、识别和 CTC 解码，复用 APK 中同一份
ONNX Runtime 1.20.0 和现有模型。跨语言只传 BGR 字节图像、返回文字/置信度/四角
坐标，不传模型浮点输入输出。公共 OCREngine 继续负责区域归属、清洗和字段拼接；
PC 使用原 RapidOCR 后端。原生后端故障向上传播，不能作为空识别继续操作。
模型会话跨任务复用，图像与推理临时对象按调用释放；任务终态清除 from last 帧。
诊断记录各模型运行次数、最大输入/输出字节和尺寸，不记录识别原文。
APK 主页面使用应用栏菜单导航，首页突出悬浮服务启停；权限、高级功能与诊断是独立
展示上下文，页面切换不写配置或启动任务。旋转恢复当前页面，系统返回和应用栏返回
均先回首页；既有 ADB 自检与只读报告通道保持不变。
状态额外携带有界 `log_records`（seq/text/level）和每次启动递增的
`log_generation`，供手机增量显示和重置日志；原 `logs` 文本列表保持不变。
设备状态保留最近 200 条日志记录供 PC 读取，悬浮窗仅保留最近 100 行展示。
手机用户选择读取同步用户名册并校验资料，空闲时仅更新手机 Session 的活动用户；
运行、暂停和结束中在 UI 与 CONTROL_LOCK 下拒绝切换，不重载本轮参数/Session。
状态增加 `sync.execution_username` 表示当前活动用户；`sync.username` 保留同步来源
的初始用户，不重写同步清单，也不回写 PC。选择本身不创建用户或修改 Profile DB。
pausing/paused/stopping 同样占用任务槽。暂停确认在引擎实际等待点发生，结束会唤醒暂停。
PC 断线不停止手机任务；PC 租约在重连后重新申请，防止重连绕过执行互斥。

配置包仅包含 config/system、local、remote、session 的数据文件（排除 Git、锁与诊断
归档）。DB 由 SQLite backup 生成，不能直接复制仍有 WAL 的数据库。手机校验后整体
交换 config，留存上一份 offline-backup/config；应用与启动共用锁，并清理配置单例和
引擎缓存。Python 实现随 APK 打包，不通过配置同步热更新。

APK 不打包 config/system，也不在启动时解压配置；没有同步标记时不装配引擎。
配置重载前关闭旧 OCR 后端的 det/cls/rec 会话及其 SessionOptions，再丢弃缓存。
Android 关闭 CPU arena，与 PC 默认一致；rec/cls 单条批次，输入 direct buffer 在会话内
复用，输出只取一份 ByteBuffer 底层数组。Java 或 ORT 分配失败转换为 MemoryError，
穿透识别失败隔离层并终止任务，释放模型资源。

运行诊断位于 data/diagnostics/android-runtime.jsonl，每份最大约 2 MiB，保留当前和上
一份；Native 日志记录模型名、会话数、推理输入/输出形状与字节、耗时、Java/native
分配及 RSS/Swap，Python 记录任务首尾与真实屏幕验收采样。没有截图、OCR 原文或配置
全文。SelfTestProvider 新增 /runtime、/runtime-previous 只读路径，沿用 shell/root/
本应用 UID 校验；配置同步不包含 data/diagnostics。

## 手机任务设置桥（应用内部，不改变代理线协议）

`core.ondevice.task_settings` 通过 `list_settings`、`get_settings`、
`preview_parameters`、`save_settings` 向原生列表/详情页提供用户上下文、参数定义、
生效值、联动可见字段和逐字段错误。编辑用户由调用方显式传入，不修改活动执行用户。
写入与手机任务启动、配置同步共享 `CONTROL_LOCK`，Kotlin 桥同时拒绝 PC 代理控制期间写入。
配置校验令牌覆盖同步状态、参数定义、生效值和当前任务用户覆盖，拒绝陈旧草稿。

代码工作流可声明 `DEVICE_SETTINGS_MODULE` 专用适配器，模块提供
`get_settings(username) -> dict`（kind/schema/values）及
`save_settings(username, values) -> None`（失败抛出可行动错误）。业务候选与校验仍归
插件领域层所有；目前自动调律使用该协议，不在公共桥硬编码调律规则。
普通脚本直接复用任务元数据与 `task_params`，不增加平行手机配置仓储。
