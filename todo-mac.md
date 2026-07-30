# macOS 平台支持 — 计划与进度

> 最后更新：2026-07-30（计划制定，未开始实施）

---

## 背景与目标

- 目标：让律匠在 macOS 上可用，优先支持 **ADB 模式**（PC 控手机），窗口模式（投屏窗口视觉识别）后置。
- 约束：需兼顾较早的 Mac 机型。经盘点，瓶颈不在机型架构（所有依赖都有 x86_64/universal2 wheel，Intel 无问题），在 **macOS 系统版本下限**。
- 代码现状利好：后端已抽象（`_backend` 在 `windows`/`adb` 间切换；`InputBackend`/`CaptureBackend` 基类；`core/desktop/` 与 `core/android/` 分离），DSL 引擎/OCR/配置系统均为纯 Python。

## 总体进度一览

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 0：依赖可行性验证（需 mac 真机） | ⏳ 未开始 | 确定实际支持的最低 macOS 版本 |
| Phase 1：ADB 模式跑通 | ⏳ 未开始 | 平台门控改动可先在 Windows 上开发 |
| Phase 2：窗口模式（Quartz） | ⏳ 未开始 | 可无限期后置 |
| Phase 3：打包分发（可选） | ⏳ 未开始 | Gatekeeper 签名/公证，独立课题 |

---

## 关键结论（2026-07-30 盘点）

### 唯一硬阻塞点

`ui/main_window.py` L101 启动时无条件 `from ..core.desktop import create_input_backend`，
而 `core/desktop/win32_util.py` L15 **模块级**执行 `ctypes.windll.user32` ——
mac 上 `ctypes.windll` 不存在，import 阶段直接 AttributeError。这是启动路径上唯一一处。

### 已天然安全（无需改动）

| 位置 | 现状 |
|---|---|
| `__main__._configure_dpi` | `ctypes.windll` 在 try 内捕获 AttributeError，mac 静默跳过 |
| `crash_handler.install` | 已有 `sys.platform == "win32"` 门控 |
| `core/android/device.py` `_resolve_adb_path` | 优先 `shutil.which("adb")`，跨平台；仅备选候选路径是 Windows 的 |
| `scrcpy_capture.py` / `adb_capture.py` / `android/input.py` | 纯 subprocess + socket + PyAV，无平台 API |
| 全仓库 subprocess 调用 | 无 `CREATE_NO_WINDOW` / `winreg` / `STARTUPINFO` |
| DSL 引擎、场景/配置系统 | 纯 Python + pathlib |

### 软问题（不崩但需处理）

| 位置 | 问题 | 处理 |
|---|---|---|
| `workflows/builtins/system.py` | confirm/pause 无 Qt 回调时回退 `MessageBoxW`；notify 直接调 `MessageBoxTimeoutW` | 有 UI 时走 `_ui_callback` 不受影响；回退分支加平台门控（mac 用 `osascript` 或降级为 log） |
| `ui/window_ops.py` L3-4 顶层 `from ctypes import wintypes` | mac 上 import 不崩，但 `_refresh_window_rect` 调用会崩 | ADB 分支不走该函数；Phase 1 只需保证不误入 windows 分支 |
| `ui/main_window.py` pynput 全局热键 F8-F10 | mac 需「输入监控+辅助功能」权限，F 键默认媒体键 | 权限未授予时降级为窗口内热键（`keyPressEvent` 已处理），启动时提示 |
| `_backend` 默认值 `"windows"`（散布约 10 处 `getattr(self, "_backend", "windows")`） | mac 上应默认 `"adb"` 并隐藏窗口扫描入口 | UI 层小改 |

### 依赖版本下限（"较早的 mac" 的真正约束）

| 依赖 | 约束 | 影响 |
|---|---|---|
| PyQt6>=6.6（Qt 6.6） | **需 macOS 11 Big Sur+** | 最硬的下限。支持 10.14/10.15 须降到 PyQt6 6.4.x（Qt 6.4 支持 10.14+），且要验证 UI 代码在 6.4 下无 API 缺口 |
| onnxruntime 1.20 | 1.19+ 疑似要求 macOS 13.3+（待实测 wheel 的 `MACOSX_DEPLOYMENT_TARGET`） | 老系统可能要 pin 到 1.16/1.17，连带调整 rapidocr 版本 |
| av / mss / pynput / numpy | 下限宽松（~10.13+） | 无风险 |
| Python 3.10+ | python.org 安装包支持 10.9+ | 无风险 |

**建议支持基线：macOS 11 Big Sur**（2013 年后绝大多数机型可升级到 11）。
如确有 10.15 及以下需求，成本跳升（双版本依赖矩阵 + Qt 6.4 回归），建议明确排除。

---

