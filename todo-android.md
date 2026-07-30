# 安卓独立执行端迁移 — 进度与下一步

> 最后更新：2026-07-31（Phase 4 进行中：APK 体积优化 + Release 签名基础设施已就位，待实机复验 release 包）

---

## 总体进度一览

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 0：环境 + 骨架 + 悬浮服务 | ✅ 完成 | JDK/SDK/Gradle 便携化、Chaquopy 接入、悬浮窗、Shizuku 通道 |
| Phase 1：三通道 PoC（截图/OCR/点击） | ✅ 完成 | e2e 自检 7 步全绿，检查点 2 已提交（`926d55e`） |
| Phase 2：核心逻辑移植与配置分层 | ✅ 完成 | pydantic 剥离 + 基类继承 + 系统配置 APK 分发 + 布局分发（`1c0b754`） |
| Phase 3：工作流引擎设备端跑通 | ✅ 完成 | DSL 引擎实机执行验证通过（`a3052ec` + `9aa140a`） |
| Phase 4：打包发布与稳定性 | 🚧 进行中 | R8 + 签名基础设施已就位；release 实机复验 + 引导流程待做 |

---

## Phase 0 已完成项

- [x] 便携工具链：JDK 17、Android SDK（platform-tools）、Gradle 8.10.2 全部在 `.tooling/`
- [x] Android 工程骨架：`android/` 目录，`compileSdk 35`、`minSdk 26`、`arm64-v8a`
- [x] Chaquopy 接入：Python 3.10（Chaquopy 仓库最高 cp310）、`srcDir("../../src")` 指向 src-layout
- [x] 设备端依赖矩阵确认：numpy 1.26.2 / cv2 4.5.1 / Pillow 11.0.0 / PyYAML 6.0.1
- [x] 悬浮窗服务（FloatService）+ MainActivity 权限引导页
- [x] Shizuku shell 通道（ShellBridge / ShellService / IShellService.aidl）
- [x] src-layout 重构：`src/lvjiang/` + 根 `pyproject.toml` + 根 `tests/`，已提交 `d2f3278`

---

## Phase 1 详细进度

### 已完成

- [x] **P1-a** 设备端依赖矩阵确认（Py3.10 + cv2/shapely 可用，pyclipper 缺失）
- [x] **P1-b** unclip 去 pyclipper 等价实现（Hausdorff ≤ 2.46px）
- [x] **P1-c** rapidocr 走 PyPI + pystubs 占位包（onnxruntime/pyclipper/shapely）+ 运行时定点替换
- [x] **P1-d** Kotlin OnnxBridge（onnxruntime-android 1.20.0 + 模型随 rapidocr 包进 APK）
- [x] **src-layout 重构** 独立提交 `d2f3278`（784 pytest 全绿）

### 重大架构变更：Shizuku → 无障碍服务

**原因**：Shizuku 无 root 模式必须由 adb 引导启动（本质是 shell uid 的 `app_process` 进程），手机重启一次就失效，要用户重新进开发者选项配对无线调试。这对开发者都困难，对普通用户等于劝退。

**替代方案**：无障碍服务（AccessibilityService）
- `takeScreenshot()` — 整屏截图（Android 11+），返回 RGBA 裸字节
- `dispatchGesture()` — 手势注入（点击/滑动/长按）
- 开启方式：设置里开一次开关，**重启保留**
- 开发期可用 adb 直接写入（`settings put secure enabled_accessibility_services ...`），全自动

**Shizuku 保留为可选高级通道**（`screencap -p` 不受截图节流限制），两者接口形状一致，上层可按可用性择一。

### 代码已就位（未提交）

