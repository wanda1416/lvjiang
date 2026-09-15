# 截图与输入后端矩阵

> Layer owner：L6 后端选择（`run_control` 按方案连接模式与设备状态挑选）
> Feeds/Affects：`core/desktop/*`、`core/android/*`、`core/ondevice/*`、`core/input_base.py`；DSL 各指令在不同后端上的实际行为
> Stability：随后端实现演进；新增/删除后端或改变某条能力时必须同步本表

同一条 DSL 在不同连接方式下落到的物理机制差别很大：`drag ... hold` 在 a11y
是两段 stroke，在 ADB 是 `motionevent` 序列或匀速 swipe，在 PC 是鼠标按下移动。
本文把所有路径列在一张表里，回答"这条指令在这个环境到底做了什么、精度如何、
哪些做不到"。

## 一、路径一览

运行目标由**方案**（`app.yaml plans`）决定：环境 + 布局 + 连接模式。连接模式只有
两种，后端在模式内部再按设备状态细分。

```
连接模式 windows（Win32 窗口）
├─ 截图：DesktopCapture（mss 抓窗口区域）
└─ 输入：SendInputInput   前台 —— 真实光标/键盘事件，窗口需在前台
         PostMessageInput  后台 —— 向目标 hwnd 投递消息，不动光标

连接模式 adb（Android 设备）
├─ 截图：AgentCapture       无障碍 takeScreenshot，RGBA 裸字节（Android 11+）
│        AndroidStreamCapture scrcpy 服务端 H.264 流，零延迟取最新帧
│        AdbCapture          adb exec-out screencap，逐帧起子进程
└─ 输入：AgentInput  ─ via=auto ─┬─ a11y   dispatchGesture / performGlobalAction
         │                       └─ shell  Shizuku 里跑 input tap/swipe/keyevent
         AdbInput    adb shell input（Agent 未连接时的兜底）

设备端独立运行（手机上的律匠 app 自己跑 .wf）
├─ 截图：ondevice/capture  无障碍 takeScreenshot 或 Shizuku screencap
└─ 输入：A11yInput / ShellInput（与 Agent 的两条通道同源，少一层协议）
```

- 「手游投屏」方案 = `windows` 模式 + `android` 环境 + `android_cast` 布局：PC 上
  对 scrcpy 窗口用 SendInput/PostMessage，截图走 DesktopCapture。它是 PC 路径，
  不是 Android 路径。
- Agent 的 `via=auto`：无障碍就绪优先无障碍，否则 Shizuku，两者都没有时握手失败
  回退 AdbInput。按键类请求在 a11y 只能发 BACK/HOME，其余 keycode 自动转 shell
  （需 Shizuku 已授权）。

## 二、输入能力矩阵

✅ 完整支持 ｜ ⚠️ 降级/有条件 ｜ ❌ 不支持（记 warning 或抛错）

