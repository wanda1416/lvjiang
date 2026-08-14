# 实体与坐标体系

DSL 指令操作的核心对象是**实体（Entity）**和**坐标（CoordRef）**。实体分为空间实体（Area）和行为实体（Action），坐标是 Area 在运行时的统一表示。理解这些概念及其关系，是正确使用 DSL 的前提。

## 目录

- [一、场景（Scene）](#一场景scene)
- [二、布局（Layout）与实体层次](#二布局layout与实体层次)
  - [实体层次](#实体层次)
  - [Area — 空间实体](#area--空间实体)
  - [Action — 行为实体](#action--行为实体)
- [三、CoordRef 类型体系（运行时坐标层）](#三coordref-类型体系运行时坐标层)
  - [运算规则](#运算规则)
  - [坐标约定](#坐标约定)
- [四、Panel — 可寻址容器](#四panel--可寻址容器)
  - [grid 型（已实现）](#grid-型已实现)
  - [regions 型（规划中）](#regions-型规划中)
  - [自对齐机制](#自对齐机制)

## 一、场景（Scene）

场景是 DSL 操作的**顶层命名空间**，每个场景对应游戏中一个页面或界面状态。场景在 `config/system/scenes/` 下的 YAML 文件中定义，包含该页面涉及的 Area 和 Panel 声明。

```yaml
# config/system/scenes/equip_weapon_detail.yaml
scene:
  name: equip_weapon_detail
  required_layout: equip_weapon_detail
  
  areas:
    - key: affix_1
      type: region
      ref: affix_1
    - key: affix_2
      type: region
      ref: affix_2
    # ...
  
  panels:
    - key: bag_grid
      rows: 3
      cols: 6
```

DSL 通过 `[scene_name]` 引用场景。场景名支持两种形式（见 [01-basics.md](01-basics.md#二引用模型)）：

```
[equip_weapon_detail]       # 配置引用
$scene_name                 # 变量引用
```

## 二、布局（Layout）与实体层次

布局定义实体的**具体坐标**。每个场景通过 `required_layout` 关联一个 Layout JSON 文件（位于 `config/system/layouts/`），Layout 负责将场景中的抽象名称映射到屏幕上的实际坐标。

### 实体层次

Layout 下所有元素统一继承 **Entity** 基类，分为两大分支：

```
Entity（布局元素基类）
├── Area（空间实体 —— 有位置、形状，可解析为 CoordRef）
│   ├── Region  — 矩形区域
│   ├── Panel   — 可寻址容器（本质也是矩形区域）
│   └── Point   — 坐标点（带半径）
└── Action（行为实体 —— 定义一次拖拽交互）
    └── Arrow   — 从起点 Point 指向终点 Point 的方向
```

| | Area | Action |
|---|------|--------|
| 本质 | **空间实体**（有位置、形状） | **行为实体**（定义一次交互） |
| 定义层 | Scene 层声明，Layout 层绑定坐标 | 纯 Layout 层定义 |
| 操作指令 | `click` / `scan` / `recognize` / `drag` | `drag` |
| 运行时表示 | → CoordRef（可参与坐标运算） | → 直接执行拖拽 |
| 约束 | Point 不能单独 `drag`（无 w/h，无法计算终点） | 不能 `scan` / `click` |

> **drag 对 Area 的隐式转换**：`drag` 操作 Region/Panel 时，引擎利用其 w/h 计算终点，将 Area 隐式转为单向 Arrow。这是语法糖，本质等价于定义一个从中心向某方向的 Arrow。

DSL 通过 `[scene].[entity]` 引用 Entity：

```
click [equip_weapon_detail].[affix_1]     # 点击 Area（Region）
scan [equip_weapon_detail].[affix_1]      # 对 Area 执行 OCR
drag [scene].[arrow]                      # 执行 Action（Arrow）定义的拖拽
drag [scene].[region] up                  # 对 Area 隐式拖拽（向上翻页）
```

## 三、CoordRef 类型体系（运行时坐标层）

Area 和 `find` 指令的产出在运行时统一解析为 **CoordRef** 类型体系，用于坐标运算和点击/拖拽：

```
CoordRef(cx, cy)              — 坐标点基类（中心点）
├── RectCoordRef(cx, cy, w, h) — 矩形区域（Region / Panel / FoundRegion）
├── CircleCoordRef(cx, cy, r)  — 圆形区域（Point）
└── tuple (cx, cy)             — 原始坐标对，可隐式转 Offset

Offset(dx, dy)                — 位移向量（独立类型，不属于 CoordRef）
```

> **tuple 的角色**：DSL 中 `(0.5, 0.3)` 这样的坐标对是原始 CoordRef，语义上是「位置」。当它参与 Offset 运算时（如 `CoordRef + tuple`），引擎自动将其转为 Offset。这保证了向量运算法则的一致性。

### 运算规则

| 运算 | 结果 | 说明 |
|------|------|------|
| CoordRef + Offset | CoordRef（保持子类） | 位置平移 |
| CoordRef - Offset | CoordRef（保持子类） | 位置平移 |
| CoordRef - CoordRef | Offset | 隐式降级为中心点 |
| Offset + Offset | Offset | 向量叠加 |
| Offset - Offset | Offset | 向量差 |
| Offset * n | Offset | 向量缩放 |
| Offset / n | Offset | 向量缩放 |
| tuple → Offset | 隐式转换 | 原始可当位移 |

**禁止的运算**：
- `CoordRef * n` / `CoordRef / n` — 位置乘以数字无意义，破坏向量运算法则
- 如需缩放，后续通过 `scale()` 函数提供

### 坐标约定

- **CoordRef 使用中心点**：`cx, cy` 命名
- **Layout 层 Region 使用左上角**：`x_ratio, y_ratio` 是左上角
- **转换时机**：`to_coord_ref()` 方法将左上角转为中心点：`cx = x + w/2`

## 四、Panel — 可寻址容器

Panel 是 Scene 层定义的一种特殊 Area，作为**可寻址的容器**存在。Panel 通过 `type` 字段区分内部结构：

| type | 语义 | 寻址方式 | 当前状态 |
|------|------|----------|----------|
| `grid` | 行列网格 | `[r][c]` | ✅ 已实现 |
| `regions` | 多个 Region 的集合 | `[name]` | 🔜 规划中 |

### grid 型（已实现）

当前所有 Panel 均为 `type=grid`（默认值，可省略），具有 `rows`/`cols` 属性，通过 `[r][c]` 二维索引寻址。`r`/`c` 从 1 开始计数。

> **索引语法区分**：`[scene].[panel][row][col]` 中，`scene`/`panel` 是配置引用（`[name]` 或 `$var`），而 `row`/`col` 是面板索引（`[INT]` 或 `[$var]`）。row/col 不是 area 引用，不支持字符串，`["a"]` 语法不合法。正确示例：`[scene].$panel_ref[$row][5]`，错误示例：`[scene].$panel_ref["a"]["b"]`。

```yaml
# Layout 定义
panels:
  - key: bag_grid
    rows: 3
    cols: 6
```

```
# DSL 使用
click [bag_equip_detail].[bag_grid][1][1]     # 点击第 1 行第 1 列格子中心
drag [bag_equip_detail].[bag_grid][1][1] down 3  # 下翻 3 行
```

### Panel 校准模式

Panel 支持三种校准模式（`calibration` 字段），控制网格检测方式：

| 模式 | 说明 |
|------|------|
| `auto` | 先图像检测，失败降级为等分（默认） |
| `even` | 跳过图像检测，直接等分 |
| `image` | 仅图像检测，失败返回 None |

```yaml
# 等分模式（跳过图像检测）
panels:
  - key: bag_grid
    rows: 3
    cols: 6
    calibration: even
```

### Panel 滚动方向

Panel 支持四种滚动方向（`scroll_direction` 字段），影响对齐判断时的行/列容差：

| 方向 | 说明 |
|------|------|
| `vertical` | 纵向滚动，允许行数少 1（默认） |
| `horizontal` | 横向滚动，允许列数少 1 |
| `both` | 双向滚动，行/列都允许少 1 |
| `none` | 不可滚动，行/列必须精确匹配 |

**约束**：`rows=1` 禁止 `vertical`/`both`，`cols=1` 禁止 `horizontal`/`both`。

```yaml
# 横向滚动面板
panels:
  - key: action_bar
    rows: 1
    cols: 6
    scroll_direction: horizontal
```

### regions 型（规划中）

`type=regions` 允许将多个已定义的 Region 合并到一个 Panel 中统一管理，寻址方式为 `[name]`：

```yaml
# 未来规划
panels:
  - key: equip_slots
    type: regions
    regions: [weapon, armor, accessory]
```

> 详见 [01-scene-layout-definition.md](../34-scene/01-scene-layout-definition.md)。

### 自对齐机制

Panel 使用图像自对齐算法确定实际网格间距：

1. 对 Panel 区域截图
2. 通过图像处理算法（方差分析）检测实际网格间距
3. 计算每个格子的中心坐标并缓存
4. 后续 `click [scene].[panel][r][c]` 直接查缓存，无需重复截图

首次对 Panel 执行 `click [scene].[panel][r][c]` 时**自动触发**对齐，也可手动调用 `align [scene].[panel]`。对齐结果缓存在 `_alignment_cache` 中，同一 Panel 只需对齐一次。窗口缩放、分辨率变化后引擎会自动检测并重新对齐。
