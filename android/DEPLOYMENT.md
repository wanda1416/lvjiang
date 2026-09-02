# Android 构建、部署与设备端手势验收

本文供后续部署 AI 使用。目标不是只把 APK 装上，而是确认 PC 经 ADB 连接 App 后，
App 能明确显示连接状态，设备端辅助已开启，并能真实执行下发的点击、滑动和按键。

## 1. 不得擅自改版本字段

构建和部署前先读取 `packaging/readme.md`。`versionName`、`versionCode`、Python 包版本与
`content_version` 都是开发者主动发布行为；除非开发者明确要求，本流程一律保持原值。
尤其不得因为构建 APK、修改了配置或认为版本号“应该递增”而自行修改。

## 2. 工具链

- JDK 17 或 21；
- Android SDK Platform 35、Build Tools 34.0.0、Platform Tools；
- Python 3.10 作为 Chaquopy `buildPython`；
- 真机已开启开发者选项和 USB 调试，`adb devices -l` 显示 `device`；
- 正式分发需要本机 `android/keystore.properties`，格式见 `README-signing.md`。

仓库默认从 `.tooling/python/cpython-3.10*/bin/python3` 查找 Python。可这样准备：

```bash
UV_PYTHON_INSTALL_DIR="$PWD/.tooling/python" uv python install 3.10
```

也可通过 `-PbuildPython=/绝对路径/python3.10` 或 `LVJIANG_BUILD_PYTHON` 指定。

## 3. 构建

从仓库根目录执行：

```bash
cd android
JAVA_HOME=/path/to/jdk17 ANDROID_HOME=/path/to/android-sdk \
  ./gradlew :app:assembleDebug
```

Windows PowerShell：

```powershell
cd android
$env:JAVA_HOME = "C:\path\to\jdk17"
$env:ANDROID_HOME = "C:\path\to\android-sdk"
.\gradlew.bat :app:assembleDebug
```

预期产物为 `android/app/build/outputs/apk/debug/app-debug.apk`。正式包执行
`:app:assembleRelease`，并在分发前确认使用正式 keystore；缺少 keystore 时项目会用 debug
签名兜底，这种 APK 只能测试，不能作为正式升级包。

## 4. 安装与权限

先确认目标序列号，再覆盖安装；不要先卸载，卸载会清除 App 数据和无障碍授权：

```bash
adb devices -l
adb -s <serial> install -r -g android/app/build/outputs/apk/debug/app-debug.apk
adb -s <serial> shell am start -n com.lvjiang.app/.MainActivity
```

在手机系统设置中手动开启“无障碍 → 律匠自动操作”。回到 App 首页，应看到：

- `辅助已开启`；
- PC 尚未连接时显示 `PC 未连接（等待 ADB 连接）`；
- 悬浮窗明确标为“仅手机独立运行任务需要”，PC 设备端手势不依赖它。

系统通常不允许普通 App 自行开启无障碍。仅在受控开发机上才可使用 ADB 修改 secure
settings；面向用户的部署不得把这种命令写成常规授权方案。

## 5. PC 连接验收

PC 端选择该 Android 设备，把“安卓输入方式”设为“设备端手势”，然后重新连接设备。
验收以下可观察状态：

1. PC 日志出现 `已连接设备端代理`，并标明无障碍或 Shizuku 通道；
2. App 保持前台时，一秒内从 `PC 未连接` 变为 `PC 已连接`；
3. App 状态区显示最近一条指令成功或失败；
4. 若手机独立模式的悬浮球正在运行，PC 连接后它自动隐藏，断开 PC 后自动恢复；
5. 关闭无障碍且未授权 Shizuku 后重连，PC 必须提示输入通道未就绪并回退
   `adb shell input`，不能显示代理连接成功。

状态协议也可用下面的只读检查确认。它会建立临时转发，打印握手状态，随后自动关闭：

```bash
python - <<'PY'
import json
from lvjiang.core.android import AdbDevice, connect_agent

client = connect_agent(AdbDevice("<serial>"))
if client is None:
    raise SystemExit("代理不可用：检查 App、无障碍和 ADB")
try:
    print(json.dumps(client.status, ensure_ascii=False, indent=2))
finally:
    client.close()
PY
```

## 6. 指令落地闭环

不能只看返回 `ok=true`。在不会造成数据损失的测试界面完成以下验证，并用前后截图确认
画面确实变化：

- `tap`：点击一个可回显状态的按钮；
- `swipe`：在可滚动页面滑动，确认内容位置变化；
- `key BACK`：打开一个次级页面后返回；
- `hold_move`：在专用测试页面或游戏摇杆上验证“推到位后保持”，不要在有破坏性操作的页面测试；
- 故意让设备端手势失败一次，确认 PC 工作流收到异常，而不是继续假装动作成功。

协议和调用入口见 `docs/30-architecture/04-device-agent-protocol.md`。最小调用示例：

```python
from lvjiang.core.android import AdbDevice, connect_agent

client = connect_agent(AdbDevice("<serial>"))
assert client is not None
try:
    client.call("tap", x=100, y=200)
    client.call("swipe", x1=500, y1=900, x2=500, y2=300, duration_ms=400)
    client.call("key", name="BACK")
finally:
    client.close()
```

坐标必须先在测试设备截图上确认，严禁照抄示例坐标到真实业务页面。

## 7. 提交前门禁

至少执行：

```bash
QT_QPA_PLATFORM=offscreen python -m pytest -q \
  tests/core/test_device_agent.py \
  tests/ui/test_android_method_settings.py \
  tests/core/test_user_config.py
cd android && ./gradlew :app:assembleDebug
```

发布前仍须按 `packaging/readme.md` 跑完整 CI。没有连接真机时，可以确认协议单测和 APK
构建，但不得声称已完成真机端到端验证；应把第 4～6 节列为待部署机执行项。

## 8. 常见故障

| 现象 | 排查 |
|---|---|
| App 显示辅助未开启 | 回到系统无障碍设置重新开启“律匠自动操作”，再回 App 确认状态 |
| PC 回退 ADB | 确认 App 已安装、进程可启动、辅助已开启；重连设备以重新握手 |
| App 显示 PC 未连接 | 确认 PC 仍保持设备连接；检查 `adb forward --list` 和 PC 日志 |
| 指令返回失败 | 查看 App 最近指令状态、PC 的 `AgentOpError` 文本和 logcat `AgentServer` |
| 悬浮球挡住截图 | 正常情况下 PC 连接会自动隐藏；若仍存在，确认安装的是本次构建的 APK |
| `install -r` 报签名不一致 | 当前 APK 与设备已装版本签名不同；先确认目标和数据备份，再由开发者决定是否卸载，不能自行清数据 |
