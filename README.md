# 律匠（lvjiang）

> 通用视觉 RPA 引擎 + 插件架构

律匠是一个基于计算机视觉的桌面自动化工具，采用「通用引擎 + 插件」架构。通过投屏画面的截图识别、OCR 与模拟点击，自动完成各类手游中繁琐的操作流程。工具运行在 PC 端，通过投屏窗口或 ADB 与手机交互，**不读取内存、不注入游戏进程**，规避手游反作弊风险。

当前已内置《燕云十六声》装备调律插件，可通过插件机制扩展至更多游戏/场景。

<p align="left">
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-blue">
  <img alt="GUI" src="https://img.shields.io/badge/GUI-PyQt6-41CD52">
  <img alt="Platform" src="https://img.shields.io/badge/Platform-Windows-0078D6">
  <img alt="License" src="https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-0099cc">
</p>

---

## ✨ 功能特性

- **双输入后端**：支持「桌面投屏窗口」（截图 + 后台 PostMessage / SendInput 点击）与「Android ADB」（screencap / scrcpy 视频流截图 + 触控注入）两种模式。
- **视觉识别流水线**：`mss` / scrcpy 截图 → RapidOCR（ONNX）文字识别 → 装备词条结构化解析 → 品阶与数值评分。
- **声明式场景与布局**：以 YAML 定义游戏界面的 Scene / Area / Point / Region，配合内置可视化编辑器标注坐标，分辨率自适应缩放。
- **工作流 DSL 引擎**：自研 `.wf` 领域特定语言，支持变量、条件、循环、子工作流调用与内置函数，把「扫描 → 识别 → 决策 → 点击」编排成可复用流程。
- **装备评分规则**：可配置的词条上限（cap）、品阶推断、流派权重，支持鸣金 / 通用等评估器。
- **背包滚动遍历**：自动化背包步进滚动 + fps 数据完整性校验，批量筛选装备（见 [自动调律流程文档](docs/35-workflows/02-auto-tuning.md)）。
- **可视化桌面应用**：PyQt6 主窗口集成实时画面预览、识别结果叠加、坐标校准、OCR 测试、材料管理与运行日志面板。
- **拟人化操作**：点击前后随机延迟、坐标随机偏移、区域中心抖动，降低机械化特征。

---

## 🧱 技术架构

```
手机运行游戏
    ↓ 投屏（视频流 + 反向控制） / ADB
PC 端投屏窗口 或 ADB 连接
    ↓ 截图
律匠：截屏 → OCR 识别 → 词条解析 → 规则评分 → 决策 → 模拟点击
    ↓ 点击事件经投屏协议 / ADB 转发
手机端执行操作
```

| 层 | 模块 | 职责 |
|----|------|------|
| 捕获 / 输入 | `src/lvjiang/core/capture_base.py`、`src/lvjiang/core/desktop/`、`src/lvjiang/core/android/` | 截图与点击的统一抽象，桌面窗口与 ADB / scrcpy 后端 |
| OCR | `src/lvjiang/core/ocr.py` | RapidOCR（ONNX Runtime）文字识别封装 |
| 场景 / 布局 | `src/lvjiang/core/scene_*.py`、`src/lvjiang/core/layout_manager.py` | 声明式界面模型加载、坐标换算、区域对齐 |
| 识别器 | `src/lvjiang/core/recognizers/` | 可插拔识别器（OCR / 模板匹配 / 颜色特征） |
| 工作流 | `src/lvjiang/workflows/` | `.wf` DSL 语法解析、执行引擎、通用内置函数 |
| 界面 | `src/lvjiang/ui/` | PyQt6 通用主窗口、场景编辑器、识别测试、运行控制 |
| 插件 | `src/lvjiang/apps/<name>/` | 游戏/场景专属插件（识别器、工作流、UI Tab） |
| 燕云插件 | `src/lvjiang/apps/yysls/` | 装备解析、评分、调律、材料识别、专属 UI |

更多细节见 [架构文档](docs/30-architecture/README.md) 与 [DSL 语法文档](docs/32-grammar/README.md)。

---

## 📂 目录结构

```
yysls-lvjiang/
├── src/                       # 通用视觉 RPA 引擎
│   ├── __main__.py            # 启动入口（python -m src [-reg <plugin>]）
│   ├── app.py                 # QApplication 入口
│   ├── config.py              # 配置加载与 Pydantic 校验
│   ├── constants.py           # 路径与常量定义
│   ├── core/                  # 截图 / 输入 / OCR / 场景 / 布局
│   │   └── recognizers/       # 可插拔识别器插件包
│   ├── workflows/             # 工作流 DSL 语法、引擎与通用内置函数
│   ├── ui/                    # PyQt6 通用图形界面
│   └── apps/                  # 插件注册表
│       ├── base.py            # AppHooks 数据类
│       └── yysls/             # 燕云十六声插件
│           ├── equip_parser/  # 装备词条解析
│           ├── evaluator/     # 评分与规则引擎
│           ├── workflows/     # 燕云专属工作流与内置函数
│           └── ui/            # 燕云专属 UI Tab
├── config/
│   ├── system/            # 随版本发布的场景 / 工作流 / 规则配置
│   └── local/             # 运行时生成的本地数据（已 .gitignore）
├── data/                  # 材料模板图、scrcpy-server 等资源
├── docs/                  # 分层文档（游戏机制 / 需求 / 架构 / 语法 / 流程）
├── scripts/               # 辅助脚本与手动测试
├── tests/                 # pytest 测试
├── dev.bat                # Windows 快捷启动脚本
└── pyproject.toml         # 项目元数据与依赖
```