| DSL / 能力 | PC 前台 SendInput | PC 后台 PostMessage | Android Agent a11y | Android Agent shell (Shizuku) | Android ADB `input` |
|---|---|---|---|---|---|
| `click` 左键 | ✅ 移动光标后点击 | ✅ 投递消息，不动光标 | ✅ tap | ✅ `input tap` | ✅ `input tap` |
| `click right/middle/x1/x2` | ✅ | ⚠️ 降级为左键 | ⚠️ 触屏无鼠标键，按普通点击 | ⚠️ 同左 | ⚠️ 同左 |
| `click ... hold` | ✅ 按下→等待→抬起 | ✅ 左键 | ✅ `long_press` stroke | ✅ 原地 swipe | ✅ 原地 `input swipe` |
| `click` 命中 `activation_key` | ✅ 转按键（含 hold→`press hold`） | ✅ 同左（后台键盘消息） | 布局不绑键，走触屏 | 同左 | 同左 |
| `drag A to B` / arrow | ✅ 按下→`smooth_move_to`→抬起 | ✅ WM_MOUSEMOVE 分步投递 | ✅ swipe stroke | ✅ `input swipe` | ✅ `input swipe` |
| `drag ... hold` | ✅ 到终点后保持 | ✅ | ✅ `hold_move`：两段 stroke 真正停住 | ⚠️ hold 合并进 swipe 总时长，匀速插值，推满只在结尾生效 | ✅ Android 10+：`motionevent DOWN→MOVE→sleep→UP`；⚠️ 更老系统合并 swipe |
| `drag` 中间过程精度 | 高精度绝对截止时间 | 同左 | 系统按帧调度 | `input` 进程 ~100–300 ms/条 | 同左 + adb 往返 |
| `drag ... scale/exact` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `move to / move by` | ✅ 相对位移分步 | ⚠️ `move to` 投递 WM_MOUSEMOVE；`move by` 忽略 | ❌ 无鼠标概念 | ❌ | ❌ |
| `place` | ✅ SetCursorPos | ⚠️ 等价一次 WM_MOUSEMOVE | ❌ | ❌ | ❌ |
| `scroll ... interval` | ✅ 逐格滚轮事件 + 间隔 | ✅ 逐格 WM_MOUSEWHEEL | ⚠️ 一次 100px×n 的 swipe，`interval` 忽略 | ⚠️ 同左 | ⚠️ 同左 |
| `press "ESC"` / `"HOME"` | ✅ 真实 ESC 键 | ✅ | ✅ 系统 BACK / HOME 全局动作 | ✅ keyevent 4 / 3 | ✅ keyevent 4 / 3（三条 Android 路径 ESC 一律等于系统返回） |
| `press "X"`（其它键） | ✅ 扫描码 SendInput | ✅ 后台键盘消息 | ⚠️ `via=auto` 转 shell，无 Shizuku 则报错 | ✅ `input keyevent`（一次即含抬起） | ✅ 同左 |
| `press "X" hold` / `down` / `up` | ✅ 真实按住 | ✅ | ❌ keyevent 是一次性的，`hold`/`down`/`up` 无法保持 | ❌ 同左 | ❌ 同左 |
| `press "MOUSE_LEFT"` 等鼠标键原语 | ✅ | ❌ | ❌ | ❌ | ❌ |
| `paste` | ✅ 剪贴板 + Ctrl+V | ✅ 剪贴板 + 后台 Ctrl+V | ❌ | ❌ | ❌ |
| `replay input_trace`（高精度回放） | ✅ 唯一支持 | ❌ | ❌ | ❌ | ❌ |
| 多点触控（同时推杆 + 转视角） | 无此概念 | 无此概念 | ✅ `GestureDescription` 最多 10 stroke（尚未接入 DSL） | ❌ | ❌ 单指针 |
| 全局热键（开始/停止/暂停/录制） | ⚠️ 前台模式禁止 `press` 当前热键 | ✅ | — | — | — |
| 抖动（point 半径 / 区域 click_rect） | ✅ | ✅ | ✅ | ✅ | ✅ 各后端一致，由引擎层计算 |
| 停止响应（hold 期间） | ✅ 引擎注入 `stop_check`，立即抬起 | ✅ | ⚠️ 手势已下发无法中断，等本段 stroke 结束（≤ 60 s） | ⚠️ 等 `input` 返回 | ⚠️ 等 shell 返回 |

关键差异展开：

- **PC 前台 vs 后台**：前台是真实输入，任何窗口都收；代价是窗口必须在前台、
  光标被占用、不能同时干别的。后台是消息投递，SDL/DirectInput 类游戏可能
  不认（只读硬件层输入），`move by`、鼠标键原语、高精度回放都做不到；优点是
  不抢光标、可最小化运行。`activate_before_send` 让后台模式在投递前瞬时激活
  窗口再还原，专门适配 scrcpy 这类只在有焦点时处理消息的窗口。
- **a11y vs shell/ADB 的手势**：a11y 是进程内 Binder 调用、系统按帧调度、
  路径由系统插值；shell/ADB 每条 `input` 都在设备上拉起一次 `app_process`
  （100–300 ms），再加 adb 往返。木桩测伤这类靠 `hold 1.52 after wait 0.8`
  堆时序的脚本只有 a11y 通道才有讨论精度的基础。
