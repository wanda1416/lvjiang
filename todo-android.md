# 安卓独立执行端迁移 — 进度与下一步

> 最后更新：2026-07-31 傍晚（装备分析上机测试已启动：release APK v10 出包完成但**尚未安装**，
> 阻塞在设备无障碍未绑定，详见「上机测试执行记录」一节，新会话可照该节直接接上）
>
> 上一次：六主题变更已提交并推送至 `20acde0`；装备分析上机前依赖面预检全绿，坐标绑定风险已排除

---

## 总体进度一览

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 0：环境 + 骨架 + 悬浮服务 | ✅ 完成 | JDK/SDK/Gradle 便携化、Chaquopy 接入、悬浮窗、Shizuku 通道 |
| Phase 1：三通道 PoC（截图/OCR/点击） | ✅ 完成 | e2e 自检 7 步全绿，检查点 2 已提交（`926d55e`） |
| Phase 2：核心逻辑移植与配置分层 | ✅ 完成 | pydantic 剥离 + 基类继承 + 系统配置 APK 分发 + 布局分发（`1c0b754`） |
| Phase 3：工作流引擎设备端跑通 | ✅ 完成 | DSL 引擎实机执行验证通过（`a3052ec` + `9aa140a`） |
| Phase 4：打包发布与稳定性 | 🚧 进行中 | release 复验 + 首启引导 + 正式签名 + 江湖号令实机跑通 + 悬浮面板打磨；剩其余业务流上机（`equip_analysis` 上机已启动，release v10 待安装） |

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

### 代码已就位（已随检查点 2 `926d55e` 提交）

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
  - 用户侧待办：已完成（见下条）
- [x] **正式签名**（2026-07-31）
  - `android/lvjiang.jks` 已生成（RSA 2048，有效期 10000 天，别名 lvjiang，
    随机 24 位密码写入 `keystore.properties`，两者均在 .gitignore）
  - `android/README-signing.md`：重建命令、备份提醒、签名变更需卸载重装
  - apksigner 验证通过：Signer CN=lvjiang（不再是 debug 兜底）
  - ⚠ 设备上现装的是 debug 签名的 release 包，下次装正式签名包必须先卸载
    （会丢无障碍授权，需重新手动开），宜合并到下次上机验证时一起做
- [x] **release 包实机复验**（2026-07-31）
  - 新增 `SelfTestProvider.kt`：release 包不可调试，`run-as` 读报告直接拒绝
    （"package not debuggable"），改走 ContentProvider 只读通道：
    `adb shell content read --uri content://com.lvjiang.app.selftest/log`，
    openFile 里校验调用方必须是 shell/root uid；`run_selftest.py` 自动降级
  - `task` 自检 5 步全绿：任务清单 7 项 → start_task ok → 0.7s 跑完
    device_smoke_test → 终态 done → 互斥校验通过，R8 没砍 Chaquopy 反射面
- [x] **用户引导流程**（2026-07-31）
  - `activity_main.xml` 重构：引导区（权限清单 ✅/⬜ + 一颗主按钮 + 每步提示行）
    在上，Phase 0 以来的开发者自检按钮全部收进默认折叠的「高级」面板
  - `MainActivity` 引导状态机：主按钮永远指向第一个未完成项
    （无障碍 → 悬浮窗 → 启动悬浮图标 → 就绪）；通知权限不单独设步，
    启动悬浮图标时顺带申请；`FloatService.isRunning` 静态标志供引导页判断
  - 自检模式（`--es selftest`）隐藏引导区、展开高级面板：布局回到实机
    验证过的旧版形态，e2e 的 OCR 目标不受引导文案干扰
  - 主题换 `DayNight.NoActionBar` + `fitsSystemWindows`：targetSdk 35 强制
    edge-to-edge，原先内容顶进状态栏、系统 ActionBar 叠在自带大标题上
  - 实机验证：三项权限全绿时主按钮一点即启动悬浮图标并翻成「运行中」；
    e2e 回归三通道闭环全部通过（OCR 12 框 → 命中目标 → 点击落地）
  - **e2e 回归抓到两处 R8 反射面破口**（之前 task 目标没走到这两条路径）：
    `OnnxOutput` 字段被砍（`'q' object has no attribute 'data'`）、`ShellBridge`
    类名被混淆（`No module named 'com'`），proguard-rules.pro 已补 keep 并复验
