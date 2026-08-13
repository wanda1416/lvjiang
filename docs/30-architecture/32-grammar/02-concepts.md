# 场景、布局与 Panel

DSL 指令操作的核心对象是**场景（Scene）**、**布局（Layout）**中定义的 **Area** 和 **Action**。理解这三个概念及其关系，是正确使用 DSL 的前提。

## 目录

- [一、场景（Scene）](#一场景scene)
- [二、布局（Layout）](#二布局layout)
  - [Area — 空间实体](#area--空间实体)
  - [Action — 行为实体](#action--行为实体)
- [三、Panel — 可寻址容器](#三panel--可寻址容器)
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

DSL 通过 `[scene_name]` 引用场景。场景名支持三种形式（见 [01-basics.md](01-basics.md#二引用模型)）：

```
[equip_weapon_detail]       # 括号常量
"equip_weapon_detail"       # 字符串常量
$scene_name                 # 变量引用
```

## 二、布局（Layout）

布局定义 Area 和 Action 的**具体坐标**。每个场景通过 `required_layout` 关联一个 Layout JSON 文件（位于 `config/system/layouts/`），Layout 负责将场景中的抽象名称映射到屏幕上的实际坐标。

### Area — 空间实体

Area 是有位置和形状的空间实体，DSL 的 `click`、`scan`、`recognize` 指令都操作 Area。

| 类型 | 说明 | DSL 支持 |
|---|---|---|
| **Region** | 有面积的矩形区域（有宽高） | ✅ `click` / `scan` / `recognize` |
| **Point** | 坐标点（无面积） | ✅ `click`（`scan` / `recognize` 当前仅支持 Region，未来可扩展） |

DSL 通过 `[scene].[area]` 引用 Area：

```
click [equip_weapon_detail].[affix_1]     # 点击 Region 中心
scan [equip_weapon_detail].[affix_1]      # 对 Region 执行 OCR
```

### Action — 行为实体

Action（Arrow）是定义一次**拖拽交互**的行为实体，与 Area 完全正交。Action 纯 Layout 层定义，不在 Scene 层声明。

| | Area | Action |
|---|------|--------|
| 本质 | **空间实体**（有位置、形状） | **行为实体**（定义一次交互） |
| 定义层 | Scene 层声明，Layout 层绑定坐标 | 纯 Layout 层定义 |
| 操作指令 | `click` / `scan` / `recognize` | `drag` |
| 互斥 | 不能 `drag` 一个 Area | 不能 `scan` / `click` 一个 Action |

DSL 通过 `[scene].[action]` 引用 Action：

```
drag [scene].[arrow]                   # 执行 Arrow 定义的拖拽
drag [scene].[arrow] 0.5 hold 0.2      # 指定时长 + 按住
```

## 三、Panel — 可寻址容器

Panel 是 Scene 层定义的一种特殊 Area，作为**可寻址的容器**存在。Panel 通过 `type` 字段区分内部结构：

| type | 语义 | 寻址方式 | 当前状态 |
|------|------|----------|----------|
| `grid` | 行列网格 | `[r][c]` | ✅ 已实现 |
| `regions` | 多个 Region 的集合 | `[name]` | 🔜 规划中 |

### grid 型（已实现）

当前所有 Panel 均为 `type=grid`（默认值，可省略），具有 `rows`/`cols` 属性，通过 `[r][c]` 二维索引寻址。`r`/`c` 从 1 开始计数。

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
