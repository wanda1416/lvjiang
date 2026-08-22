# 律匠开发路线图 v3

> 状态快照（2026-08-15 刷新）：Phase 0~9 已全部完成，并延伸出 Android
> 独立执行端、macOS 适配、打包分发与质量门禁。下文 Phase 0~8 为原始规划
> 存档（实际交付已超出原计划，详见 docs/40-development/）。当前阶段与
> 后续规划见文末「当前阶段」一节。

工具形态：PyQt6 图形界面应用（Windows 主用）+ Android 独立执行端

## Phase 0：项目骨架（1天）

目标：目录结构、依赖管理、配置系统、GUI 主窗口能跑起来

```text
创建项目目录
├── pyproject.toml / requirements.txt
├── lvjiang/
│   ├── __init__.py
│   ├── __main__.py        # 启动入口（python -m lvjiang）
│   ├── config.py          # 配置加载与校验
│   └── constants.py       # 常量定义（分辨率基准、默认路径等）
├── lvjiang/core/          # 核心能力包
│   ├── __init__.py
│   ├── capture.py         # 屏幕捕获（mss + 窗口定位）
│   ├── ocr.py             # OCR接口
│   ├── input.py           # 输入控制（pyautogui）
│   └── region.py          # POI区域定义 + 坐标换算
├── lvjiang/workflows/     # 工作流包（空）
│   └── __init__.py
├── lvjiang/ui/            # GUI包
│   ├── __init__.py
│   └── app.py             # PyQt6 主窗口
└── tests/                 # 测试目录
    └── __init__.py
```

交付物：能 `python -m lvjiang` 跑起来，显示 PyQt6 主窗口

## Phase 1：坐标校准工具（2-3天）

目标：让用户标记各功能区域，生成坐标配置文件

为什么先做这个：

- 所有后续功能都依赖准确的区域坐标
- 游戏UI可能变动，坐标需要可重新校准
- 不同用户分辨率不同，需要相对比例换算

功能设计（GUI 内嵌）：

```text
校准工具集成在主窗口中：
├─ 顶部：刷新投屏窗口列表 / 实时捕获投屏窗口
├─ 左侧：预设区域列表（可勾选/编辑）
│   ├── 装备详情面板、初始词条区域
│   ├── 调律词条区域（×4）、调律/转律/回收/确认按钮
│   ├── 材料数量显示、背包格子区域
├─ 中间：截图显示区，支持鼠标框选区域
├─ 右侧：选中区域的属性（名称、坐标、颜色特征）
└─ 底部：保存配置 / 导出 / 重置
```

核心机制：

```yaml
# 生成的坐标配置文件 coordinate.yaml
# 所有坐标基于 1920x1080 基准分辨率
base_resolution: [1920, 1080]

regions:
  equip_detail_panel:
    type: "box"           # 矩形区域
    left: 380
    top: 140
    width: 480
    height: 700

  initial_affix:
    type: "box"
    left: 400
    top: 380
    width: 400
    height: 40

  tuning_affix_1:
    type: "box"
    left: 400
    top: 440
    width: 400
    height: 35

  # ... 更多区域

  bag_grid:
    type: "grid"          # 格子阵列
    first_cell: [1280, 150]   # 第一个格子中心
    cell_size: [80, 80]       # 格子宽高
    gap: [10, 10]             # 间距
    cols: 6                    # 列数
    rows: 5                    # 行数

  tune_button:
    type: "point"         # 单点
    x: 900
    y: 680
    # 可选：颜色特征，用于运行时校验
    color_check: [200, 200, 200]  # 按钮正常状态的RGB
```

运行时坐标换算：

```python
class CoordinateSystem:
    def __init__(self, config_path, actual_resolution):
        self.config = load(config_path)
        self.scale_x = actual_resolution[0] / self.config.base_resolution[0]
        self.scale_y = actual_resolution[1] / self.config.base_resolution[1]

    def get(self, name):
        region = self.config.regions[name]
        if region.type == "box":
            return (
                int(region.left * self.scale_x),
                int(region.top * self.scale_y),
                int(region.width * self.scale_x),
                int(region.height * self.scale_y)
            )
        elif region.type == "point":
            return (
                int(region.x * self.scale_x),
                int(region.y * self.scale_y)
            )
        elif region.type == "grid":
            # 返回第(row, col)个格子的坐标
            pass
```

## Phase 2：POI截取框架 + OCR验证（2-3天）

