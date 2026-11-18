# 律匠项目演进总结

> 时间跨度：2026-07-13 ~ 2026-07-21
> 提交总数：120+
> 本文档按阶段梳理项目演进过程，重点展开重构/拆分/解耦细节，标注用户主动提出的关键指令。

---

## 阶段总览

| 阶段 | 时间 | 主题 | 核心交付 |
|------|------|------|----------|
| Phase 0 | 07-13 | 项目骨架 | PyQt6 GUI + 核心模块 + DPI 适配 |
| Phase 1 | 07-14 | 区域编辑器 | 画布交互 + 坐标体系 + 布局层级 |
| Phase 2 | 07-14~15 | 配置与场景管理 | 多用户 + 配置分层 + YAML 外部化 |
| Phase 3 | 07-15~16 | DSL 工作流引擎 | .wf 文件执行 + lark 语法 + 子工作流 |
| Phase 4 | 07-16~17 | DSL 语义增强 | 四大指令统一 + 变量系统 + 崩溃防护 |
| Phase 5 | 07-17~18 | 图像识别与 OCR | 材料识别 + grid 校准 + scrcpy 截图 |
| Phase 6 | 07-18~19 | 输入控制与稳定性 | SendInput 迁移 + PostMessage + 泄漏修复 |
| Phase 7 | 07-19~20 | 装备调律业务 | 自动调律工作流 + 滚动校验 + 装备面板 |
| Phase 8 | 07-20~21 | 架构通用化 | 插件化架构 + 模块解耦 + 文档规范化 |
| Phase 9 | 07-21~22 | 游戏配置与调律规则重构 | 游戏配置对话框 + 流派配置 + 调律规则标准词条化 |

---

## Phase 0：项目骨架（07-13）

**关键提交：**
- `9b64996` Phase 0 项目骨架初始化
- `798959b` 窗口扫描定位、双屏 DPI 适配
- `24e3f22` BorderOverlay 全屏覆盖层
- `918563d` app.py 按功能拆分为三模块

**首次拆分：** `app.py` 单文件 → 拆为 `main_window.py`（主窗口）+ `run_control.py`（运行控制）+ `overlay.py`（覆盖层），职责分离。

**交付物：** `python -m lvjiang` 启动 PyQt6 主窗口，支持多分辨率。

---

## Phase 1：区域编辑器（07-14）

**关键提交：**
- `0612e4f` OCR 引擎迁移 RapidOCR + 区域编辑器实现
- `7c8eb8c` **布局层级重构** Layout → Scene → Region
- `752f822` 区域拖拽/拉伸吸附对齐
- `553017e` **区域编辑器拆分为 region_editor 子包**
- `15c0f7e` 画布中间层坐标解耦

### 重构详情：布局层级重构 `7c8eb8c`

**背景：** 最初所有区域平铺在一个 `regions.json` 中，无层级概念。

**重构内容：**
- 引入三层模型：`Layout`（布局容器）→ `Scene`（场景分类）→ `Region`（具体区域）
- `region_config.py` 重写为 Layout dataclass + LayoutConfigManager
- 数据迁移：`regions.json` → `layouts/默认布局.json` + `config.json`
- 编辑器 UI 重构为 SceneTab（QTabWidget），每个场景一个标签页

**影响文件：** 8 files, +725 / -287

### 重构详情：编辑器拆分为子包 `553017e`

**背景：** 区域编辑器最初是单文件 `region_editor_dialog.py`，随功能增加膨胀到难以维护。

**拆分结果：**
```
lvjiang/ui/region_editor/
├── __init__.py
├── canvas.py          # 画布核心绘制（从原文件提取）
├── dialog.py          # 对话框主逻辑
└── scene_tab.py       # 场景标签页
```

**影响文件：** 6 files, +537 / -499

### 重构详情：画布中间层坐标解耦 `15c0f7e`

**背景：** 画布直接操作屏幕像素坐标，与截图分辨率强耦合。