- **a11y 的手势时长上限**：单次 `dispatchGesture` 最长 60 秒。`hold_move` 的
  移动段与停住段各是一次 dispatch，任一段超过 60 秒手势失败。长时间持续推杆
  不用 `hold` 表达，属于后续 locomotion 会话原语的职责。
- **a11y 会被真实触摸取消**：用户碰屏幕，正在执行的手势走 `onCancelled`，
  指针被强制抬起。ADB 注入不受影响。
- **按键**：Android 侧只有 shell/ADB 能发任意 keycode，且 `keyevent` 是一次性的，
  `press hold / down / up` 在所有 Android 路径都不可能真正保持。

## 三、截图能力矩阵

| | DesktopCapture (PC) | AgentCapture (a11y) | AndroidStreamCapture (scrcpy) | AdbCapture (screencap) | 设备端 ondevice |
|---|---|---|---|---|---|
| 机制 | mss 抓窗口区域，专用工作线程 | `takeScreenshot` RGBA 裸字节 | scrcpy 服务端 H.264 流，后台线程持续解码 | `adb exec-out screencap -p`，逐帧子进程 + PNG 编解码 | a11y `takeScreenshot` 或 Shizuku `screencap` |
| 单帧耗时 | 十几 ms | 几十 ms | `capture()` 零等待返回最新帧 | 300–800 ms | 同 a11y/shell |
| 前置条件 | 目标窗口可见（最小化拿不到） | Android 11+、无障碍已开 | 设备端推 scrcpy-server.jar | 只需 adb | app 常驻 |
| 限制 | 被遮挡部分是遮挡物；DPI 感知换算 | 有节流（连续调用最小间隔数百毫秒）；`FLAG_SECURE` 窗口不可截 | 有压缩失真；`capture_lossless` 另取 | 慢；无 UI 实时预览 | 同 a11y/shell |
| 尺寸来源 | 窗口客户区 | 截图实际尺寸 | 设备原始分辨率（不缩放，与 tap 同坐标系） | 实际截图尺寸（横屏游戏 wm size 可能是竖屏，以截图为准） | 同左 |
| 实时预览 | ✅ | ✅ 轮询 | ✅ 帧回调 | ❌ | — |

坐标系约定：所有 Android 截图后端输出的图像与 `input tap` 使用同一坐标系（不缩放、
已对齐设备方向），工作流的画布归一化坐标经布局 canvas 换算后直接可用。

## 四、暴露面与运行前提

不同路径被游戏或系统"看见"的方式不同，这不是精度问题，但决定了某条路径能不能用：

| 路径 | 需要用户做的事 | 被检测的信号 |
|---|---|---|
| PC 前台 | 游戏窗口保持前台 | 无特殊信号（真实输入） |
| PC 后台 | 目标 hwnd | 部分游戏不响应消息注入 |
| a11y | 手机上开启律匠无障碍服务（一次） | 部分手游检测"启用了无障碍服务"并拒绝运行 |
| Shizuku | 每次开机后重新激活 Shizuku 并授权 | 开发者选项/无线调试已开 |
| ADB | USB 或无线调试已连接 | 同上 |

## 五、选择建议

- **战斗/移动/木桩**（时序敏感）：Android 用 a11y；PC 用前台 SendInput。后台
  PostMessage 只在游戏确认接受消息注入且不需要 `move by` 时用。
- **日常导航/扫描**（时序不敏感）：任一路径均可；Android 上开 a11y 免去每次
  激活 Shizuku 的麻烦。
- **需要任意按键**（安卓）：必须有 Shizuku 或 ADB；只开无障碍会在 `press` 处报错。
- **两个都开**（无障碍 + Shizuku）是安卓最稳的配置：手势走 a11y，按键自动转 shell。

## 六、维护约定

- 新增或删除输入/截图后端、改变某条指令在某后端的行为时，先改本表再改代码注释。
- 各指令文档（`32-grammar/03.3-mouse.md` 等）只保留该指令特有的说明，跨后端
  差异以本表为准并链接过来。
- 后端类的 `kind`（`InputBackendKind`）是引擎判定运行模式的唯一依据，不允许在
  通用层按具体类名分派。