- [x] versionCode 4 已上机（config 变更随之重新解压）
- [x] **江湖号令实机跑通**（2026-07-31）
  - 依赖面核查全绿：`activity_jianghu.wf` 纯 click/drag + OCR scan（无图像匹配、不需
    Shizuku），8 个场景 YAML + `默认布局.json`（18 场景，activity_jianghu 18 region 零缺失）
    随 config/system 分发，配置重构后新机无需 local 补齐
  - versionCode 升 5，卸载重装正式签名包，中文 OCR 实机 0.97-1.0
  - 悬浮面板点「江湖号令」→ 游戏内完整走完刷新→分发动作→退回首页，用户确认可用
- [x] **悬浮面板 UI 打磨**（2026-07-31，versionCode 6→8）
  - **紧凑化**：状态/日志/按钮字号下调，行高从 dp(30) 再砍到 dp(15)（Button 默认内
    边距显式压到 setPadding(12,2,12,2)），滚动区 260→170dp，横屏一屏能放下
  - **启动即收圆点**：`launchTask` 成功后 `closePanel()` 只留一个圆点，图标颜色反映
    运行态（绿/蓝/红/橙），不遮挡游戏；面板关了仍继续轮询给图标上色
  - **面板贴近图标**：新增 `anchorPanelToIcon()`，面板据图标当前 x/y 定位——图标在
    左半屏则面板贴右侧、右半屏贴左侧，纵向与图标顶端对齐并按屏高 clamp 防越界
- [x] **悬浮清单接暴露层**（2026-07-31）
  - 病灶：设备端 `task_runner.list_tasks()` 直接用发现层全集，绕过了 `workflows.yaml`
    的 exposed/overrides——冒出 `auto_purchase_xinde` 等原始 id、还混进 `auto_tuning`/
    `device_smoke_test` 等不该给日常用户看的脚本
  - 修复：把桌面 `run_control` 的暴露逻辑抽成 `discovery.list_exposed_scripts()`，
    桌面下拉与设备端悬浮面板共用同一套（避免两处各写一份）；`list_tasks()` 改用它，
    并单独把 `device_smoke_test` 补到末尾（smoke.py 自检链路硬依赖它出现在清单中）
  - 结果：面板只展示 5 个暴露脚本（含中文显示名：当前装备分析/单次调律/自动购买心法/
    江湖号令/盟主争锋）+ 末尾冒烟测试项

- [x] **配置架构重构**（2026-07-31，versionCode 8→9）
  - `DelayConfig` → `InputSimConfig`（去 `custom` 字段）+ `DelayParam`（原 `CustomDelay`）
  - `input_delay` 从 session.json 迁到 `config/system/app.yaml`（system/local 分层：
    开发者放 system，用户改动提交到 local，随 APK 分发）
  - `input_delay` 拆为两个独立 dict：`input_simulation`（输入模拟参数）+
    `delay_params`（命名等待参数）
  - 引擎构造器参数 `delay_config` 拆为 `input_sim` + `delay_params`
  - Mixin 字段 `_delay.custom` → `_delay_params`，`_delay.jitter/offset` → `_input_sim`
  - 注入方法 `_inject_delay_config` → `_inject_input_sim`
  - settings_dialog 读写改 app.yaml；session.json 的 input_delay 节点已删除
  - workflow_runner.create_engine 从 app.yaml 加载（修设备端 step_interval 缺失 bug）
  - 受影响测试全部更新（10 个测试文件 + 3 处源码注释）
  - ruff + pytest 全绿回归通过