**解耦方案：**
- 引入 Canvas Middle Layer，画布内部使用归一化坐标 [0,1]
- 绘制时按当前画布尺寸换算，截图缩放/平移不影响区域定义
- 后续修复 `c1df83c` 处理缩放后坐标换算精度问题

**用户指令：**
- **"布局栏改为标签+按钮模式"** → 布局栏交互多次迭代（下拉框 → 标签按钮 → 下拉框+激活标签）
- **"画布初始图片左上角对齐而非居中"** → 修复画布初始渲染位置

---

## Phase 2：配置与场景管理（07-14~15）

**关键提交：**
- `39233d6` **拆分自带配置与用户级配置**
- `cb88fb5` 多用户支持基础功能
- `c25ca8e` 场景定义 YAML 外部化
- `d9756a8` **配置目录三层化重构**
- `e399955` 场景分组管理功能

### 重构详情：配置分层 `39233d6` + `d9756a8`

**背景：** 最初所有配置混在一个 `config/` 目录，用户修改和程序自带内容无法区分。

**三层化结果：**
```
config/
├── system/        # 程序自带，只读，随代码分发
│   ├── scenes/    # 场景定义 YAML
│   └── workflows/ # 工作流 DSL
├── local/         # 用户数据，.gitignore
│   ├── layouts/   # 布局实例 JSON
│   ├── users/     # 用户配置 JSON
│   └── session.json
└── backup/        # 自动备份
```

**文件迁移：**
- `config/user/` → `config/local/`
- `config.json` → `session.json`（运行时状态）
- 新增 `preferences.yaml`（用户偏好，手动编辑）
- 删除死代码：`region.py`、`CoordinateConfig`

**影响文件：** 15 files, +149 / -223

### 重构详情：场景定义 YAML 外部化 `c25ca8e`

**背景：** 场景定义硬编码在 Python 代码中，修改需改代码重编译。

**外部化方案：**
- 场景定义移至 `config/system/scenes/*.yaml`
- 新增 `scene_loader.py` 负责加载
- 新增 `scene_registry.py` 作为全局注册表
- 支持加载顺序控制（system → local override）

**用户指令：**
- **"拆分自带配置与用户级配置"** → 配置分层为 system/local/backup
- **"多用户支持"** → 主菜单重构，用户配置独立存储

---

## Phase 3：DSL 工作流引擎（07-15~16）

**关键提交：**
- `a55e971` 工作流基类抽象
- `17c5a36` 工作流声明式 DSL 引擎与 .wf 文件执行
- `a54e4d9` DSL 解析器重写，支持条件分支
- `02daadb` **工作流架构分层**
- `f860a7a` DSL v2 解析器重写 — lark + EBNF 语法
- `034be51` DSL call 子工作流 + engine 状态隔离

### 重构详情：工作流架构分层 `02daadb`

**背景：** 最初工作流是硬编码 Python 类，每个流程一个文件。

**分层结果：**
```
BaseWorkflow（基类）
├── 声明式 .wf 文件（DSL 语法）
└── WorkflowEngine（纯解释器）
    ├── parser.py（解析 .wf → AST）
    └── engine.py（执行 AST）
```

**核心变化：**
- 工作流从 Python 代码 → DSL 文本文件
- 引擎从业务逻辑 → 纯解释器
- 新增内置函数机制，DSL 可调用 Python 能力

### 重构详情：DSL v2 解析器重写 `f860a7a`

**背景：** v1 解析器是手写递归下降，语法扩展困难。

**重写方案：**
- 引入 Lark 解析器框架
- 用 EBNF 语法文件 `grammar.lark` 定义 DSL 语法
- 解析器自动生成 AST
- 支持条件分支（if/elif/else）、循环（while/for）

**里程碑：** 从硬编码工作流 → 声明式 DSL 解释执行。

---

## Phase 4：DSL 语义增强（07-16~17）