| 文件 | 职责 |
|---|---|
| `android/.../A11yService.kt` (234 行) | 无障碍服务 + A11yBridge 门面（截图 RGBA / tap / swipe / longPress） |
| `android/.../res/xml/a11y_config.xml` | 无障碍配置：`canTakeScreenshot=true`、`canPerformGestures=true` |
| `android/.../AndroidManifest.xml` | 注册 A11yService + BIND_ACCESSIBILITY_SERVICE 权限 |
| `android/.../strings.xml` | 新增无障碍标签/描述 + 点击回显按钮文案 |
| `src/.../ondevice/a11y.py` (67 行) | A11yBridge Python 门面（`.INSTANCE` 走 Kotlin 单例） |
| `src/.../ondevice/capture.py` (121 行) | 双通道截图：A11yCapture（主，RGBA 直传 numpy）+ ShellCapture（可选） |
| `src/.../ondevice/input.py` (126 行) | 双通道输入：A11yInput（主）+ ShellInput（可选），共享 `_GestureInput` 基类 |
| `src/.../ondevice/shell.py` (73 行) | ShellBridge Python 门面（`.INSTANCE`） |
| `src/.../ondevice/smoke.py` (384 行) | 自检：OCR 5 步 + e2e 7 步闭环（截图→OCR→点击→再截图确认） |
| `src/.../ondevice/onnx_session.py` (70 行) | OrtInferSession → Kotlin OnnxBridge 桥接 |
| `src/.../ondevice/rapidocr_adapter.py` (130 行) | RapidOCR 设备端适配器 |
| `.tooling/enable_a11y.py` (75 行) | adb 自动开启无障碍开关（保留已有条目） |
| `.tooling/run_selftest.py` (104 行) | 装包→触发→轮询报告文件→落盘（报告走文件不走 logcat） |
| `.tooling/probe.py` | 快速诊断探针 |
| `.tooling/await_shizuku.py` | Shizuku 等待脚本（降级为辅助） |
| `.tooling/diag_device.py` | 设备现场诊断 |

### 关键发现与决策

1. **设备 logcat 不可靠**：实测这台 vivo 设备会把普通应用的 Log 整个滤掉（同一 pid 抓到 0 行）。报告改走文件（App 写私有目录，adb 用 `run-as` 读回）
2. **Kotlin `object` 在 Chaquopy 里必须走 `.INSTANCE`**：编译后方法是实例方法，挂在 `INSTANCE` 静态字段上
3. **模型/字典入 assets 可省**：rapidocr 自带 15.44MB 模型随包进 APK，Chaquopy 导入时解压到文件系统
4. **pydantic 是 Phase 2 硬障碍**：`config.py` 顶层 `from pydantic import BaseModel`，设备端没有。`InputBackend` 基类因此不能在设备端导入，`input.py` 暂用独立实现
5. **重装 App 会撤销无障碍绑定**：必须先装包、再开开关

### 待完成

- [x] **P1-e** 构建 + 装包 + adb 开启无障碍 + 跑通 e2e 自检（2026-07-30）
  - 按「先装包、再开开关」顺序执行：`BUILD SUCCESSFUL in 1m 48s` → install Success → 无障碍服务 1s 内绑定
- [x] **P1-f** 三通道闭环验证通过（2026-07-30）
  - e2e 7 步全绿：截图 1260x2800 → OCR#1 2.08s/10 框命中「自检：点击回显」→ dispatchGesture 点击 → OCR#2 0.96s 确认「已生效」
  - 附加项 Shizuku shell 通道 FAILED 属预期（已降级为可选通道，未激活）
  - 报告存 `.tooling/selftest_report.txt`，检查点 2 已提交（`926d55e`）

---

## Phase 2 进展：核心逻辑移植与配置分层

- [x] 解决 pydantic 在设备端的可用性（2026-07-30）
  - 方案：**剥离 pydantic 换 dataclass**（而非找 wheel）。理由：v2 核心是 Rust 扩展
    pydantic-core，Chaquopy 无法安装；v1 纯 Python 能装但双版本 API 不一致是长期坑；
    使用面只有 config.py 一处（4 个模型 + ge/lt 约束），__post_init__ 完全等价
  - 卸载 pydantic 后全量 pytest 仍全绿；pyproject 已移除依赖
- [x] `CaptureBackend` / `InputBackend` 基类设备端可继承（2026-07-30）
  - `ondevice/input.py` 改为继承 InputBackend，删除写死的延迟常量，
    DelayConfig 经 `_inject_delay_config` 统一注入（与 PC 端同源）
  - 设备端 e2e 自检重跑全绿，新继承链实机验证通过
- [x] 修复重装后无障碍不重新绑定：`enable_a11y.py` 改为「先摘除再写回」强制刷新
  （重装后 settings 残留条目不触发绑定，vivo 实测）
