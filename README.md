# 律匠（lvjiang）

> 通用视觉 RPA 引擎 + 插件架构

律匠是一个基于计算机视觉的桌面自动化工具，采用「通用引擎 + 插件」架构。通过桌面窗口的截图识别、OCR 与模拟点击，自动完成各类繁琐的操作流程。工具运行在 PC 端，支持两种输入后端：**窗口模式**（捕捉任意 PC 窗口，包括游戏窗口化模式、手机投屏窗口、模拟器）和 **ADB 模式**（直连手机/模拟器）。**不读取内存、不注入游戏进程、不上传你的游戏数据**，规避反作弊风险。

当前已内置《燕云十六声》装备调律插件，可通过插件机制扩展至更多游戏/场景。

> **当前状态**：支持直接操作游戏 PC 端窗口，也支持通过 **手机投屏**、**Android 模拟器** 或 **ADB** 操作安卓端。直接操作端游窗口时，请以管理员身份启动律匠。

<p align="left">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="GUI" src="https://img.shields.io/badge/GUI-PyQt6-41CD52">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-0078D6">
  <img alt="License" src="https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-0099cc">
</p>

> 📖 **用户手册**：[快速开始](docs/60-userguide/01-quick-start.md) | [目录](docs/60-userguide/README.md)

---

## ✨ 功能特性

- **双输入后端**：支持「窗口模式」（捕捉任意 PC 窗口：游戏窗口化、手机投屏、模拟器；可选择前台 SendInput 或后台 PostMessage 投递）与「ADB 模式」（screencap / scrcpy 视频流截图 + 触控注入）两种模式。
- **视觉识别流水线**：`mss` / scrcpy 截图 → RapidOCR（ONNX）文字识别 → 装备词条结构化解析 → 品阶与数值评分。
- **声明式场景与布局**：以 YAML 定义游戏界面的 Scene / Area / Point / Region，配合内置可视化编辑器标注坐标，分辨率自适应缩放。
- **工作流 DSL 引擎**：自研 `.wf` 领域特定语言，支持变量、条件、循环、子工作流调用与内置函数，把「扫描 → 识别 → 决策 → 点击」编排成可复用流程。内置 CoordRef 坐标类型体系，支持坐标向量运算。支持 `full by` 全量匹配与短路匹配双模式、panel 范围索引、工作流暂停/恢复。
- **装备评分规则**：可配置的词条上限（cap）、品阶推断、流派权重，支持鸣金 / 通用等评估器。
- **背包滚动遍历**：自动化背包步进滚动 + fps 数据完整性校验，批量筛选装备（见 [自动调律流程文档](docs/20-requirements/01-auto-tuning.md)）。
- **可视化桌面应用**：PyQt6 主窗口集成实时画面预览、识别结果叠加、坐标校准、OCR 测试、材料管理与运行日志面板。
- **拟人化操作**：点击前后随机延迟、坐标随机偏移、区域中心抖动，降低机械化特征。
- **毕业率计算**：Excel 公式 → Python 转换引擎，支持最优组合搜索与毕业概率评估。
- **i18n 国际化**：核心模块 tr() 全覆盖，支持中文 / 英文界面切换。
- **安卓独立执行端（实验性）**：基于 Chaquopy 将核心引擎移植到设备端，无障碍服务完成截图与手势注入，无需 PC 即可运行（截图 → OCR → 点击三通道闭环已验证，见 [Android 平台计划](docs/00-meta/platforms/android.md)）。

---

## 🧱 技术架构

```
PC 端游窗口 / 手机运行游戏
    ↓ 直接捕捉 / 投屏（视频流 + 反向控制）/ ADB
PC 端游戏或投屏窗口 / ADB 连接
    ↓ 截图
律匠：截屏 → OCR 识别 → 词条解析 → 规则评分 → 决策 → 模拟点击
    ↓ 点击事件经窗口输入 / 投屏协议 / ADB 投递
PC 端或手机端执行操作
```