目标：能按区域裁剪截图，送OCR识别，输出结构化结果

POI（Point of Interest）框架设计：

```text
PoiExtractor
├── 加载坐标配置
├── 截图（全屏或指定区域）
├── 按名称裁剪ROI
├── 可选预处理（放大、灰度、二值化）
└── 送OCR识别
```

验证流程（GUI）：

在主窗口增加“OCR测试”标签页：

- 选择要测试的区域（下拉框）
- 点击“截图识别”按钮
- 显示：裁剪后的图片 + OCR原始结果 + 解析后的结构化数据
- 支持手动修正 OCR 错误，积累纠错样本

输出：

- 确认OCR在裁剪后的区域上准确率足够
- 积累一批标注数据，用于优化解析器

## Phase 3：UI状态检测（1-2天）

目标：识别当前游戏界面处于什么状态

状态定义：

```text
UNKNOWN      # 未知
BAG          # 背包界面
EQUIP_DETAIL # 装备详情弹窗
TUNE_PANEL   # 调律界面
TRANSFER_PANEL # 转律界面
CONFIRM_DIALOG # 确认弹窗
MATERIAL_SHORTAGE # 材料不足提示
```

检测方式（纯视觉，不读内存）：

- 模板匹配：检测特定UI元素是否存在（如调律按钮）
- 颜色特征：某些按钮有特定颜色
- OCR关键词：识别"调律"、"转律"、"回收"等文字

验证方式（GUI）：

在主窗口增加“状态检测”标签页：

- 实时显示当前检测到的状态
- 用户可以手动标注正确状态，积累训练数据

## Phase 4：词条解析器（2天）

目标：把OCR原始文本解析为结构化词条数据

- 输入：OCR结果（文字+置信度）
- 输出：标准化的词条对象

```text
解析流程：
OCR原始: "最大外功攻击 荐 99.3"
    ↓
分离: 名称="最大外功攻击", 标签="荐", 数值="99.3"
    ↓
名称标准化: "max_outer_attack"
    ↓
数值解析: 99.3, 类型=flat
    ↓
输出: Affix(name="max_outer_attack", raw="最大外功攻击", value=99.3, is_pct=False)
```

关键：模糊匹配 + 纠错

- 编辑距离匹配
- 数值范围校验（发现异常值）
- 上下文校验（某部位不可能出现的词条）

## Phase 5：输入控制封装（1天）

目标：封装pyautogui点击，加入随机延迟

```python
class InputController:
    def click(self, poi_name: str):
        # 从坐标配置获取位置
        x, y = self.coords.get(poi_name)
        # 随机偏移（模拟人类不精确）
        x += random.randint(-3, 3)
        y += random.randint(-3, 3)
        # 随机延迟
        time.sleep(random.uniform(0.1, 0.3))
        pyautogui.click(x, y)
        time.sleep(random.uniform(0.1, 0.2))
```

验证（GUI）：

在主窗口增加“输入测试”按钮，点击后观察投屏画面是否有反应

## Phase 6：工作流编排（3-5天）

目标：把以上能力串成完整流程

### 工作流1：扫描穿戴装备

```text
开始
  ↓
检测当前状态是否为角色装备界面
  ↓
逐部位点击（或OCR识别当前界面所有装备）
  ↓
识别每件装备的词条
  ↓
评分（此时用简单规则，后期可配置）
  ↓
展示结果，标记顶级装备
  ↓
用户确认
```

### 工作流2：批量筛选

```text
选择部位
  ↓
遍历背包格子（按坐标配置的位置点击）
  ↓
对每件装备：
  点击 → 等待详情 → OCR识别 → 评分 → 决策 → 执行
  ↓
翻页 → 重复
```

### 工作流3：精调

```text
选择装备
  ↓
进入调律界面
  ↓
循环：
  OCR识别当前词条
  评估 → 决策（调律/转律/保留/回收）
  执行点击
  等待动画
  ↓
直到结束条件触发
```

## Phase 7：规则引擎配置化（3-5天）

目标：把硬编码规则抽成YAML配置

- 流派预设文件
- 词条权重配置
- 评分公式配置
- 库存策略配置

此时才进入你之前关心的"会心双刀规则"、"顶级装备判定"等细节。

## Phase 8：GUI 完善（持续）