- [x] **设备端插件加载缺失修复 + 自动调律暴露**（2026-07-31，versionCode 9→10）
  - 病灶：桌面由 `__main__.py` 按 `-reg yysls` 加载插件，设备端入口是 Chaquopy 直连
    `task_runner`/`workflow_runner`，**从不加载插件**，导致两类静默故障：
    1. `implementations.list_workflows()` 为空 → 发现层的 class 来源全空 →
       `single_tuning`/`auto_tuning` 退化解析成 `config/system/workflows/` 下的
       **同名旧 DSL 文件**（`auto_tuning.wf` 仅 879 字节，只做背包遍历，无潜力判定/
       材料检查/说明文档，与类实现完全不是一回事）
    2. 燕云 `builtin_modules` 未导入 → `to_equipment` 等未注册 →
       `single_tuning.wf:55` 调用即报未知函数，**设备端必然失败**（此前未实机跑过，
       故一直没暴露）
  - 修复：新增 `core/ondevice/plugins.py` 的幂等 `ensure_loaded()`（登记
    `DEVICE_APPS=("yysls",)`），在 `list_tasks()` / `_resolve_task()` /
    `create_engine()` 三个入口调用。插件模块顶层刻意不 import PyQt6（builder 内
    延迟导入）、`register_hooks` 只存不调 tab/menu builder，故设备端加载安全
  - `auto_tuning` 加 `DISPLAY_NAME = "自动调律"` 并入 `workflows.yaml` 的 exposed；
    暂不定义 `PARAMETERS`——其配置面（部位多选 + 每规则玩法多选 + 全局开关）超出
    现有 select/number 参数 schema 的表达力，设备端先按内置默认配置运行
  - 无 run_ctx 注入时的默认行为已逐条查实可用：`TuningContextMixin.ctx` 惰性建全默认
    实例 → `selected_slots=None` 即全部 8 部位；`judge_configs=None` →
    `_ensure_judge_config()` 读插件 session，设备端该文件不存在时 `get_section`
    返回空 dict → 回退全部规则默认判定；`rule_judges` 空列表无影响（契约注明
    「暂未消费，仅记录」）；文档目录 `PROJECT_ROOT/logs/tuning` 在设备端即
    `$HOME/lvjiang/logs/tuning`，可写且创建失败只警告不中断
  - 验证：`list_tasks()` 裸进程实测 `single_tuning`/`auto_tuning` 的 source 由
    `wf` 变 `class`、`to_equipment` 已注册、`PyQt6 not in sys.modules`；
    ruff + pytest（exit 0）全绿

### 本轮改动文件（已提交并推送）

按主题拆成 6 个提交，`master` 与 `origin/master` 已同步（最新 `20acde0`）：

| 提交 | 主题 |
|---|---|
| `54fb43e` `feat(tuning_rules)` | 仅首词条逐规则化 + 首词条方向比较 + 潜力日志改进 |
| `ceb447f` `feat(rules_editor)` | 调律门槛迁基础配置页 + 行为页仅首词条列 + 布局优化 |
| `9ecb626` `fix(ondevice)` | 设备端插件加载缺失修复 + 自动调律暴露 |
| `6e1fe9c` `chore(android)` | versionCode 9→10 + todo 记录插件加载修复 |
| `f2c28bb` `chore(rules-data)` | 会心大小号玩法与首词条数据更新 |
| `20acde0` `fix(app)` | 退出时先停 loguru 异步写入线程，避免退出挂起 |

涉及文件：

| 文件 | 改动 |
|---|---|
| `src/lvjiang/workflows/discovery.py` | 新增 `list_exposed_scripts()` + `_load_exposure()` 暴露层 |
| `src/lvjiang/core/ondevice/task_runner.py` | `list_tasks()` 改走暴露层 + 补冒烟测试项 |
| `src/lvjiang/ui/run_control.py` | `_load_workflow_configs` 改用共享暴露层，删重复逻辑 |
| `android/.../FloatService.kt` | 紧凑化 + 启动收圆点 + `anchorPanelToIcon` 面板贴图标 |
| `src/lvjiang/core/ondevice/plugins.py` | 新增：设备端幂等插件加载 `ensure_loaded()` |
| `src/lvjiang/core/ondevice/workflow_runner.py` | `create_engine` 先 `ensure_loaded()` |
| `src/lvjiang/apps/yysls/.../auto_tuning.py` | 加 `DISPLAY_NAME`（暂不加 PARAMETERS） |
| `config/system/workflows.yaml` | exposed 加 `auto_tuning` |
| `android/app/build.gradle.kts` | versionCode 4→10（改了 config/system，必须 bump 才重解压） |