| 层 | 模块 | 职责 |
|----|------|------|
| 捕获 / 输入 | `src/lvjiang/core/capture_base.py`、`core/desktop/`、`core/android/` | 截图与点击的统一抽象，桌面窗口与 ADB / scrcpy 后端 |
| OCR | `src/lvjiang/core/ocr.py` | RapidOCR（ONNX Runtime）文字识别封装 |
| 场景 / 布局 | `src/lvjiang/core/scene_definition.py`、`core/layout_manager.py` | 声明式界面模型加载、坐标换算、区域对齐 |
| 识别器 | `src/lvjiang/core/recognizers/` | 可插拔识别器（OCR / 模板匹配 / 颜色特征 / 参考图） |
| 配置管理 | `src/lvjiang/core/config/` | 应用配置、会话管理、用户数据、工作流配置加载与合并 |
| 工作流 | `src/lvjiang/workflows/` | `.wf` DSL 语法解析（grammar/）、执行引擎（engine/）、通用内置函数（builtins/） |
| 界面 | `src/lvjiang/ui/` | PyQt6 通用主窗口、场景编辑器、识别测试、运行控制 |
| 插件 | `src/lvjiang/apps/<name>/` | 游戏/场景专属插件（识别器、工作流、UI Tab） |
| 燕云插件 | `src/lvjiang/apps/yysls/` | 装备解析、评分、调律、材料识别、毕业率、专属 UI（config/ + core/ + workflows/ + ui/） |
| 设备端 | `src/lvjiang/core/ondevice/`、`android/` | 安卓独立执行端：无障碍截图/手势桥接、rapidocr 设备端适配、Kotlin 宿主工程 |

更多细节见 [架构文档](docs/30-architecture/README.md) 与 [DSL 语法文档](docs/30-architecture/32-grammar/README.md)。

---

## 📂 目录结构

```
lvjiang/
├── src/lvjiang/               # 通用视觉 RPA 引擎（src-layout，唯一可导入包）
│   ├── __main__.py            # 启动入口（python -m lvjiang [-reg <plugin>]）
│   ├── app.py                 # QApplication 入口
│   ├── _version.py            # 版本号
│   ├── core/                  # 截图 / 输入 / OCR / 场景 / 布局 / 坐标类型
│   │   ├── coord_types.py     # CoordRef 坐标类型体系（CoordRef/RectCoordRef/CircleCoordRef/Offset）
│   │   ├── config/            # 配置加载与会话管理（app.yaml / session / 用户 / 工作流配置）
│   │   ├── desktop/           # 桌面投屏窗口后端（截图 + SendInput 点击）
│   │   ├── android/           # ADB / scrcpy 后端
│   │   ├── ondevice/          # 设备端（Chaquopy）无障碍截图/输入与 OCR 适配
│   │   ├── recognizers/       # 可插拔识别器插件包（OCR / 模板 / 颜色 / 参考图）
│   │   ├── scene_definition.py      # 声明式场景模型加载
│   │   ├── layout_manager.py        # 布局索引与坐标换算
│   │   ├── reference_db.py          # 参考图库管理
│   │   ├── platforms.py             # 跨平台统一收口
│   │   └── crash_handler.py         # 崩溃日志收集
│   ├── workflows/             # 工作流 DSL 语法、引擎与通用内置函数
│   │   ├── grammar/           # .wf 语法定义（Lark）与解析器
│   │   ├── engine/            # 执行引擎（控制流 / 数据操作 / 面板识别）
│   │   ├── base/              # DSL 原语基类（识别 / 坐标 / 面板 / 动作）
│   │   ├── builtins/          # 通用内置函数（算术 / 字符串 / 系统）
│   │   └── implementations/   # 插件级工作流实现（背包遍历 / 调律）
│   ├── i18n/                  # 国际化
│   ├── ui/                    # PyQt6 通用图形界面
│   │   ├── scene_editor/      # 场景编辑器（坐标标注 / 区域绘制）
│   │   ├── reference/         # 参考图库管理 UI
│   │   ├── batch/             # 批量运行面板
│   │   ├── main/              # 主窗口本体与各功能混入（运行控制 / 窗口操作 / 菜单 / 托盘）
│   │   ├── scripts/           # 脚本录制、编辑与清单配置
│   │   ├── ocr/               # 图像识别对话框与画布
│   │   └── notices/           # 公告 / 更新 / 关于 / 反馈
│   └── apps/                  # 插件注册表
│       ├── base.py            # AppHooks 数据类
│       └── yysls/             # 燕云十六声插件
│           ├── config/        # 配置管理（manager / profile / 毕业率会话 / 玩法）
│           ├── core/          # 核心业务逻辑
│           │   ├── combat/        # 战斗属性与装备模型
│           │   ├── equip_parser/  # 装备词条解析（含定音词条）
│           │   ├── evaluator/     # 评分规则引擎
│           │   ├── graduation/    # 毕业率计算（Excel 公式 → Python）
│           │   ├── loadout/       # 装备方案管理
│           │   ├── profile_engine/ # Profile 持久化与再生数学
│           │   ├── recognizer/    # 材料识别
│           │   └── tuning_rules/  # 调律规则解析与判定
│           ├── workflows/     # 燕云专属工作流与内置函数
│           │   ├── builtins/      # 背包 / 装备 / Profile 专属函数
│           │   └── implementations/ # 自动调律 / 背包遍历实现
│           └── ui/            # 燕云专属 UI Tab
│               ├── game_settings/ # 游戏规则配置面板
│               ├── loadout/       # 装备展示与方案面板
│               ├── profile/       # Profile 数据面板
│               ├── tune_settings/ # 调律规则编辑面板
│               └── tuning/        # 调律进度面板
├── android/                   # 安卓独立执行端（Kotlin + Chaquopy 宿主工程）
├── config/
│   ├── system/                # 随版本发布的配置
│   │   ├── layouts/           # 布局定义（按名称分目录）
│   │   ├── scenes/            # 场景定义 YAML
│   │   ├── workflows/         # 工作流脚本（.wf）
│   │   ├── references/        # 参考图库（{空间}.yaml + 同名图片目录）
│   │   ├── yysls/             # 燕云插件专属配置（调律规则 / 玩法等）
│   │   ├── app.yaml           # 应用配置
│   │   ├── layouts.yaml       # 布局索引
│   │   ├── scenes.yaml        # 场景索引
│   │   └── ocr_rules.yaml     # OCR 规则配置
│   ├── local/                 # 用户覆盖配置（已 .gitignore）
│   └── session/               # 运行时会话数据
├── data/                      # 材料模板图、scrcpy-server 等资源
├── docs/                      # 分层文档
├── packaging/                 # PyInstaller 打包配置与脚本
├── scripts/                   # 辅助脚本与手动测试
├── tests/                     # pytest 测试（按 core / ui / workflows / yysls 分包）
├── dev.bat                    # Windows 快捷启动脚本
├── dev.sh                     # macOS 快捷启动脚本
└── pyproject.toml             # 项目元数据与依赖
```

