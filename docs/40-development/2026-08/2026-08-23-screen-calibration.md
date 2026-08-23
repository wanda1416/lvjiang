# Dev Log: 屏幕标定——换机后对齐布局画布 + 设备端屏幕映射 + macOS 构建链 + 代理通道实机验证

> 日期：2026-08-23
> 涉及模块：`core/screen_calib.py`、`core/ondevice/screen_calib_api.py`、`core/android/calib.py`、`core/android/agent.py`、
> `android/.../CalibActivity.kt`、`ScreenMap.kt`、`CalibOverlay.kt`、`AgentServer.kt`、`FloatService.kt`、`android/app/build.gradle.kts`
> 关键词：屏幕标定、画布、device profile、ScreenMap、准星覆盖层、Gradle wrapper、实机 e2e

---

## 问题描述

布局（场景区域 / 点 / 箭头）是脚本作者在自己那台手机上适配的（1080p 挖孔屏，画布 = 整屏）。换一台手机，
游戏内容区常常不在同一位置：挖孔安全区不同、宽高比不同留黑边、系统缩放——所有区域一起偏，OCR 截错位置、
点击点到旁边。用户没有办法在手机上自己把布局"对齐"到本机。

另外两个前置问题：

1. 2026-08-22 做的设备端代理通道只在 PC 侧假服务端和 `kotlinc` 上验过，没上过真机——`build.gradle.kts` 把
   buildPython 写死成 Windows 路径，Mac 上出不了包。
2. 个别机器截图网格与触摸网格本身就不重合（挖孔处理、截图缩放），这是比"内容区位置"更底一层的偏差。

## 方案

### 两层映射，各管一件事

| 层 | 解决什么 | 存在哪 | 怎么量 |
|---|---|---|---|
| **布局画布**（`layouts.yaml` 的 `canvas`） | 游戏内容区在截图里的矩形（换机后最常见的偏差） | 该布局的本地覆盖（`config/local/layouts.yaml`，设备私有） | app 内「屏幕标定」页：参照图 + 本机截图，点几组地标 |
| **ScreenMap**（设备端） | 截图像素 → 触摸像素不重合 | `filesDir/lvjiang/calib/<型号_WxH>.json` | PC `python -m lvjiang.core.android.calib probe --apply`，自动找准星 |

思路沿用"参照机 + 设备 profile"（参照机的内容区 → 目标机的内容区，每轴一个 scale/offset）加
"在注入点画准星、截图比对"的标定法。律匠的画布本来就是 layout 级的内容矩形，`layouts.yaml` 里
「继承布局 = extends + 不同 canvas」已经是 device profile 的形状，缺的只是**把矩形量出来**。

### 画布标定流程（`core/screen_calib.py`，PC 与设备共用）

1. 布局目录放参照截图 `layouts/<布局>/_reference.png`（+ sidecar `_reference.json` 记拍它时的画布与尺寸）。
   作者在自己手机上用标定页「本机画面设为参照图」即可生成，拉回 PC 进 `config/system` 随版本分发。
2. 目标机停在同一画面，标定页藏起自己截一张（透明主题 + 内容 INVISIBLE + 窗口底色全透明 → 无障碍
   `takeScreenshot` 拍到的就是底下的游戏；截图期间悬浮图标也藏起来，免得被当地标）。
3. 用户在参照图上点一个地标 → `locate_landmark` 用 120px patch 多尺度 `TM_CCOEFF_NORMED` 在本机图里找
   同一地标（分数 ≥ 0.6 采纳）；找错 / 没找到就在本机图上点一下纠正。
4. `solve_canvas`：地标先换成参照画布内归一化坐标 (u,v)，逐轴最小二乘 `live = c + u * size`；
   某轴两点没拉开（间距 < 0.15）就只拟合平移、缩放按两图尺寸比等比（letterbox）；残差回显。
5. 「保存画布」→ `save_canvas` 写 `layouts.yaml` 本地覆盖（用户态只落 diff，与系统值一致则删文件）；
   「恢复默认」回系统值。引擎下次加载布局自动用新画布，所有场景一起生效。

### ScreenMap 标定（`core/android/calib.py`）

清映射 → 对角两点 `calib_mark` → 截图找准星 → 拟合逆映射 → 写回 → 第三点验证。映射施加在
`A11yBridge` / `ShellBridge` 注入口，PC 代理通道与设备端 Python 通道一次标定两边生效。

## 实机验证（Android 14 测试机 1080x1920，本机 macOS 出包）

- 代理通道：握手 → 无障碍截图 1080x1920 0.30s → swipe 拉下通知栏 → BACK 收起，截图前后对比确认手势落地。
- 在 app 内走完整标定回路：悬浮面板「屏幕标定」→ 自动截底下的时钟 app → 设为参照图 → 换成人工裁剪缩放的
  参照图（模拟内容区 = (60,200,960,1500)）→ 点两个地标（FAB 与「08:30」文字）均自动定位 → 解出
  `x=0.056 y=0.128 w=0.889 h=0.759`（期望 0.0556/0.1259/0.8889/0.7595，残差 0px）→ 保存后
  `config/local/layouts.yaml` 出现覆盖 → 恢复默认后文件删除。
