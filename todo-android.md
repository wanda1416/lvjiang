# 安卓独立执行端迁移 — 进度与下一步

> 最后更新：2026-07-30（Phase 2 进行中：pydantic 剥离 + 基类继承已实机验证）

---

## 总体进度一览

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 0：环境 + 骨架 + 悬浮服务 | ✅ 完成 | JDK/SDK/Gradle 便携化、Chaquopy 接入、悬浮窗、Shizuku 通道 |
| Phase 1：三通道 PoC（截图/OCR/点击） | ✅ 完成 | e2e 自检 7 步全绿，检查点 2 已提交（`926d55e`） |
| Phase 2：核心逻辑移植与配置分层 | 🔧 进行中 | pydantic 已剥离（dataclass），基类继承与 DelayConfig 注入已上设备 |
| Phase 3：工作流引擎设备端跑通 | ⏳ 未开始 | |
| Phase 4：打包发布与稳定性 | ⏳ 未开始 | |

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
- [ ] 配置层搬上设备：设备端 session/YAML 的存放与加载（config.py 已可导入，
  SESSION_PATH 不存在时走默认值；还缺设备端写入/下发链路）
- [ ] 工作流 YAML 下发到设备应用私有目录（`filesDir`，不污染用户数据目录）
- [ ] 场景定义 / 区域配置的设备端加载

## Phase 3 待办：工作流引擎设备端跑通

- [ ] DSL 引擎在设备端 Python 运行
- [ ] 场景识别 → 决策 → 操作的完整循环
- [ ] 悬浮服务作为任务入口

## Phase 4 待办：打包发布与稳定性

- [ ] APK 体积优化（当前 72MB+，主要来自 rapidocr 模型 + onnxruntime-android）
- [ ] 异常恢复机制（无障碍服务被系统关掉后的自动重连）
- [ ] Release 签名构建
- [ ] 用户引导流程（首次启动 → 开无障碍 → 开始使用）

---

## 未提交的变更

```
git status:
 M .gitignore
 M config/system/scenes/game_menu_page.yaml
 M scripts/count_file_and_code.py
?? android/                          ← 整个 Android 工程（含 Kotlin + 资源 + pystubs）
?? src/lvjiang/core/ondevice/        ← 设备端 Python 模块（8 个文件）
```

`.tooling/` 下的脚本（enable_a11y.py、run_selftest.py 等）也是新增，但 .tooling 本身可能已 gitignore。

---

## 下一步操作（按顺序）

1. **提交 Phase 2 阶段性成果**（pydantic 剥离 + 基类继承 + enable_a11y 修复）
2. **配置层设备端落地**：session/工作流 YAML 的存放位置与下发链路
3. **场景定义设备端加载** → 进入 Phase 3（DSL 引擎设备端跑通）