---

## 🔧 环境要求

- **操作系统**：Windows（桌面投屏模式依赖 Win32 API）或 macOS 11+（ADB 模式）
- **Python**：3.10 及以上
- **游戏侧**：手机端《燕云十六声》，并满足以下任一连接方式：
  - **投屏模式**：PC 端存在可截屏、且支持鼠标点击转发到手机的投屏窗口（以 vivo 自带投屏为参考，不绑定特定品牌）
  - **ADB 模式**：手机开启 USB / 无线调试，PC 端可通过 ADB 连接（可选启用 scrcpy 视频流）

---

## 🚀 安装

```powershell
# 1. 克隆仓库
git clone git@github.com:wanda1416/lvjiang.git
cd lvjiang

# 2. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"
```

核心依赖：`PyQt6`、`mss`、`pynput`、`rapidocr-onnxruntime`、`onnxruntime`、`PyYAML`、`lark`（DSL 解析器）、`loguru`、`av`（scrcpy 模式的 H.264 解码）。

---

## ▶️ 使用

> 🧑‍💻 **首次使用？** 请参考 [用户指南](docs/60-userguide/README.md)——从连接手机、启动程序到运行自动调律的完整步骤。

**Windows 用户** 可直接从 [Releases](https://github.com/wanda1416/lvjiang/releases) 下载最新发行包（免安装 zip 或安装程序 exe），解压/安装后运行 `lvjiang.exe` 即可，无需 Python 环境。

> 使用安装程序时建议选择「仅为当前用户安装」，不建议「为所有用户安装」，否则可能被安全软件拦截。

**源码运行**（macOS / Linux / 需要最新功能）：

```powershell
# 方式一：纯通用模式（场景编辑 + 识别测试）
python -m lvjiang

# 方式二：加载燕云插件（通用引擎 + 燕云装备调律）
python -m lvjiang -reg yysls

# 方式三：Windows 快捷脚本（默认加载燕云插件，自动设置 PYTHONPATH）
.\dev.bat
```

> src-layout 下 `lvjiang` 包位于 `src/` 内：已 `pip install -e .` 则直接可用；否则需先将 `src` 加入 `PYTHONPATH`（`dev.bat` 已处理）。

启动后进入 PyQt6 主窗口，基本流程：

1. **选择连接**：刷新并选择投屏窗口，或连接 ADB 设备。
2. **校准坐标**：在场景编辑器中标注 / 校验各界面区域（首次使用或游戏 UI 变动时）。
3. **扫描装备**：自动截图识别当前穿戴装备，展示词条与评分结果。
4. **选择部位与模式**：勾选需要处理的调律部位（武器类 / 防具类），选择批量筛选或精调。
5. **开始执行**：实时查看画面预览、识别叠加与运行日志。

> 详细业务流程见 [当前装备分析](docs/30-architecture/35-workflows/01-current-equip-analysis.md) 与 [自动调律流程](docs/20-requirements/01-auto-tuning.md)。

---

## ⚙️ 配置

配置采用三层分离：出厂默认 → 用户覆盖 → 运行时会话，读取恒为合并视图。

| 层 | 路径 | 内容 | 是否提交到代码仓库 |
|---|---|---|---|
| 系统默认 | `config/system/` | 场景 / 工作流（`.wf`）/ 布局 / 输入参数 / 调律规则 / 参考图 | ✅ |
| 用户覆盖 | `config/local/` | 用户对 `app.yaml` 的键级 diff（配置管理对话框写入） | ❌ |
| 运行时会话 | `config/session/` | `session.json`（当前状态）、用户数据、工作流输出日志 | ❌ |

`config/local/` 与 `config/session/` 只保存在你自己的电脑上，律匠不会把其中任何内容上传到任何服务器。律匠全部的联网行为见 [隐私说明](PRIVACY.md)。

`config/system/app.yaml` 示例（输入模拟 + 命名延迟参数）：

```yaml
input_simulation:
  before_click_wait: [0.1, 0.3]     # 点击前随机延迟（秒）
  after_click_wait: [0.1, 0.3]      # 点击后随机延迟（秒）
  click_random_offset: 5            # 坐标随机偏移像素
  region_jitter_ratio: 0.25         # 区域中心抖动比例

delay_params:
  page_refresh:
    label: 页面刷新等待
    range: [2.0, 3.0]
  scroll_settle:
    label: 滚动惯性等待
    range: [3.0, 4.0]
```

工作流通过 `wait <key>`（DSL）/ `wait_delay(key)`（代码）按 key 引用命名延迟参数。

---

## 🧪 开发

```powershell
# 运行测试
pytest

# 运行单个测试文件（测试目录按 core / ui / workflows / yysls 分包）
pytest tests/workflows/test_parser.py
```

测试基线要求干净：`pyproject.toml` 中已将未处理警告升级为错误（`filterwarnings = ["error"]`）。

---

## 📚 文档

文档采用分层编号组织，索引见各目录 README：

| 目录 | 内容 |
|------|------|
| [`PRIVACY.md`](PRIVACY.md) | 隐私说明：联网行为、匿名调律数据收集发出什么/不发出什么 |
| [`docs/`](docs/README.md) | 完整文档中心与按读者导航 |
| [`docs/50-releases/`](docs/50-releases/README.md) | 发布记录与版本说明 |
| [`docs/60-userguide/`](docs/60-userguide/README.md) | 用户指南：连接手机、配置与运行自动调律 |
| [`docs/00-meta/`](docs/00-meta/README.md) | 元信息：路线图、文档组织约定 |
| [`docs/10-game/`](docs/10-game/README.md) | 游戏机制事实层：装备系统、流派、伤害、调律 / 转律规则 |
| [`docs/20-requirements/`](docs/20-requirements/README.md) | 需求文档：运行环境、操作流程、配置模型、Profile、毕业率、国际化 |
| [`docs/30-architecture/`](docs/30-architecture/README.md) | 技术架构：主窗口状态机、插件系统 |
| [`docs/30-architecture/31-models/`](docs/30-architecture/31-models/README.md) | 数据模型：装备模型、场景实现、会话与上下文 |
| [`docs/30-architecture/32-grammar/`](docs/30-architecture/32-grammar/README.md) | 工作流 DSL 语法规范 |
| [`docs/30-architecture/33-engine/`](docs/30-architecture/33-engine/README.md) | 引擎细节：截图与裁剪 |
| [`docs/30-architecture/34-scene/`](docs/30-architecture/34-scene/README.md) | 场景 / 布局定义与编辑 |
| [`docs/30-architecture/35-workflows/`](docs/30-architecture/35-workflows/README.md) | 业务流程编排 |
| [`docs/40-development/`](docs/40-development/README.md) | 开发日志：按月归档的重构 / 决策记录 |

---

## 🗺️ 路线图

项目按阶段演进：项目骨架 → 坐标校准 → POI 截取与 OCR → UI 状态检测 → 词条解析器 → 输入封装 → 工作流编排 → 规则引擎配置化 → GUI 完善 → 安卓独立执行端（实验性）。完整路线见 [roadmap](docs/00-meta/01-roadmap.md)（1932 测试用例），平台计划见 [Android](docs/00-meta/platforms/android.md) 与 [macOS](docs/00-meta/platforms/macos.md)。

---

## 🤝 贡献

欢迎通过 Issue 与 Pull Request 参与改进。提交前请：

1. 保证 `pytest` 全部通过；
2. 遵循现有代码风格与文档分层约定；
3. 涉及新场景 / 工作流时，同步更新 `config/system/` 与对应文档。

---

## 💬 反馈与支持

欢迎通过以下方式反馈问题：

- **微信交流群**：主窗口「帮助 → 反馈」中点击「扫码加群」
- **GitHub Issue**：[github.com/wanda1416/lvjiang/issues](https://github.com/wanda1416/lvjiang/issues)
- **提交规范**：[问题反馈规范](docs/60-userguide/08-feedback-and-issues.md)

**反馈范围说明：**

| 可以帮你的 | 超出能力范围的 |
|-----------|--------------|
| Bug 报告（附带日志/截图） | 一对一使用教学 |
| 功能建议与需求 | 远程协助配置环境 |
| 文档内容过时或无法实操 | 逐个步骤指导操作 |

作者精力有限，无法提供一对一使用教学。[用户指南](docs/60-userguide/README.md)和交流群已覆盖绝大多数使用场景，遇到问题请先查阅文档或在群内讨论。正式提交 Bug 时必须提供版本与运行环境、复现步骤、预期与实际结果、连续日志；识别或点击问题还需附截图或录屏。信息不足的问题在补齐前不会进入代码排查。

---

## 🔒 数据与隐私

律匠只在三种情况下联网：查公告、查有没有新版本、以及（0.7.0 起）收集匿名调律数据。

- 前两项**只下载不上传**，从第一个版本起就是如此。
- 匿名调律数据收集需要你在首次启动时明确同意，不同意就一条都不会发；同意之后也能随时在设置里关掉，并一键删除本地未上传的记录。
- 律匠**不会上传**你的截图、日志、装备数据、游戏账号或角色名，也不会上传 `config/` 目录里的任何内容。

完整说明见 [隐私说明](PRIVACY.md)。服务端代码同样公开：[`ops/stats-worker/`](ops/stats-worker/)

---

## ⚠️ 免责声明

本项目仅供学习与技术研究使用。使用自动化工具操作游戏可能违反相关游戏的用户协议，由此产生的账号封禁等一切后果由使用者自行承担。请在遵守当地法律法规及游戏服务条款的前提下使用，作者不对任何滥用行为负责。

---

## 📄 License

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE) 协议发布。

- ✅ 允许：个人学习、研究、修改、非商业分发
- ❌ 禁止：任何形式的商业使用（包括内部商业用途、商业分发、商业服务等）
- 🚫 **不开放商业授权**：无论何种情况，均不允许商业使用，请勿联系作者申请

完整协议文本见 [LICENSE](LICENSE) 文件。

---

Copyright (c) 2026 律匠作者（lvjiang）