### 本轮新增验证设施

| 文件 | 作用 |
|---|---|
| `tests/core/test_task_runner.py` (18 用例) | 状态机 / JSON 协议 / 并发互斥 / 真引擎跑 .wf，把 DSL 语法错拦在 PC 上 |
| `smoke.py target=task` (5 步) | 设备端走悬浮图标同一条路的自检，不靠手点 |
| `.tooling/verify_panel.py` | 面板 UI 验证：坐标从 dumpsys/uiautomator 现算，不写死 |
| `.tooling/run_selftest.py --no-install` | 同一 APK 连跑多个 target，避免反复装包 |
| `.tooling/run_selftest.py --release` | 用 release APK 装包跑自检，验证 R8 没砍反射 |
| `android/app/proguard-rules.pro` | R8 keep 规则：Chaquopy 反射访问面 + 服务构造器 + AIDL + ONNX |
| `scripts/manual-tests/preflight_device_workflows.py` | 上机前预检（DSL 侧）：走设备端真实发现路径 + session 活动布局，只解析不执行 |
| `scripts/manual-tests/preflight_class_refs.py` | 上机前预检（类实现侧）：AST 抽 Python 字面量坐标调用比对布局 |
| `tests/workflows/test_system_wf_refs_gate.py` (13 用例) | **CI 门禁**：系统布局 × 全部系统 .wf 的引用绑定，漏绑当场红 |
| `WorkflowEngine.validate_only()` | 只解析 + 两道静态校验的正式 API，判据与真执行共用 `_load_and_validate` |

### 上机前依赖面预检（2026-07-31，装备分析上机前置）

江湖号令上机前是靠「依赖面核查全绿」才敢点第一下的。装备分析按同一标准补了两条
校验路径，都在 PC 上跑、零点击 —— 实机失败的代价是游戏已经被点到别处去了。

- **DSL 侧**：`preflight_device_workflows.py` 按设备端同一条路径（`ensure_loaded` →
  `_default_layout_name` → `_load_layout`）装配引擎，再调 `WorkflowEngine.validate_only()`。
  实测 6 项中 4 个 DSL 脚本（`equip_analysis` / `auto_purchase_xinde` /
  `activity_jianghu` / `mengzhuzhengfeng`）**全部通过，失败 0 项**
- **类实现侧**：静态引用收集器只认 DSL 语法，`auto_tuning` / `single_tuning` 在 Python
  里用 `self.click_region("场景", "key")` 引坐标，是校验盲区。`preflight_class_refs.py`
  用 AST 抽出调用点（`self.CONST` 形式经类属性求值）再比对布局：`auto_tuning`
  21 组 + `single_tuning` 17 组去重引用，**缺失 0 处**

「只校验」已从短路私有 `_exec_body` 的 hack 沉成正式 API：`WorkflowEngine.validate_only()`
与 `_execute_dsl` 共用 `_load_and_validate`（解析 + import 链 + 两道校验），因此不会
出现「预检放过、上机仍炸」的判据偏差。

**已进 CI 门禁**：`tests/workflows/test_system_wf_refs_gate.py` 对「系统布局 × 全部系统
.wf」参数化跑 `validate_only`（12 个 .wf × 1 个布局 + 1 项清单非空断言）。固定只读
`config/system` 层：随 APK 分发到设备端的就是这一层，且不受本机 `config/local` 影子
文件影响。`_` 前缀的编辑器临时脚本（`_editor_run.wf` / `_recorded.wf` / `_testwf/`）
不入门禁 —— 它们随上一次编辑器操作变化，不是分发物。门禁有效性已反向验证：
拿空布局跑 `equip_analysis.wf` 报「12 处引用在当前布局中找不到」，不是空跑通过。

逐场景复核关键 key 的结果：