**关键提交：**
- `535701b` **DSL 去隐式化重构 + 崩溃防护体系**
- `bc211a0` **变量 `$var` 与场景引用 `[scene]` 语法分离**
- `3b96b8d` 数值比较运算符、eval 字面量、字段链式访问
- `8ca7d67` **统一四大指令语法**（click/scan/recognize/drag）
- `6a4f194` scan/recognize by 子句短路识别
- `a65d6af` 录制功能 + DSL 坐标字面量

### 重构详情：语法分离 `bc211a0`

**背景：** 最初 `$var` 和 `[scene]` 共用同一套访问语法，语义混淆。

**分离方案：**
- `grammar.lark`：新增 `var_ref($NAME)`、`click_target`、`find_stmt` 规则
- `ast_nodes.py`：新增 `SceneRef`/`Find`，删除 `ClickMatch`，`Click` 改为 `SceneRef|VarRef`
- `engine.py`：`_exec_click` 分 `SceneRef`/`VarRef` 两路执行
- 数据流：`ocr_scene` 返回纯文本，`_exec_scan` 额外存 `region_map` 到 `engine._scan_meta`

**影响文件：** 11 files, +566 / -308

### 重构详情：去隐式化 + 崩溃防护 `535701b`

**背景：** DSL 引擎存在多处隐式行为（自动类型转换、隐式变量创建），导致崩溃难以定位。

**重构内容：**
- 移除隐式类型转换，要求显式 `to_number()`/`to_string()`
- 变量未定义时抛出明确错误而非静默创建
- 新增 try/catch 崩溃防护，记录崩溃堆栈
- 工作流文件全部适配新语法

### 重构详情：四大指令统一 `8ca7d67`

**背景：** click/scan/recognize/drag 四个核心指令语法不统一，参数传递方式各异。

**统一方案：**
- 所有指令统一为：`指令 目标 [参数]`
- 目标统一支持 `[scene].region_key` 或 `$var`（动态引用）
- 参数统一支持常量、变量、字段访问
- 测试覆盖全部指令变体

**用户指令：**
- **"calibrate → align 全栈重命名"** (`5f4811f`) → 术语简化
- **"DSL 去隐式化重构"** → 消除隐式行为，增加显式控制
- **"变量与场景引用语法分离"** → `$var` 仅用于变量，`[scene]` 仅用于场景引用

---

## Phase 5：图像识别与 OCR（07-17~18）

**关键提交：**
- `5f6319a` 材料分类器 MaterialRecognizer
- `3611dd2` **grid 校准算法重构为亮度二值化**
- `ed3255c` scrcpy H.264 视频流截图方案
- `29581ea` scrcpy 流式截图 OCR 识别优化
- `404d34f` Panel 类型系统 + DSL 范围迭代语法

### 重构详情：grid 校准算法重构 `3611dd2`

**背景：** 原算法用方差谷点检测网格分隔线，对低对比度场景失效。

**重构方案：**
- 改用亮度二值化：将图像转为灰度，按阈值二值化
- 短区间过滤：过滤掉宽度过窄/过宽的候选分隔线
- 算法更鲁棒，适配更多场景

### 重构详情：OCR 场景识别逻辑抽象 `8bba594`

**背景：** OCR 识别逻辑散落在各工作流中，重复代码多。

**抽象方案：**
- 新增 `OCREngine` 类，封装区域裁剪 + 预处理 + 识别
- 各工作流通过 `OCREngine` 调用，不直接操作图像
- 支持批量识别、结果缓存

**用户指令：**
- **"grid 校准算法重构"** → 从方差谷点 → 亮度二值化 + 短区间过滤
- **"引入 scrcpy 截图方案"** → 替代 mss，实现无损 H.264 截图

---

## Phase 6：输入控制与稳定性（07-18~19）

