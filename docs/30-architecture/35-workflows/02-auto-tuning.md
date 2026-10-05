# 流程：自动调律（Auto Tuning）

> 需求与行为规格（为什么要 Panel、遍历策略语义、指纹模型、决策编排三行为点、回收
> 处理）见 [20-requirements/10-tuning/01-auto-tuning.md](../../20-requirements/10-tuning/01-auto-tuning.md)。
> 本文只写实现侧：Panel 数据结构、图像自校准、模块拆分与配置落点。

## 1. Panel 数据结构

### 1.1 Scene YAML — 新增 `panels` 段

```yaml
# bag_equip_detail.yaml
panels:
  - key: bag_grid
    name: 背包格区域
    cols: 6
    rows: 3
    h_span: 0.0048    # 列间距（归一化，初始估算，运行时由图像校准覆盖）
    v_span: 0.0064    # 行间距（归一化，初始估算，运行时由图像校准覆盖）
```

- `panels` 与 `regions`、`points` 同级，是 Area 的第三种形态
- Panel 内部不定义子 region — 格子坐标由引擎运行时计算
- `h_span` / `v_span` 为初始估算值，实际运行时通过图像分析自校准

### 1.2 DSL 访问语法

Panel 内的格子通过 `[panel_key][row][col]` 寻址：

```dsl
# 点击第 2 行第 3 列的格子
click [bag_equip_detail].[bag_grid][2][3]

# 用变量寻址
eval $row = 1
eval $col = 4
click [bag_equip_detail].[bag_grid][$row][$col]
```

## 2. 图像自校准 — 方差分析定位

每次进入背包页或滚动后，对 panel 区域截图，通过像素方差分析精确定位每个格子：

```text
原理：
  slot 内有装备图标 → 像素变化丰富 → 高方差
  span 是纯色间隔   → 像素几乎不变 → 低方差

算法：
  1. 截取 panel 区域图像
  2. 转灰度图
  3. 计算每行像素方差 → variance_y[h]
  4. 计算每列像素方差 → variance_x[w]
  5. 低方差带 = span，高方差带 = slot
  6. 从方差剖面提取 slot 精确边界 → 算出每个格子中心坐标
```

**关键特征：slot 黑边**

每个 slot 边缘有**非常明显的黑色边框**，这是校准的强信号：

- 黑边与 span 纯色间隔、slot 内部图标都有明确对比，方差/边缘检测都能稳定切带
- 即使 slot 为空（无图标），**黑边仍在** → 空 slot 也能被定位

## 3. 实现架构

### 3.1 模块拆分

```text
src/lvjiang/apps/yysls/workflows/implementations/
├── auto_tuning.py          # 编排层（AutoTuningWorkflow）
├── bag_traversal/          # 背包遍历策略
│   ├── base.py             # 抽象基类
│   ├── dedup.py            # 去重策略（默认）
│   └── positional.py       # 位置对齐策略
└── tuning/                 # 调律功能模块
    ├── __init__.py
    ├── judge.py            # TuningJudge: 判定与评级（纯逻辑）
    ├── executor.py         # TuningExecutor: 调律执行（材料检查、狗粮决策）
    ├── navigator.py        # TuningNavigator: 导航（DSL subcall 桥接）
    └── recycler.py         # TuningRecycler: 重置与回收
```

### 3.2 职责分离

| 类 | 职责 | 依赖 |
|----|------|------|
| `AutoTuningWorkflow` | 编排层：部位循环、装备处理主链、行为处置 | 组合引用 tuning/* |
| `TuningJudge` | 判定与评级：潜力判定、期望评级、行为表评级提供者 | 纯逻辑，不依赖 UI |
| `TuningExecutor` | 调律执行：单轮调律、材料检查、狗粮决策、就绪确认 | 通过 wf 引用访问 UI 原语 |
| `TuningNavigator` | 导航：页面跳转、词条收集 | DSL subcall 桥接 |
| `TuningRecycler` | 重置与回收：重置调律（冷却期检查）、装备回收 | 通过 wf 引用访问 UI 原语 |

### 3.3 DSL subcall 桥接

导航逻辑通过 DSL subcall 文件实现，避免 Python 与 DSL 两处重复维护：

```python
# 导航 subcall 文件（统一在 navigation.wf 中定义）
_NAV_FILE = "subcall/navigation.wf"
_NAV_MAIN_TO_EQUIP = (_NAV_FILE, "nav_main_to_equip")
_NAV_EQUIP_TO_TUNE = (_NAV_FILE, "nav_equip_to_tune")
_NAV_BACK_TO_MAIN = (_NAV_FILE, "nav_back_to_main")
```

引擎通过 `load_subcalls()` 加载，`call_subcall()` 桥调用。

## 4. 调律说明文档

调律说明文档（`TuningDocWriter`）记录本次运行的完整过程：

- 运行头：用户名、规则配置、部位选择
- 装备节：每件值得调律的装备的判定过程、调律轮次、狗粮策略、结束决策
- 运行小结：成品清单（一般评级及以上的装备）

输出目录：`logs/tuning/{username}/`。

## 5. 场景与布局配置

### 5.1 bag_equip_detail.yaml

```yaml
# 废弃：18 个 bag_1_1 ~ bag_3_6 region
# 新增：
panels:
  - key: bag_grid
    name: 背包格区域
    cols: 6
    rows: 3
    h_span: 0.0048    # 初始估算，运行时自校准
    v_span: 0.0064    # 初始估算，运行时自校准
```

### 5.2 Layout JSON

```json
{
  "bag_equip_detail": {
    "panels": [
      {
        "key": "bag_grid",
        "x_ratio": 0.6638,
        "y_ratio": 0.5524,
        "w_ratio": 0.2789,
        "h_ratio": 0.3010
      }
    ],
    "arrows": [
      {
        "key": "scroll_down",
        "from_cx_ratio": 0.78,
        "from_cy_ratio": 0.65,
        "to_cx_ratio": 0.78,
        "to_cy_ratio": 0.55
      }
    ]
  }
}
```

## 相关文档

| 文档 | 内容 |
|------|------|
| [20-requirements/10-tuning/01-auto-tuning.md](../../20-requirements/10-tuning/01-auto-tuning.md) | 自动调律的需求与行为规格 |
| [01-current-equip-analysis.md](01-current-equip-analysis.md) | 用户当前装备分析流程 |
| [34-scene/01-scene-layout-definition.md](../34-scene/01-scene-layout-definition.md) | Scene / Area / Panel / Layout 的通用语义 |