## Phase 0 待办：依赖可行性验证（0.5~1 天，需 mac 真机）

- [ ] **P0-a** 目标 mac 上安装 Python 3.10+，`pip install -e ".[dev]"` 全部依赖
  - 重点：onnxruntime + rapidocr-onnxruntime 在该 macOS 版本上的可装版本组合
  - 若 onnxruntime 1.20 装不上，测试 pin 1.17/1.16 的组合，记录 pyproject 需要的平台条件依赖
- [ ] **P0-b** 跑全量 `pytest tests`（约 833 例，DSL/OCR 逻辑纯 Python，预期全绿）
- [ ] **P0-c** 单独验证 OCR 实链路：rapidocr 加载模型 + 对 `data/references/` 任一图片推理
- [ ] **P0-d** 产出结论：实际支持的最低 macOS 版本 + 依赖 pin 矩阵（写回本文档）

## Phase 1 待办：ADB 模式跑通（2~3 天）

平台门控部分不依赖 mac 真机，可先在 Windows 上开发自测：

- [ ] **P1-a** `core/desktop/win32_util.py` 模块级 `ctypes.windll` 移入函数（或平台门控），
  杜绝非 Windows 平台 import 即崩
- [ ] **P1-b** `ui/main_window.py` 启动路径按平台懒加载：
  非 Windows 时不 import `core.desktop`，`_win_input = None`，`_backend` 默认 `"adb"`
- [ ] **P1-c** `workflows/builtins/system.py` 三个 MessageBox 回退分支加 `sys.platform` 门控
  （mac 回退用 `osascript -e 'display dialog ...'`，或直接降级为 log 输出）
- [ ] **P1-d** `core/android/device.py` `_resolve_adb_path` 补 mac 候选路径：
  `~/Library/Android/sdk/platform-tools/adb`、`/opt/homebrew/bin/adb`、`/usr/local/bin/adb`
- [ ] **P1-e** UI 适配：mac 上隐藏「扫描窗口」「后台模式」入口；
  pynput 热键权限缺失时 try/except 降级 + 启动提示（窗口内热键兜底）
- [ ] **P1-f** Windows 回归：上述改动后全量 pytest + Windows 实机冒烟（两种后端均正常）
- [ ] **P1-g** mac 真机验证（依赖 Phase 0）：
  连设备 → scrcpy 流截图 → OCR → 跑一条完整调律工作流

## Phase 2 待办：窗口模式（8~10 天，可无限期后置）

- [ ] **P2-a** `core/desktop/` 拆分为 `desktop/windows/` + `desktop/macos/` + 公共工厂
- [ ] **P2-b** macOS 输入后端：Quartz `CGEvent` 点击/拖拽（pyobjc-framework-Quartz）
  - mac 无 PostMessage 等价物（无法向窗口句柄投递消息），只有全局事件注入一种模式
  - UI 层在 mac 上屏蔽「后台输入」选项
- [ ] **P2-c** macOS 窗口枚举与定位：`CGWindowListCopyWindowInfo`
  - 注意 window name 不一定等于标题栏文本，匹配策略需实测调整
- [ ] **P2-d** Retina 坐标换算：物理像素 ÷ backingScaleFactor → 逻辑坐标，
  与 mss 截图坐标系对齐（等价 Windows 端 DPI 感知问题）
- [ ] **P2-e** 辅助功能权限引导：`CGEventPost` 需用户在
  「系统设置 → 隐私与安全性 → 辅助功能」授权，未授权时静默失败，必须启动检测 + 引导
- [ ] **P2-f** mac 真机联调：投屏窗口定位 → 截图 → OCR → 点击闭环

## Phase 3 待办：打包分发（可选，独立课题）

- [ ] Gatekeeper 签名/公证方案调研（无签名 app 需右键打开或 `xattr -d com.apple.quarantine`）
- [ ] 是否 pyinstaller 打包 vs 提供 pip 安装脚本

---

## 风险清单（按优先级）

1. **onnxruntime 版本矩阵**（Phase 0 必须实测）— 唯一可能推翻「macOS 11 基线」的因素
2. **pynput 全局热键权限** — 影响体验不影响功能，窗口内热键兜底
3. **scrcpy 流 PyAV 软解性能** — 老款 Intel 无硬解兜底，需实测帧率（H.264 软解不重，预期可接受）
4. **窗口模式的辅助功能权限 + Retina 换算 + 后台输入语义缺失**（Phase 2）— 处理不好直接影响可用性

---

## 下一步操作（按顺序）

1. **P1-a ~ P1-f**：平台门控改动（Windows 上即可开发，不依赖 mac 真机）
2. **P0 全部**：拿到目标 mac 后做依赖验证，产出最低版本结论
3. **P1-g**：mac 真机跑通 ADB 模式全链路