**关键提交：**
- `8daff4a` **Win32 SendInput 替换 pyautogui**
- `31ebbe8` PostMessage 后台鼠标模式
- `deaf280` PostMessage drag 补发 WM_NCHITTEST
- `e7ff7cd` **修复屏幕捕获内存泄漏**
- `2ebdd96` **输入/截图后端抽象化** + ADB 后端

### 重构详情：输入后端抽象化 `2ebdd96`

**背景：** 输入控制直接调用 pyautogui，无法切换不同输入方式。

**抽象方案：**
```
InputBase（抽象基类）
├── SendInputBackend（Win32 前台）
├── PostMessageBackend（Win32 后台）
└── ADBBackend（Android 设备）
```

**核心变化：**
- `input_base.py` 定义统一接口
- 各后端实现 `click()`/`drag()`/`swipe()` 等方法
- 通过配置切换后端，工作流代码不变

### 重构详情：截屏内存泄漏修复 `e7ff7cd`

**背景：** 长时间运行后内存持续增长，最终 OOM 崩溃。

**根因：** mss 截图返回的 Bitmap 对象未被正确释放，GDI 句柄泄漏。

**修复方案：**
- 显式调用 `DeleteObject` 释放 GDI 句柄
- 引入看门狗机制，超时自动终止截图
- 后续引入 scrcpy 方案彻底替代 mss

**用户指令：**
- **"用 Win32 SendInput 替换 pyautogui"** → 去除 pyautogui 依赖
- **"新增 PostMessage 后台鼠标模式"** → 光标不动，直接投递鼠标事件
- **"修复屏幕捕获内存泄漏"** → 截屏死锁根治

---

## Phase 7：装备调律业务（07-19~20）

**关键提交：**
- `405c56c` 装备分析工作流与毕业率计算
- `5cef3fb` 属性规则统一管理
- `2e190e2` 词条映射配置化 + 属性管理 UI
- `2f5ebfb` 装备状态面板 — 主面板展示八件装备
- `66a6a73` 自动调律代码化 + 手甲术语修正
- `6226fd9` **滚动校验重构**与 DSL 文档补全

### 重构详情：装备模型去 slot 依赖 `42f8294`

**背景：** 装备数据依赖 `slot`（槽位）字段推断类型，但 slot 与装备类型是不同概念。

**重构内容：**
- `EquipmentData` 移除 `slot` 字段，新增 `category` property
- `parser.parse()` 替代 `parse_slot()`，从 `type` 推断类别
- 评估器全链路 `equip.slot` → `equip.type`/`equip.category`
- 规则配置 `first_affix`/`divine_affixes` 改为 `type`-keyed
- `single_tuning.wf` 培养页判断改为 DSL if 条件分支

**影响文件：** 14 files, +341 / -174

### 重构详情：业务流架构解耦 `9f2d4f1`

**背景：** 工作流中混用 `convert` 和 `eval` 两种数据转换语法，逻辑分散。

**解耦方案：**
- 废弃 `convert` DSL 语法，统一使用 `eval` 调用内置函数
- `equipment_parser` 作为 builtins 内置函数，纯 OCR 文字分析，不依赖场景
- `run_control.py` 瘦身为通用执行器，结果保存至 `{flow_id}_{timestamp}.json`
- GUI 两按钮改为下拉列表 + 开始执行按钮

### 重构详情：滚动校验重构 `6226fd9`

**背景：** 背包遍历工作流中滚动后无法准确判断新行内容。

**重构方案：**
- 引入滚动校验机制：滚动后对比前后 grid 内容
- 自愈闭环：检测到异常时自动回滚重试
- 步进约束：限制单次滚动行数，避免跳过
- 文档化：整理为独立流程文档 `0e90bfb`

**用户指令：**
- **"装备状态面板 2×4 布局"** → 左武器+环佩，右防具
- **"不需要展示数值比例"** → 移除 cap_pct 百分比显示
- **"怎么颜色没了？"** → 保留颜色分级（≥90%金/≥80%紫/≥60%蓝/<60%绿）
- **"提供刷新按钮"** → 手动重读用户 JSON 数据
- **"手甲为正确武器类型名称"** → 全量替换术语

