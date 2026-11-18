# 2026-08-22 设备端代理通道：PC 经律匠 app 用无障碍截图 + 手势，替代 adb shell input

> Layer owner：L9 Evidence（过程记录）
> Feeds/Affects：L7 `docs/30-architecture/04-device-agent-protocol.md`（协议契约，本次新增）；
> L8 `core/android/agent.py`、`android/.../AgentServer.kt`；L6 ADB 模式后端选择
> Stability：过程记录，不再更新；稳定结论已提升到协议文档与用户指南

## 目标

PC 端 ADB 模式控制手机改走手机上的律匠 app：截图与点击/拖拽/推住/BACK·HOME 由 app 内的
无障碍服务（可选 Shizuku）在设备上落地，`adb shell input tap/swipe` 退为不可达时的回退。

## 做了什么

### 设备端（Kotlin）

- 新增 `AgentServer`：`LocalServerSocket("lvjiang-agent")` + 长度前缀 JSON 协议，
  op：`ping/status`、`screenshot`、`tap`、`long_press`、`swipe`、`hold_move`、`key`、`shell`。
  分发到既有的 `A11yBridge` / `ShellBridge`，没有新增任何设备端能力——只是把它们借出去。
- 对端 uid 校验：只接受 adbd（shell/root）与本进程。abstract socket 任何 app 都能连，
  不校验等于让任意 app 往屏幕上注入手势。
- `App.onCreate` 与 `A11yService.onServiceConnected` 各调一次幂等 `start()`：
  无障碍开着 → 进程常驻 → 代理在线，用户不需要再开什么。
- 编译验证：本机没有 Windows 侧的 Gradle 工具链（`build.gradle.kts` 钉死了 Windows buildPython），
  用 `kotlinc -cp android-34/android.jar` 加 `BuildConfig` / `IShellService` / Shizuku 三个桩
  编译 `AgentServer.kt + A11yService.kt + ShellBridge.kt + ShellService.kt + App.kt`，零错误。
  **真机装包验证待 Windows 侧 Gradle 构建**——这是本轮唯一没闭环的一段。

### PC 端（Python）

- `core/android/agent.py`：`AgentClient`（forward + 握手 + 协议版本校验 + 断线重连一次）、
  `AgentCapture`（RGBA 裸字节直转 BGR，免 PNG 编解码；节流失败退避重试 3 次）、
  `AgentInput`（公开面与 `AdbInput` 完全一致；drag hold 走 `hold_move` 真正停住；
  ESC→BACK、HOME→HOME；其它键按 keycode 发，需要 Shizuku）。
- 工厂 `create_input_backend / create_capture_backend` 加 `agent` 参数；新增 `connect_agent`。
- 配置 `UserConfig.adb_agent_mode`（默认开）；设置页「ADB 输入方式」下拉 + 主窗口「设备端手势」勾选框。
- 连接流程：代理连不上只提示一行并回退 adb，不算失败；代理在但截图不可用时截图退 screencap、手势仍走代理；
  「流式截图」勾着时预览仍用 scrcpy、手势走代理。断连时关 socket + 撤 forward。

### 测试

`tests/core/test_device_agent.py`（14 例）：本地 TCP 假服务端按同一协议应答，覆盖帧编解码、
握手（版本不符 / 连上即关的假连接 / 拒连 / forward 失败）、`retryable` 重试、断线重连、
输入分派参数（tap/swipe/hold_move/scroll/key）、工厂选择。全量 2196 通过。

## 决策与取舍

- **代理挂在 app 进程而不是单独前台服务**：无障碍绑定已经让进程常驻，再起前台服务是多一层通知与权限。
  代价是无障碍没开、app 也没打开时代理不在线——这时 PC 端本来也没有无障碍手势可用，回退 adb 就是正确行为。
- **默认开启 + 自动回退**，而不是默认关：目标就是把 adb input 换掉；没装 app 的用户无感（多一行日志）。
- **RGBA 裸字节而非 PNG**：整屏 1260×2800 约 14 MB/帧，USB 下 adb forward 吞吐足够；
  省掉的是设备端 PNG 编码（Bitmap.compress 数百 ms）与 PC 端解码。Shizuku 通道保留 PNG（`screencap -p` 原生输出）。
- **`key` 不静默降级**：无障碍只会 BACK/HOME，其它 keycode 没 Shizuku 时明确报错，
  而不是假装发了——与「加 fail-fast 前先问报错时刻与受害时刻」一致：这里报错时刻就是受害时刻，用户在场。

## 未做

- 真机端到端（装包 → PC 连接 → 跑一条 .wf）：等 Windows 侧构建 APK 后做。
- scrcpy 流式模式下的 `capture_lossless` 仍回退 screencap，没有改走代理截图。