| 场景 | 绑定量 | 复核结果 |
|---|---|---|
| `equip_weapon_detail` | 16 区域 | `more_func` ✅，`SCAN_FIELDS` 8 字段全绑 |
| `equip_armor_detail` | 17 区域 | 同上 |
| `equip_tune_detail` | 23 区域 | `back` / `reset_tune` / `reset_confirm` / `reset_confirm_2` ✅ |
| `bag_equip_detail` | 36 区域 + panel `bag_grid` | `back` ✅，8 个部位 key 全绑 |

**共同盲区**：场景参数非字面量时两条路径都只校验到场景一级 —— 即 DSL 的
`click [bag_equip_detail].$slot` 与类实现的 `click_region(GRID_SCENE, slot)`（slot 是
循环变量）。`equip_analysis` 用到的 8 个部位 key 已逐个查过绑定，这条路是干净的。
（曾据此误判 `more_func` 缺失 —— 它在 `equip_weapon_detail` / `equip_armor_detail` 上，
而 `auto_tuning` 是对动态变量 `detail_scene` 点它，AST 整条跳过。报「缺失」前必须先
确认引用的真实场景。）

顺带查实：布局已从 `config/local/layouts/` 迁到 `config/system/layouts/`（单份
`默认布局.json`），随 `syncSystemConfig` 分发，`syncLayoutConfig` 任务已并入，分发链自洽。

### 实机验证踩到的坑

- **写死坐标会点到应用外**：`MainActivity` 的状态文本会被自检报告撑长，把下方按钮
  全部往下推（启动悬浮图标从 968 移到 1091）。第一下点空之后第二下落到桌面，
  误开了用户的闹钟应用。现已改为从 dump 现算，找不到就中止、绝不盲点。
- **悬浮窗在 uiautomator dump 里不存在**：它是 `FLAG_NOT_FOCUSABLE`，不是 active
  window。只能从 `dumpsys window` 的 `type=2038 ... frame=` 取；且 `LayoutParams`
  里写的 y 不等于实际 y（`FLAG_LAYOUT_NO_LIMITS` 下 40/400 实测落到 40/547）。
- **vivo 开始拦 adb 写无障碍开关**（2026-07-31 起）：`settings put` 写入成功但
  系统不绑定（dumpsys 无命中），必须在设置页手动开一次；开过之后重启保留，
  但重装 APK 仍会撤销。`run_selftest.py` 的自动刷绑定在这台设备上已不可靠。
- **静默装包也被拦**：`adb install` / `pm install` 都弹确认框（INSTALL_FAILED_ABORTED），
  需要在手机上点「继续安装」。
- **重复 `am start` 同一 Activity 不触发自检**：目标已在栈顶且 intent 匹配时，
  AM 只报「delivered to top-most instance」但 onCreate/onNewIntent 都不走
  （standard launchMode 无 SINGLE_TOP flag）。先 BACK 销毁实例再 start 即可，
  不必 force-stop（那会连无障碍服务一起杀掉）。
- **R8 反射面要 keep 到「返回类的字段」粒度**：keep 了 OnnxBridge 的方法不等于
  keep 它返回的 OnnxOutput；Python 侧 `from com.lvjiang.app import X` 的每个 X
  及其返回类型都必须整类 keep（含 `<fields>`）。task 目标跑绿不代表反射面
  完整，e2e 这种走全链路的目标才是 release 复验的有效判据。
- **覆盖安装（`pm install -r`）不撤销无障碍授权**：2026-07-31 实测两次覆盖安装
  后无障碍/悬浮窗/通知全部保留，且未弹确认框；之前「重装撤销授权」的记录
  针对的是卸载重装场景。悬浮服务会被杀掉，需重新点「启动悬浮图标」。
- **上机必须出 release 包，不能图省事用 debug 包**：设备上装的是 release 签名包，
  `adb install -r` 一个 debug 包会直接 `INSTALL_FAILED_UPDATE_INCOMPATIBLE`
  （signatures do not match）。签名冲突无法用参数绕过，唯一的原地升级办法就是用
  同一套 keystore 出 release。**别为了装 debug 包去卸载** —— 卸载会连带清掉设备端
  `config/local`（session、设备上标定的布局），而 release 包不可调试、`run-as` 用不了，
  卸载前根本没法备份那批数据，属于不可逆动作。