---

## Phase 8：架构通用化（07-20~21）

**关键提交：**
- `ee855a7` **内置函数模块化拆分** + DSL 算术运算符
- `408d48f` 字典字面量初始化
- `3c73b9c` **DSL def/import/call proc 模块化重构**
- `b2b6361` **单包插件化架构重构**
- `20042fe` **文档结构重构**与编号规范化
- `040df7a` 移除 EQUIP_REGIONS 硬编码
- `6d14e0d` Region 移除冗余 name 字段 + 表格列宽优化

### 重构详情：UI 大型文件 Mixin 拆分 `576c5a6`

**背景：** `canvas.py`（977行）、`main_window.py`（643行）、`dialog.py`（516行）过大，AI 处理时容易混淆。

**拆分结果：**
```
canvas.py (977→382)
├── canvas_interaction.py (673)  # 交互逻辑
└── canvas_coords.py (102)       # 坐标换算

main_window.py (643→299)
├── window_ops.py (190)          # 窗口操作
└── run_control.py (173)         # 运行控制

dialog.py (516→275)
└── layout_ops.py (212)          # 布局操作
```

**继承关系：**
- `RegionCanvas → CanvasInteractionMixin → CanvasCoordMixin`
- `MainWindow(WindowOpsMixin, RunControlMixin, QMainWindow)`
- `RegionEditorDialog(LayoutOpsMixin, QDialog)`

**影响文件：** 8 files, +1417 / -1359

### 重构详情：region_editor 模块系统性解耦 `c3891f7`

**背景：** `dialog.py`（653行）和 `scene_tab.py`（600行）职责混杂。

**拆分结果：**
```
dialog.py (653→275)
├── scene_ops.py (416)        # 分组/场景 CRUD + 右键菜单
├── recognition_ops.py (130)  # OCR/材料识别
└── script_ops.py (134)       # DSL 脚本测试器

scene_tab.py (600→255)
├── scene_region_panel.py (255)  # 区域列表/CRUD
└── scene_poi_panel.py (389)     # 坐标/方向列表/CRUD

region_config.py → layout_manager.py  # LayoutConfigManager 独立
```

**约束：** 所有文件控制在 400 行以内，降低 AI 处理时的混淆风险。

**影响文件：** 9 files, +1605 / -1470

### 重构详情：语法模块解耦至 grammar 子包 `203c862`

**背景：** `grammar.lark`、`parser.py`、`ast_nodes.py` 散落在 `workflows/` 根目录，引擎与语法定义耦合。

**解耦方案：**
```
workflows/
├── engine.py              # 引擎（通过 grammar 模块感知语法）
└── grammar/               # 语法子包（关注点分离）
    ├── __init__.py
    ├── grammar.lark       # EBNF 语法定义
    ├── parser.py          # Lark 解析器
    └── ast_nodes.py       # AST 节点定义
```

**核心变化：**
- 引擎通过 `grammar` 模块感知语法，实现关注点分离
- `_coord_meta` 父子引擎共享，支持子工作流访问父工作流扫描坐标

**影响文件：** 12 files, +83 / -24

### 重构详情：内置函数模块化拆分 `ee855a7`

**背景：** `builtins.py` 单文件 505 行，所有内置函数混在一起。

**拆分结果：**
```
builtins/
├── __init__.py         # 模块导出
├── _registry.py        # 函数注册表
├── arithmetic.py       # 算术运算（add/sub/mul/div/mod/pow/min/max/abs/ceil/floor/round）
├── bag_traverse.py     # 背包遍历（scroll/scroll_to_bottom/get_grid）
├── equipment.py        # 装备相关（equipment_parser/attr_caps/find_tune_material）
├── general.py          # 通用工具（log/wait/sleep/random/concat/len/range/type）
└── system.py           # 系统级（screenshot/click_match/recognize/align）
```