- ScreenMap probe：两探针点 0.0px 偏差 → 恒等，验证点误差 0.0px。

## 踩坑

| 坑 | 现象 | 处理 |
|---|---|---|
| Android 12+ 悬浮窗按 ~0.8 不透明度合成 | 截图里准星是 (208,4,187) 而非 (255,0,229)，精确色匹配 0 像素命中 | 按 HSV 色相（≈153）+ 饱和度找准星 |
| Android 11+ 不再理会 `FLAG_LAYOUT_NO_LIMITS` 对系统栏的含义 | 覆盖层只有 1080x1794，导航栏区域画不到 | `fitInsetsTypes = 0` |
| 标定页被拉进律匠主页所在 task | 从悬浮面板 NEW_TASK 打开后，底下露出的是主页而不是游戏，截到主页 | `taskAffinity=""` + `excludeFromRecents` + `singleTask` |
| 系统设置 app 强制隐藏第三方悬浮窗 | 悬浮图标/准星在设置页上全不可见（`mForceHideNonSystemOverlayWindow`） | 验证用别的 app 做"游戏"替身；真游戏无此限制 |
| `adb install` 流式装 83MB 包时测试机 USB 掉线 | `device not found` | `adb push` + `pm install -r -g` |
| 相似图案地标自动定位错位 | 两张几乎一样的闹钟卡片，120px patch 匹配到另一张 | 这是设计内行为：用户在本机图上点一下纠正；地标尽量选独一无二的 UI |
| 设备端默认布局取名册排序第一个 | 无 session `active_layout` 时用了「桌面布局」 | 既有行为，与本次无关，记下备查 |

## 构建链（macOS）

- `app/build.gradle.kts` 的 buildPython 改为按平台通配 `.tooling/python/cpython-3.10*`（Windows 取 `python.exe`，
  其它取 `bin/python3`），也接受 `-PbuildPython=` / `LVJIANG_BUILD_PYTHON`；
  `UV_PYTHON_INSTALL_DIR=<repo>/.tooling/python uv python install 3.10` 一条命令装好。
- 加了 Gradle 8.10.2 wrapper（`android/gradlew`），`JAVA_HOME=$(brew --prefix openjdk@17) ./gradlew :app:assembleDebug`
  首次 5 分钟（含自动装 platform-35），增量 5–20 秒。`local.properties` 指向 brew 的 `android-commandlinetools`。

## 悬浮球动态开关（截图/标定时隐藏）

标定 / 截图会把律匠悬浮球一起截进画面，用户可能把它当地标去点。加 `AgentServer.float_icon` op +
`FloatService.setIconHidden`（隐藏态跨图标重建保持）+ PC `AgentClient.set_float_icon` + CLI
`calib float --hide/--show`；ScreenMap probe 截图前自动藏、结束恢复，app 内屏幕标定页本来就在截底图时隐藏。
真机验证（测试机 B / Android 16）：`float_icon(hide)` 后悬浮球从截图消失、`--show` 恢复。

## 跨设备实测（测试机 A 1080x2400 ↔ 测试机 B 1264x2800）

两台真机（Android 13 国产 ROM / Android 16）都装包跑通目标游戏：无障碍 + 代理（protocol 2）+ 悬浮球 + 代理截图。
拿测试机 A 主界面当参照、测试机 B 当目标，用三个位置固定的 HUD 地标（左上/右上/右下三个界面图标）
模板匹配定位（跨分辨率 2400→2800 ~1.17×，匹配分 0.93+），`solve_canvas` 解出测试机 B 画布
`x=0.0005 y=-0.0002 w=0.999 h=1.000` = 恒等（残差 0.2px）。结论：该游戏 HUD 贴边渲染 + 两台宽高比
几乎相同（2.222 / 2.215），内容 1:1，默认布局直接可用。这也端到端验证了地标定位 + 画布解算在真机真游戏上正确。

注：部分国产 ROM（测试机 A）禁 adb 注入触摸（`input tap` 被拒）与写 secure settings（装包去掉 `-g`），
但代理通道走无障碍手势不受限；测试机 B 无此限制。

## 后续

- 真机（作者的挖孔屏）生成正式参照图并提交到 `config/system/layouts/默认布局/_reference.png`。
- 标定页目前只在手机上；PC 端区域编辑器若要"设为参照图"，直接调 `screen_calib.save_reference_image`。
- ScreenMap 在 PC 主窗口没有入口（CLI 足够，非恒等的设备极少）；连接日志里会带「已标定」提示。