---

## 🔧 环境要求

- **操作系统**：Windows（桌面投屏模式依赖 Win32 API）
- **Python**：3.10 及以上
- **游戏侧**：手机端《燕云十六声》，并满足以下任一连接方式：
  - **投屏模式**：PC 端存在可截屏、且支持鼠标点击转发到手机的投屏窗口（以 vivo 自带投屏为参考，不绑定特定品牌）
  - **ADB 模式**：手机开启 USB / 无线调试，PC 端可通过 ADB 连接（可选启用 scrcpy 视频流）

---

## 🚀 安装

```powershell
# 1. 克隆仓库
git clone <repo-url>
cd yysls-lvjiang

# 2. 创建并激活虚拟环境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装依赖（含开发依赖）
pip install -e ".[dev]"
```

核心依赖：`PyQt6`、`mss`、`pynput`、`rapidocr-onnxruntime`、`onnxruntime`、`PyYAML`、`pydantic`、`loguru`、`av`（scrcpy 模式的 H.264 解码）。

---

## ▶️ 使用

```powershell
# 方式一：纯通用模式（场景编辑 + 识别测试）
python -m src

# 方式二：加载燕云插件（通用引擎 + 燕云装备调律）
python -m src -reg yysls

# 方式三：Windows 快捷脚本（默认加载燕云插件）
.\dev.bat
```

启动后进入 PyQt6 主窗口，基本流程：

1. **选择连接**：刷新并选择投屏窗口，或连接 ADB 设备。
2. **校准坐标**：在场景编辑器中标注 / 校验各界面区域（首次使用或游戏 UI 变动时）。
3. **扫描装备**：自动截图识别当前穿戴装备，展示词条与评分结果。
4. **选择部位与模式**：勾选需要处理的调律部位（武器类 / 防具类），选择批量筛选或精调。
5. **开始执行**：实时查看画面预览、识别叠加与运行日志。

> 详细业务流程见 [当前装备分析](docs/35-workflows/01-current-equip-analysis.md) 与 [自动调律流程](docs/35-workflows/02-auto-tuning.md)。

---

## ⚙️ 配置

- **系统配置**（`config/system/`）：随版本发布的场景、工作流（`.wf`）与规则定义，通常无需手动修改。
- **本地偏好**（`config/local/session.json`）：用户级配置，由配置管理对话框写入，覆盖代码默认值，例如：

```jsonc
{
  "settings": {
    "adb_capture_streaming": false,   // ADB 模式是否启用 scrcpy 视频流截图
    "desktop_window_title": "投屏",    // 桌面模式投屏窗口标题关键字
    "desktop_background_input": true   // 是否启用后台输入（PostMessage）
  },
  "input_delay": {                     // 拟人化延迟参数
    "before_click_wait": [0.1, 0.3],   // 引擎级：点击前延迟
    "after_click_wait": [0.1, 0.2],    // 引擎级：点击后延迟
    "custom": {                        // 命名等待参数（供工作流 wait 按 key 引用）
      "page_refresh_wait": { "label": "页面刷新等待", "range": [2.0, 3.0] }
    }
  }
}
```

`config/local/` 下的所有内容（布局、会话、截图等）均为运行时生成，已在 `.gitignore` 中忽略。

---

## 🧪 开发

```powershell
# 运行测试
pytest

# 运行单个测试文件
pytest tests/test_parser.py
```

测试基线要求干净：`pyproject.toml` 中已将未处理警告升级为错误（`filterwarnings = ["error"]`）。

---

## 📚 文档

文档采用分层编号组织，索引见各目录 README：

| 目录 | 内容 |
|------|------|
| [`docs/10-game/`](docs/10-game/README.md) | 游戏机制事实层：装备系统、调律 / 转律规则 |
| [`docs/20-requirements/`](docs/20-requirements/README.md) | 需求文档：运行环境、操作流程、配置模型 |
| [`docs/30-architecture/`](docs/30-architecture/README.md) | 技术架构：主窗口状态机、场景 / 布局定义与编辑 |
| [`docs/31-models/`](docs/31-models/) | 数据模型：装备模型、场景实现、会话与上下文 |
| [`docs/32-grammar/`](docs/32-grammar/README.md) | 工作流 DSL 语法规范 |
| [`docs/33-engine/`](docs/33-engine/README.md) | 引擎细节：截图与裁剪 |
| [`docs/35-workflows/`](docs/35-workflows/README.md) | 业务流程编排 |

---

## 🗺️ 路线图

项目按阶段演进：项目骨架 → 坐标校准 → POI 截取与 OCR → UI 状态检测 → 词条解析器 → 输入封装 → 工作流编排 → 规则引擎配置化 → GUI 完善。完整路线见 [roadmap](docs/00-meta/roadmap.md)。

---

## 🤝 贡献

欢迎通过 Issue 与 Pull Request 参与改进。提交前请：

1. 保证 `pytest` 全部通过；
2. 遵循现有代码风格与文档分层约定；
3. 涉及新场景 / 工作流时，同步更新 `config/system/` 与对应文档。

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