- [x] 系统配置随 APK 分发（2026-07-30）
  - `build.gradle.kts` 加 Sync 任务：`config/system/` → APK assets（构建时自动同步）
  - `App.kt` 加 `syncSystemConfig()`：首次启动 / APK 升级后解压到 `filesDir/lvjiang/config/system/`
    （用 versionCode 做 stamp，同版本重复启动跳过）
  - `constants.py` 的 `_project_root()` 在安卓端返回 `$HOME/lvjiang`
    （Chaquopy 的 `HOME` = `/data/user/0/com.lvjiang.app/files`）
  - 实机验证：`scenes.yaml` + `workflows/`（10 个文件）+ `yysls/` 全部到位
- [x] 重装后 e2e 自检再次全绿（2026-07-30 第二轮）
  - pm install 绕过安装确认框 → enable_a11y 强制刷新绑定 → e2e 13s 完成
  - 截图 1260x2800 / OCR 1.72s / 点击生效确认 3.15s
- [x] 场景定义 / 区域配置的设备端加载（2026-07-30）
  - `target=config` 自检 4 步全绿：constants 路径解析 → scenes.yaml 解析（16 个场景文件）
    → workflows.yaml 解析（8 个工作流文件）
- [x] 工作流 YAML 的设备端加载验证（2026-07-30）
  - `workflows/*.wf` 已随 assets 解压，`load_yaml` 可正确解析
- [x] **Phase 2 提交**（2026-07-30，`1c0b754`）
  - 含：constants.py + App.kt + build.gradle.kts + smoke.py（config 自检）+ todo-android.md

## Phase 3 完成：工作流引擎设备端跑通

- [x] loguru + lark 加入设备端依赖（2026-07-30）
  - `build.gradle.kts` pip 块新增 `loguru==0.7.3` + `lark==1.3.1`
  - 工作流引擎 + scene_registry + grammar parser 全部可用
- [x] 布局文件随 APK 分发（2026-07-30）
  - `build.gradle.kts` 新增 `syncLayoutConfig` Sync 任务：`config/local/layouts/手机直控.json` → APK assets
  - `App.kt` `syncSystemConfig()` 同时解压系统配置和布局文件到 `filesDir/lvjiang/config/local/layouts/`
- [x] 设备端工作流引擎装配（2026-07-30）
  - `workflow_runner.py`：把 A11yCapture / A11yInput / OCREngine / Layout / WorkflowEngine 串起来
  - 提供 `create_engine()` 和 `run_workflow(wf_name)` 两个入口
- [x] 工作流引擎自检目标（2026-07-30）
  - `smoke.py` 新增 `target=workflow`：4 步验证（loguru → 布局加载 → .wf 解析）
  - 验证 DSL 语法解析器在设备端可用
- [x] 实际执行测试工作流（2026-07-30）
  - `smoke.py` 新增 `target=run`：3 步验证（引擎创建 → 工作流执行 → 变量验证）
  - `device_smoke_test.wf`：纯计算测试（变量赋值 + for 循环 + log，不点击游戏）
  - 实机验证 3 步全绿：count=5.0, msg='hello from device'
- [x] **Phase 3 提交**（2026-07-30，`a3052ec` + `9aa140a`）
  - versionCode bump 到 2 触发配置重新解压
  - workflow_runner.py 修复 OCR 初始化

## Phase 4 待办：打包发布与稳定性

- [x] **悬浮服务作为任务入口**（2026-07-31）
  - `task_runner.py`（335 行）：任务生命周期管理，对外四个 JSON 接口
    （`list_tasks` / `start_task` / `stop_task` / `get_status`）；状态机
    idle→running→done/failed/stopped，日志环形缓冲 200 行，引擎跳任务复用
  - `PyBridge.kt`：Chaquopy 调用唯一入口，`ensureStarted` 幂等化消除 Python.start 竞态
  - `FloatService.kt`（163 → 437 行）：任务面板 + 状态色图标 + 1s 轮询（仅运行中）
  - 停止走引擎已有的 `stop_check` 协作式回调，不强杀线程
  - 实机验证：面板列出 7 个任务 → 点「设备端冒烟测试」→ 状态行「已完成」+ 日志两行 + 图标转蓝
- [x] **异常恢复：无障碍掉线检测与设置页引导**（2026-07-31）
  - 系统不开放自动重连 API，可做的是尽早拦住并把人送到开关页：
    `MainActivity` 新增无障碍状态行 + `btn_a11y` 跳转；`FloatService.launchTask`
    启动前先查；`task_runner.start_task` 再查一道
  - `A11yCapture` 截图重试：节流退避 3 次 × 0.4s；掉线是硬故障，不重试直接报清楚