**同时增强：**
- `grammar.lark` 支持算术运算符（`+`/`-`/`*`/`/`/`%`）
- 支持负数字面量、字典字面量（含嵌套）
- 新增 `test_arith.py` 测试覆盖

**影响文件：** 16 files, +1196 / -666

### 重构详情：DSL def/import/call proc 模块化重构 `3c73b9c`

**背景：** 子工作流调用语法 `call "file.wf" with` 不够直观，且参数传递方式不统一。

**重构方案：**
- 拆分为三个正交指令：
  - `import "file.wf"` — 引入外部 def 定义
  - `def name($params) ... end` — 定义子过程
  - `call name($args)` — 调用本地或导入的过程
- 移除旧的 `call "file.wf" with/read` 语法

**核心改动：**
- `grammar.lark`：新增 `import`/`def`/`call_proc` 规则
- `ast_nodes.py`：新增 `Import`/`ProcDef`/`CallProc`，移除 `Call`
- `engine.py`：新增 `_exec_call_proc`/`_resolve_imports`（含循环检测）
- 变量隔离：`save`/`restore` caller variables，`session`/`context` 共享引用

**文件迁移：**
- `subcall/*.wf`：包入 `def` 格式
- `auto_tuning`/`single_tuning`/`equip_analysis`：改用 `import` + `call proc`

**影响文件：** 16 files, +761 / -507

### 重构详情：单包插件化架构重构 `b2b6361`

**背景：** `lvjiang/` 包混合了通用引擎和业务逻辑，无法复用。

**重构方案：**
```
lvjiang/（原）
├── core/          # 通用引擎
├── ui/            # 通用 UI
├── workflows/     # 通用工作流引擎
└── equip_parser/  # 业务逻辑（混在一起）

src/（新）
├── core/          # 通用引擎（不变）
├── ui/            # 通用 UI 基础
├── workflows/     # 通用工作流引擎
└── apps/
    └── yysls/     # 业务插件
        ├── core/          # 业务核心
        ├── equip_parser/  # 装备解析
        ├── evaluator/     # 评估器
        ├── ui/            # 业务 UI
        └── workflows/     # 业务工作流
```

**核心变化：**
- 通用引擎（`src/core/`、`src/ui/`、`src/workflows/`）与业务插件（`src/apps/yysls/`）分离
- 新增 `src/apps/base.py` 插件基类
- 新增 `src/apps/__init__.py` 插件注册表
- 通用 `recognizers/` 模块（OCR/模板/颜色识别器）

**影响文件：** 134 files, +1644 / -573

### 重构详情：Region 移除冗余 name 字段 `6d14e0d`

**背景：** Layout JSON 中 region 同时存储 `key` 和 `name`，但 `name` 已在 Scene YAML 中定义，属于冗余。

**解耦方案：**
- `Region` dataclass 移除 `name` 字段，仅保留 `key` + 坐标
- 新增 `get_region_name(scene_key, region_key)` 辅助函数，通过 Scene 定义查名称
- `canvas.py`/`recognition_ops.py` 改用辅助函数
- `canvas_interaction.py` 移除对 `region.name` 的写入
- 4 个 Layout JSON 文件清理冗余 `name` 字段

### 重构详情：移除 EQUIP_REGIONS 硬编码 `040df7a`

**背景：** `scene_registry.py` 硬编码引用 `equip_weapon_detail` 场景，破坏通用性。

**解耦方案：**
- 移除 `EQUIP_REGIONS` 全局变量
- `canvas.py` 初始化 `_current_regions` 为空列表
- 通过 `set_scene_key` 动态加载，不再绑定具体业务场景