---

## 上机测试执行记录（2026-07-31 傍晚，中断在安装前）

本节是**断点续跑清单**，新会话照着往下走即可，不用重新摸环境。

### 已完成

| 步骤 | 结论 |
|---|---|
| 设备连接 | `192.168.0.103:5555`，vivo V2415A（`product:PD2415`），无线 adb 在线 |
| 查已装版本 | `versionCode=8`、release 签名（`run-as` 报 not debuggable），装于 11:22:04 |
| 判定须重装 | 代码侧 `versionCode=10`；v8 缺 `9ecb626` 的 `ensure_loaded()` 修复，`to_equipment` 会报未知函数，v8 上跑 `equip_analysis` 必失败 |
| debug 包试装 | ❌ `INSTALL_FAILED_UPDATE_INCOMPATIBLE`，签名不匹配（详见踩坑最后一条） |
| 出 release 包 | ✅ `BUILD SUCCESSFUL in 3m 26s`，产物 `android/app/build/outputs/apk/release/app-release.apk`（71.8MB，17:52:18，versionCode 10） |
| 查游戏前台 | `com.netease.yyslscn/com.netease.game.MessiahNativeActivity` 在前台 |
| 查 `equip_analysis` 入口 | `workflows.yaml` exposed **第一项**，显示名「当前装备分析」 |
| 查脚本前置 | 首行 `call nav_main_to_equip()` → 游戏必须停在**主界面**（游戏世界画面，不能在任何子菜单里） |

### 未完成 / 阻塞点

1. **APK 未安装**（包已就绪）。装包会在手机上弹「继续安装」确认框，需要手动点。
2. **无障碍服务未绑定**：`settings get secure enabled_accessibility_services` 当前只有
   `hello.litiaotiao.app/.LttService`（李跳跳）和 `com.wtkj.app.clicker/.service.ClickerService`
   （点击器），**没有 `com.lvjiang.app/.A11yService`**。截图与点击两个通道都依赖它，不开
   跑不了。vivo 从 2026-07-31 起拦 adb 写这个开关（写入成功但系统不绑定），需在
   「设置 → 无障碍」里手动开一次「律匠自动操作」。
   - `.tooling/enable_a11y.py` 已核对过是**追加**语义（`others` 列表保留原有条目），
     不会关掉李跳跳和点击器，可以先跑它试一次，不成再手动开。

### 断点续跑命令

```powershell
$adb = ".tooling\android-sdk\platform-tools\adb.exe"

# 1. 装包（手机上点「继续安装」）。包已存在则不必重新构建
& $adb install -r android\app\build\outputs\apk\release\app-release.apk

# 2. 开无障碍（先自动试，输出「服务已绑定」才算成功；失败就去设置页手动开）
.venv\Scripts\python.exe .tooling\enable_a11y.py

# 3. 校验：应能看到 com.lvjiang.app 命中
& $adb shell settings get secure enabled_accessibility_services

# 需要重新出包时（改了 src/ 下的 Python 代码就要）：
#   pwsh -NoProfile -File .tooling\gradle-bg.ps1 :app:assembleRelease
#   日志 .tooling/gradle-bg.log，轮询 BUILD SUCCESSFUL，全量约 3.5 分钟
```

随后：启动律匠 App → 点「启动悬浮图标」（覆盖安装会杀掉悬浮服务，必须重点一次）
→ **把游戏切回主界面** → 悬浮面板选「当前装备分析」→ 跑。

观察重点见「下一步操作」第 1 项。

---

## 下一步操作（按顺序）

1. **`equip_analysis` 实机验证**（🚧 已启动，卡在安装 + 无障碍，见上节断点续跑清单）：
   读装备详情 + OCR 词条，属识别类，风险低，宜先跑。坐标绑定风险已由上机前预检排除，
   剩下的只有运行期问题：
   - `to_equipment` 是插件 builtin，靠 `9ecb626` 的 `ensure_loaded()` 修复才在设备端可用，
     这次是它的**首次真实验证**
   - OCR 词条识别在手机分辨率下的准确率未验过，看 `collect session.equipped` 的输出对不对
   - 建议先只跑这一个，不要与调律链路混在一起