- [x] **APK 体积优化**（2026-07-31）
  - `build.gradle.kts` release 开 `isMinifyEnabled = true` + `isShrinkResources = true`
  - 新增 `android/app/proguard-rules.pro`：保留 Chaquopy 反射访问的 Kotlin object
    （A11yBridge / OnnxBridge 的 INSTANCE 字段与公开方法）、服务构造器、AIDL Stub、
    ONNX Runtime JNI
  - `syncSystemConfig` / `syncLayoutConfig` 改挂到 `preBuild`：AGP 8 + Gradle 8.10
    strict task validation 抓 `generateReleaseLintVitalReportModel` 对 assets 目录的
    隐式依赖，原 `generate*Assets` 匹配模式不够（lint 不走这条命名规则）
  - debug 74.03 MB → release 70.01 MB（约 5%）；大头是 Chaquopy 打包的 Python
    运行时 + rapidocr 模型 + onnxruntime-android，这些 assets R8 动不了，进一步
    压缩需要裁剪 Python 包或换 onnxruntime-android 的 slim 产物
- [x] **Release 签名构建基础设施**（2026-07-31）
  - `build.gradle.kts`：从 `android/keystore.properties` 读取 storeFile / 密码，
    文件存在则签 release，不存在则走 debug 签名兜底（`assembleRelease` 始终能跑）
  - `.gitignore` 新增 `android/keystore.properties` / `*.jks` / `*.keystore`，
    避免密钥材料入库
  - 用户侧待办：`keytool -genkeypair -v -keystore lvjiang.jks -keyalg RSA \
    -keysize 2048 -validity 10000 -alias lvjiang`，再把路径与密码写入
    `android/keystore.properties`（待补 `android/README-signing.md` 说明文档）
- [ ] 用户引导流程（首次启动 → 开无障碍 → 悬浮窗权限 → 开始使用）
- [ ] release 包实机复验（当前无障碍绑定在 vivo 上被厂商策略拦下，需手动确认一次）

### 本轮新增验证设施

| 文件 | 作用 |
|---|---|
| `tests/core/test_task_runner.py` (18 用例) | 状态机 / JSON 协议 / 并发互斥 / 真引擎跑 .wf，把 DSL 语法错拦在 PC 上 |
| `smoke.py target=task` (5 步) | 设备端走悬浮图标同一条路的自检，不靠手点 |
| `.tooling/verify_panel.py` | 面板 UI 验证：坐标从 dumpsys/uiautomator 现算，不写死 |
| `.tooling/run_selftest.py --no-install` | 同一 APK 连跑多个 target，避免反复装包 |
| `.tooling/run_selftest.py --release` | 用 release APK 装包跑自检，验证 R8 没砍反射 |
| `android/app/proguard-rules.pro` | R8 keep 规则：Chaquopy 反射访问面 + 服务构造器 + AIDL + ONNX |

### 实机验证踩到的两个坑

- **写死坐标会点到应用外**：`MainActivity` 的状态文本会被自检报告撑长，把下方按钮
  全部往下推（启动悬浮图标从 968 移到 1091）。第一下点空之后第二下落到桌面，
  误开了用户的闹钟应用。现已改为从 dump 现算，找不到就中止、绝不盲点。
- **悬浮窗在 uiautomator dump 里不存在**：它是 `FLAG_NOT_FOCUSABLE`，不是 active
  window。只能从 `dumpsys window` 的 `type=2038 ... frame=` 取；且 `LayoutParams`
  里写的 y 不等于实际 y（`FLAG_LAYOUT_NO_LIMITS` 下 40/400 实测落到 40/547）。

---

## 下一步操作（按顺序）

1. **release 包实机复验**：手动确认一次「设置 → 无障碍 → 律匠自动操作」开关后，
   跑 `run_selftest.py task --release`，确认 R8 没把 Chaquopy 反射面砍掉
2. **首次启动引导流程**（权限一步步过：无障碍 → 悬浮窗 → 主界面）
3. **真实业务工作流上机**（自动调律 / 装备分析，需游戏环境）
4. **正式签名**：生成 keystore + 写 `android/README-signing.md` 说明文档