- 实时画面预览（投屏画面 + 识别结果叠加）
- 日志面板（每步决策的完整日志）
- 统计报表（处理装备数量、保留/回收/精调汇总）
- 配置编辑器（可视化修改流派规则、调律预算）

## 当前阶段（2026-08-15 刷新）

已完成：Phase 0 → Phase 9 全部落地，并超额延伸。

| 阶段 | 状态 | 核心交付 |
|------|------|----------|
| Phase 0 项目骨架 | ✅ | PyQt6 主窗口 + 核心模块 + 双屏 DPI 适配 |
| Phase 1 坐标校准 | ✅ | 区域编辑器（画布交互 + Layout→Scene→Region 层级 + 吸附对齐） |
| Phase 2 POI + OCR | ✅ | RapidOCR + 材料识别 + grid 校准 + scrcpy 截图 |
| Phase 3 UI 状态检测 | ✅ | 场景/视图模型 + 模板匹配 + OCR 关键词（置信度阈值 0.35） |
| Phase 4 词条解析器 | ✅ | 装备解析（定音词条全量池匹配）+ 模糊匹配纠错 |
| Phase 5 输入控制 | ✅ | SendInput / PostMessage（已弃 pyautogui）+ 随机延迟 + InputSimConfig |
| Phase 6 工作流编排 | ✅ | DSL 工作流引擎（lark 解析 + 四大指令 + 子工作流 + validate_only 预检 + CoordRef 坐标体系 + Entity 层次） |
| Phase 7 规则引擎配置化 | ✅ | 调律规则 YAML（开关机制 + 逐条处置规则 + 狗粮有序规则表 + 材料策略） |
| Phase 8 GUI 完善 | ✅（部分持续） | 实时预览 + 日志面板 + 规则编辑器；统计报表面板仍为占位 |
| Phase 9 游戏配置与规则重构 | ✅ | 游戏配置对话框 + 流派/玩法/调律规则三层术语统一 |

Phase 9 之后的延伸工作（07-26 ~ 08-16）：

- **自动调律端到端流水线**：背包遍历 → 潜力判定 → 实际调律 → 终局判定 → 调律说明文档。
- **Android 独立执行端**：三通道 PoC 闭环、系统配置随 APK 分发、设备端工作流引擎、原生调律参数配置页、release 实机复验。
- **打包分发**：PyInstaller onedir 一键打包 + 内置 adb，用户免装 platform-tools。
- **平台适配**：抽离 core/platforms.py；macOS Phase 0（依赖验证 + 退出崩溃修复）。
- **质量门禁**：ruff + mypy + GitHub Actions CI；pytest 1932 例全绿。
- **DSL CoordRef 坐标统一体系**：CoordRef/RectCoordRef/CircleCoordRef/Offset 类型层次 + 向量运算规则 + AST SceneRef→EntityRef 重命名 + click/drag 语义修正。
- **DSL click/drag 时序增强**：suppress_defaults + before/after 组合 wait_clause + 泛化元组语法 + clock/datetime 内置函数。
- **i18n 国际化框架**：核心模块 + 翻译文件（zh_CN/en_US）+ 25+ 文件 tr() 改造 + 设置对话框语言选择。
- **配置架构**：ConfigResolver 双层（system/local）分离写合并读；布局目录化存储（layouts.yaml + layouts/{名}/{场景}.json）。

后续规划（按优先级）：

1. **装备分析流程扩展（背包批量）**：现有 equip_analysis.wf 已实现 8 件穿戴装备扫描 + EquipStatusTab 展示；待扩展到背包批量遍历（遍历背包 → OCR → 评级/潜力判定 → 输出结构化报告）。
2. **统计报表面板**：数据源 `output["tuning_reports"]` 已产出，EquipStatusTab 已展示装备数据；独立统计面板本体未做。
3. **转律 / 装上执行**：当前转律仅用于评级模拟（judge 预测潜力），无真实点击转律的工作流；毕业装备替换穿戴（装上）亦未做。
4. **CoordRef 运算落地实际工作流**：当前 CoordRef 类型体系与运算规则已就位，但现有 .wf 脚本尚未使用坐标运算功能；待实际场景验证后补充示例工作流。
5. **多游戏插件**：引擎侧图色 / 模板定位 / 持续手势原语已补齐（2026-08-22，见 `docs/30-architecture/32-grammar/06.4-vision-functions.md`），新增游戏走 `apps/` 插件机制：坐标转 scene/layout、流程按 `.wf` 重写，不搬任何脚本运行时。