2. **桌面下拉回归确认**（阻塞项，等用户表态）：已查实事实如下，差异不会报错，纯属误导：
   - `run_control._load_workflow_configs` 直接拿 `list_exposed_scripts()` 填下拉，所以
     桌面**必然**多出一项「自动调律」，与调律 Tab 并列成第二个入口
   - 调律 Tab 是在 `configure` 回调里注入 `TuningRunContext`（部位/规则配置/开关/
     `doc_username`）；下拉这条路不走 `configure`，`run_ctx` 为 None → `self.ctx` 惰性
     建全默认实例，即全部 8 部位 + 回退插件 session 的规则判定，且 `doc_username=""`
     （调律说明文档缺操作用户名）
   - `TuningRunContext` 每个字段都有兜底，所以不会崩 —— 问题是用户分不清两个入口的区别
   - exposed 是双端共用的，若不希望桌面出现这个入口，需把暴露层拆成双端各一份
3. **调律参数 UI（后置）**：待自动调律实机跑通后再做。两条路线：
   - 扩 `PARAMETERS` schema 加 `multiselect`/`bool`，双端按元数据动态渲染表单（桌面
     `main_window._rebuild_param_panel` 已是这个模式）；配套需 `list_tasks()` 带出
     `parameters`（当前丢了）、`PyBridge.startTask` 加参数（当前硬传 `""`）、
     `auto_tuning.run()` 从 `get_variable` 组装 `TuningRunContext`
   - Android 原生调律配置页写设备端 `config/session/yysls/session.json`，
     走 `_ensure_judge_config` 已有的 session 回退路径（可持久化，但要重造规则表编辑器）

### 待 ① 跑通后才推进的其余上机项

- **自动调律（`auto_tuning`）**：已按内置默认配置暴露（全部 8 部位 + 全部规则默认
  判定），含滚动校验 + 状态机，链路最长，实机可能踩背包滚动、详情刷新延迟等
  既有坑，需重点观察；`single_tuning` 修了插件加载后才第一次真正可运行
- **盟主争锋（`mengzhuzhengfeng`）、自动购买心法（`auto_purchase_xinde`）**：
  纯点击/识别类，可仿江湖号令快速验证；预检已全绿

### 遗留待决（与 Phase 4 上机不相干）

- **仓库已分叉：`master` ahead 8 / behind 1**（截至 2026-07-31 傍晚）。本地 8 个未推：
  `cd32c46` PyInstaller 打包 → `ab1901c` adb 子进程 CREATE_NO_WINDOW → `0b1286b` 定音词条
  全量池匹配 → `e499166` 内置 adb 随包分发 → `80dba3f` 顶档/普通条件结构演进 →
  `e1d3dbc` 调律说明文档同步 → `25d9d4c` `validate_only` + 引用门禁 + 上机预检脚本 →
  `cef0020` gitignore 补 mypy/ruff 缓存。远端有 1 个未合入：`feb7518` fix `_wait_for_threads`
  排除 `_DummyThread`。**推送前必须先处理这个分叉**（rebase 或 merge，由用户定）。
- 当前工作区未提交：`tuning_rules/` 下 3 个 yaml + `tests/yysls/test_huixin_judge.py`
  （属并行进行的调律规则改动，不是上机相关）+ 本文件。
- `.git-mypy-base/` worktree （detached HEAD `33c1baf`）**仍在**，是做 mypy 基线对比时建的，待清：
  `git worktree remove --force .git-mypy-base`
- **CI 的 mypy 门禁在远端已经是红的**：裸 `python -m mypy` 报 2 个错（`engine/core.py:347`
  的 `workflow._engine = self`、`ondevice/workflow_runner.py:151`），在 `origin/master` 与当前
  HEAD 都存在，是既有存量、非新引入。注意给 mypy 传路径参数会**覆盖**
  `[tool.mypy] files` 清单（`mypy src/lvjiang` 报 145 错，不是门禁基线）；同理 ruff 的
  门禁范围只是 `src tests`，加上 `scripts` 会多出 42 个既有错。

