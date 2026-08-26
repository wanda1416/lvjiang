# 设备端代理协议（PC ↔ 律匠 app）

> Layer owner：L7 Contract（PC 与设备 app 之间的线协议）
> Feeds/Affects：L8 `src/lvjiang/core/android/agent.py`、`android/.../AgentServer.kt`；L6 ADB 模式的截图/输入后端选择
> Stability：稳定（协议版本 1；两端实现必须同步改）

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
- PC 侧传输失败（断线/超时）就地重连一次（forward 仍在）；再失败抛 `AgentTransportError`。

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
| `ping` / `status` | — | `protocol`、`app`（versionName）、`sdk`、`a11y`、`shizuku`（在跑）、`shizuku_granted`、`calib_identity`（屏幕映射是否恒等）、`screen{w,h,rotation}` |
| `screenshot` | `via`、`timeout_ms`(5000) | a11y：`fmt:"rgba"`, `w`, `h` + RGBA 裸字节；shell：`fmt:"png"` + PNG。节流失败 `retryable:true` |
| `tap` | `x`,`y`,`duration_ms`(50) | — |
| `long_press` | `x`,`y`,`duration_ms`(800) | — |
| `swipe` | `x1`,`y1`,`x2`,`y2`,`duration_ms`(300) | — |
| `hold_move` | `x1`,`y1`,`x2`,`y2`,`move_ms`,`hold_ms` | a11y 两段 stroke 真正停住；shell 只能把 hold 合并进 swipe 时长 |
| `key` | `name:"BACK"/"HOME"` 或 `keycode:int` | BACK/HOME 无障碍用 `performGlobalAction`；其它 keycode 只有 Shizuku 能发（`auto` 下自动转 shell，没 Shizuku 报错不静默降级） |
| `shell` | `cmd:[...]` | Shizuku 执行，stdout 作二进制负载 |
| `calib_get` | — | `key`（机型_WxH）、`screen{w,h,rotation}`、`calib{sx,ox,sy,oy}`、`identity`、`stored`、`overlay{w,h}\|null` |
| `calib_set` | `sx`(1),`ox`(0),`sy`(1),`oy`(0) | 保存当前朝向分辨率的屏幕映射；全恒等等价于删文件。返回同 `calib_get` |
| `calib_clear` | — | 删掉当前朝向分辨率的映射文件 |
| `calib_mark` | `x`,`y`,`tap`(false),`via` | 在 (x,y) **经映射后**的像素画准星（需悬浮窗权限，没有则 `ok=false`）；`tap=true` 同点再点一下。返回 `px{x,y}` + `calib_get` 字段 |
| `calib_hide` | — | 撤掉准星覆盖层 |
| `float_icon` | `hidden`(true) | 动态显隐悬浮球（截图/标定前藏起来，免得被截进画面）。返回 `running`（悬浮服务是否在跑）、`hidden` |

坐标都是设备截图坐标系的像素（与 `screencap` / 无障碍截图一致），PC 端不做旋转变换。

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
（设置页「安卓输入方式」/ 主窗口「设备端手势 (Beta)」勾选）决定是否尝试输入代理。
截图始终独立服从「安卓截图方式」中的 `scrcpy` / `ADB screencap` 选择：

- 连上：输入走 `AgentInput`；截图仍按设置走 `AdbCapture` 或 `AndroidStreamCapture`
- 连不上：日志提示一行，整体回退 `AdbInput` + screencap/scrcpy，**不算连接失败**
- 代理只替代 `adb shell input`，不会因为启用设备端手势而切换截图后端

## 两端改动约定

- 改协议（新 op / 改字段）必须同时改 `AgentServer.kt` 与 `agent.py`，并 bump 两边的 `PROTOCOL_VERSION`
  （v1 基础 op；v2 加 `calib_*` 与 status 的 `calib_identity` / `screen`）；
  PC 端握手时版本不一致直接拒绝（提示升级手机 app），避免静默错位。
- PC 侧单测 `tests/core/test_device_agent.py` 用本地假服务端覆盖线协议与后端行为；
  Kotlin 侧可用 `kotlinc -cp android.jar` 做编译检查（见开发日志 2026-08-22）。