**用户指令：**
- **"layouts 下的 json 还有 name 属性，纯属多余"** → Region 移除冗余 name 字段
- **"为什么区域列表表格列宽全部一样？"** → 名称/Key 自适应，布尔列固定 50px
- **"为什么 scene_registry 绑定了具体场景？"** → 移除 EQUIP_REGIONS 硬编码
- **"内置函数模块化拆分"** → builtins.py → builtins/ 子包
- **"单包插件化架构重构"** → 通用主窗口 + 插件继承体系
- **"文档结构重构"** → 分层编号规范化

---

## Phase 9：游戏配置与调律规则重构（07-21~22）

**关键提交：**
- `606e91d` **游戏配置重构 + 调律规则标准词条化重构**（39 files, +2603/-1358）
- `e5bb2b3` docs: 清理 PROGRESS.md 已提交状态与未提交清单

### 重构详情：游戏配置重构

**背景：** 装备属性管理（attributes.yaml）与调律规则（tuning_rules/*.yaml）
是上下游关系，两者原本各自为政，缺乏统一入口与共享词汇源。

**重构内容：**
- 新增「游戏配置」对话框（`AttrManagerDialog`，3 Tab）：装备配置 / 词条配置 /
  流派配置（school_panel.py 重写为左右分栏）；
- attributes.yaml 顶层新增 `weapon_types`（10 武器注册表）与 `schools`（新
  schema `流派名 → {attr, main, sub}`，预填十大流派）；
- 横刀 → 唐横刀 全局改名（注册表驱动）；
- 武器类型动态化（constants.py 模块加载时读 attributes.yaml 快照）；
- 菜单与热键收口（F5/F6/F8-F10）。

### 重构详情：调律规则标准词条化重构

**背景：** 调律规则 YAML 里混用符号（大外/小外/会意/会心/精准/大无相/小无相/
小外属）与标准词条名，且 `variants` 层让 schema 嵌套过深。

**重构内容：**
- 删除 `SYMBOL_VOCAB`/`SYMBOL_MAP` 符号层与 `variants` 层（字段上提 YAML
  顶层），规则词条唯一来源 = `AttrRuleManager.get_normal_affix_names()` 标准
  全集（越界名保存拒绝）；
- heal.yaml 拆为 heal_pure/heal_fire 两条独立规则；
- 规则可新建/删除（TuningRuleManager.create_rule/delete_rule + 对话框 Tab
  增删）；导航「流派设置」改「规则设置」；
- UI checkbox 网格改「已选列表 + AffixPickerDialog 添加/移除」
  （variant_pool_page.py → pool_page.py）；
- 属攻归一化：非武器本属 → 无相，错位属攻（武器上流派属攻、非武器字面无相）
  加 `(错位)` 标记判垃圾（generic._normalize）。

**结果：** pytest 528 例全绿（yysls 342 例 + 全仓 528 例）。

> 详细重构记录见 `2026-07-22-tuning-rules-standardization.md`。

---

## 重构脉络总结

### 数据层解耦演进
```
Phase 1: regions.json 平铺 → Layout/Scene/Region 三层模型
Phase 2: 配置混放 → system/local/backup 三层分离
Phase 2: 场景硬编码 → YAML 外部化 + Registry
Phase 8: Layout JSON 冗余 name → 仅 key 关联，name 从 Scene 查
Phase 8: scene_registry 绑定业务场景 → 移除硬编码，canvas 动态加载
Phase 9: 调律规则符号层删除 → 标准词条全集（attributes.yaml _aliases）唯一来源
```

### 代码层拆分演进
```
Phase 0: app.py → main_window + run_control + overlay
Phase 1: region_editor_dialog.py → region_editor/ 子包
Phase 2: UI 大文件 → Mixin 拆分（canvas/main_window/dialog）
Phase 4: region_editor → 4 个 Mixin（scene/recognition/script/layout）
Phase 4: 语法文件散落 → grammar/ 子包
Phase 7: builtins.py → builtins/ 子包（按功能分类）
Phase 8: lvjiang/ → src/ + src/apps/yysls/（通用引擎 + 业务插件）
Phase 9: tuning_rules UI checkbox 网格 → 列表 + AffixPickerDialog（variant_pool_page.py → pool_page.py）
```

### DSL 语法演进
```
Phase 3: 硬编码工作流 → 声明式 .wf 文件
Phase 3: 手写递归下降 → Lark + EBNF 语法
Phase 4: $var/[scene] 混用 → 语法分离
Phase 4: 隐式行为 → 去隐式化 + 崩溃防护
Phase 4: 指令语法不统一 → 四大指令统一
Phase 7: convert 语法 → 废弃，统一 eval + 内置函数
Phase 8: call "file.wf" with → import/def/call proc 模块化
```

---

## 用户关键指令索引

### 架构解耦类
| 指令 | 影响范围 | 提交 |
|------|----------|------|
| 移除 layout JSON 中冗余 name 字段 | Region dataclass, canvas, 4 个 JSON | `6d14e0d` |
| 移除 EQUIP_REGIONS 硬编码 | scene_registry, canvas | `040df7a` |
| 内置函数模块化拆分 | builtins/ 子包 | `ee855a7` |
| 单包插件化架构重构 | 全项目目录结构 | `b2b6361` |
| calibrate → align 全栈重命名 | DSL 指令、文档 | `5f4811f` |
| 配置目录三层化 | config/ 目录结构 | `d9756a8` |
| 场景定义 YAML 外部化 | scene_loader, scene_registry | `c25ca8e` |
| 变量与场景引用语法分离 | grammar.lark, parser, engine | `bc211a0` |
| DSL 去隐式化 + 崩溃防护 | engine, parser | `535701b` |
| def/import/call proc 模块化 | grammar, engine, 全部 .wf | `3c73b9c` |

### UI/交互类
| 指令 | 影响范围 | 提交 |
|------|----------|------|
| 装备状态面板 2×4 布局 | equip_status_panel.py | `2f5ebfb` |
| 不需要展示数值比例 | equip_status_panel.py | `2f5ebfb` |
| 保留颜色分级 | equip_status_panel.py | `2f5ebfb` |
| 提供刷新按钮 | equip_status_panel.py, main_window.py | `2f5ebfb` |
| 表格列宽优化 | 3 个 panel 文件 | `6d14e0d` |

### 技术选型类
| 指令 | 影响范围 | 提交 |
|------|----------|------|
| Win32 SendInput 替换 pyautogui | input 后端 | `8daff4a` |
| scrcpy H.264 截图方案 | capture 后端 | `ed3255c` |
| grid 校准算法重构 | grid 检测 | `3611dd2` |
| PostMessage 后台鼠标模式 | input 后端 | `31ebbe8` |

---

## 技术债务与后续方向

### 已解决
- [x] pyautogui 依赖移除
- [x] 截屏内存泄漏
- [x] DSL 语法语义正交化
- [x] 配置分层与多用户支持
- [x] 场景/布局数据解耦
- [x] 内置函数模块化
- [x] 通用引擎与业务插件分离

### 待处理
- [ ] 工作流 DSL 文档持续补全
- [ ] 自动调律工作流原型验证
- [x] 装备评估规则配置化（2026-07-22 调律规则标准词条化重构完成）
- [x] 插件化架构下业务模块迁移（2026-07-21 单包插件化架构重构完成）

> 后续待办详见项目根目录 `TODO.md`。

---

## 附录：提交统计

| 类型 | 数量 | 说明 |
|------|------|------|
| feat | ~45 | 新功能 |
| refactor | ~35 | 重构 |
| fix | ~20 | 修复 |
| docs | ~12 | 文档 |
| test | ~5 | 测试 |
| perf/chore | ~3 | 性能/杂项 |

**代码规模：** 120+ commits，覆盖 PyQt6 GUI、DSL 引擎、OCR 识别、输入控制、装备调律业务等完整技术栈。
