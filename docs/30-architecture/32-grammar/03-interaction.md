# DSL 交互指令

## 操作对象语义

DSL 指令操作两类本质不同的对象：**Area**（空间实体）和 **Action**（行为实体）。

| | Area | Action |
|---|------|--------|
| 本质 | **空间实体**（有位置、形状） | **行为实体**（定义一次交互） |
| 定义层 | Scene 层定义，Layout 层绑定坐标 | 纯 Layout 层定义 |
| 可执行操作 | click / scan / recognize（对空间做的事） | execute（执行该行为） |
| 不可执行操作 | 不能 drag 一个 Area | 不能 scan / click 一个 Action |

两类对象的操作集**完全正交**，不存在跨类操作。

| 指令 | 操作对象 | 对象类型 | 说明 |
|------|----------|----------|------|
| `click [scene].[area]` | **Area** | 空间实体 | 点击区域中心（Region 或 Point） |
| `scan [scene].[area]` | **Area** | 空间实体 | 对区域执行 OCR（当前仅支持 Region，未来可扩展 Point） |
| `recognize [scene].[area]` | **Area** | 空间实体 | 对区域执行图像识别（当前仅支持 Region，未来可扩展 Point） |
| `drag [scene].[action]` | **Action** | 行为实体 | 执行拖拽动作（Arrow） |

> **注**：`scan` / `recognize` 当前实现仅支持 Region 类型的 Area，但语义上允许未来扩展到 Point 类型（对坐标点周围区域做 OCR）。

### Panel — 通用容器

Panel 是 Scene 层定义的一种特殊 Area，作为**可寻址的容器**存在。Panel 通过 `type` 字段区分内部结构：

| type | 语义 | 寻址方式 | 当前状态 |
|------|------|----------|----------|
| `grid` | 行列网格 | `[r][c]` | ✅ 已实现 |
| `regions` | 多个 Region 的集合 | `[name]` | 🔜 规划中 |

当前所有 Panel 均为 `type=grid`（默认值，可省略），具有 `rows`/`cols` 属性，通过 `[r][c]` 二维索引寻址。

```yaml
# 当前（隐式 grid，type 可省略）
panels:
  - key: bag_grid
    rows: 3
    cols: 6

# 未来（显式 type）
panels:
  - key: bag_grid
    type: grid            # 网格型（默认）
    rows: 3
    cols: 6
  - key: equip_slots
    type: regions         # Region 集合型
    regions: [weapon, armor, accessory]
```

> **注**：`type=regions` 允许将多个已定义的 Region 合并到一个 Panel 中统一管理，届时寻址方式将自动适配为 `[name]` 而非 `[r][c]`。详见 [01-scene-layout-definition.md](../34-scene/01-scene-layout-definition.md)。

---

## 一、click — 点击

点击屏幕上的 Area（Region 或 Point）或 Panel 格子。

**语义**：`click` 是 `click_func(scene, area)` 的语法糖，接收两个常量参数查坐标表。

**语法**：

```
click literal.literal          # literal 可以是 []、"" 或 $var
click [scene].[panel][r][c]    # Panel 三级索引（点击格子中心）
```

**示例**：

```
click [scene].[region]         # 常量.常量
click [scene].$var             # 常量.变量
click $scene.[region]          # 变量.常量
click $scene.$var              # 变量.变量
click "scene"."region"         # 字符串常量（等价于 [scene].[region]）
click (0.52, 0.38)             # 画布归一化坐标（录制产物）

# Panel 三级索引
click [bag_equip_detail].[bag_grid][1][1]     # 点击第 1 行第 1 列的格子中心
click [scene].[panel][$row][$col]             # 动态行列（变量指定）
```

**说明**：

- `[scene].[area]`：从场景配置中取出 Area 的中心坐标并点击（Region 优先，未命中回退 Point）
- `[scene].$var`：Area 名在运行时由变量值决定。引擎会先从 `_coord_meta` 查找该 key 对应的 Region（由 scan/recognize 自动存入），找到则直接点击其屏幕坐标；找不到则回退到场景配置中查找同名 Area
- `click (rx, ry)`：**画布归一化坐标模式**。`rx`/`ry ∈ [0,1]`，表示画布内容区域内相对位置。回放时按「窗口偏移 + 画布原点 + 比例 × 画布尺寸」动态反算屏幕坐标，窗口缩放/移动后仍准确。这是录制功能（F8）生成的坐标字面量，可直接剪切复用，与 `scene.area` 引用形式混用
- `[]` 和 `""` 在非赋值语境等价，都表示静态常量
- `[scene].[panel][r][c]`：**Panel 三级索引模式**。`r`/`c` 从 1 开始计数。首次执行时自动触发图像自对齐（`align`），缓存格子中心坐标；后续点击直接查缓存。详见 [align](#四align--面板自对齐)

## 二、drag — 拖拽

执行基于 Arrow 定义的拖拽动作，或在 Panel 内按方向翻页。

**语义**：`drag` 是 `drag_func(scene, action, ...)` 的语法糖。场景名和 Arrow 名都支持 `[]`/`""`/`$var` 三种形式。

**语法**：

```
# Arrow 拖拽（基于 Layout Action 定义）
drag literal.literal                            # literal 可以是 []、"" 或 $var
drag literal.literal 0.5                        # 指定时长 0.5s
drag literal.literal [0.3, 0.8]                 # 随机时长范围 0.3~0.8s
drag literal.literal 0.5 hold 0.2               # 拖拽后按住 0.2s

# Panel 拖拽（翻页）
drag [scene].[panel][r][c] up [n]              # 上翻 n 行（默认 1）
drag [scene].[panel][r][c] down [n]            # 下翻 n 行（默认 1）
drag [scene].[panel][r][c] left [n]            # 左翻 n 列（默认 1）
drag [scene].[panel][r][c] right [n]           # 右翻 n 列（默认 1）
drag [scene].[panel][r][c] up $var             # 上翻 $var 行（动态）
drag [scene].[panel][r][c] down $var           # 下翻 $var 行（动态）
drag [scene].[panel][r][c] left $var           # 左翻 $var 列（动态）
drag [scene].[panel][r][c] right $var          # 右翻 $var 列（动态）
```

**示例**：

```
# Arrow 拖拽
drag [scene].[arrow]                            # 常量.常量
drag [scene]."arrow"                            # 常量.字符串
drag [scene].$var                               # 常量.变量
drag $scene.[arrow]                             # 变量.常量
drag $scene.$arrow                              # 变量.变量
drag [scene].[arrow] 0.5                        # 指定时长
drag [scene].[arrow] 0.5 hold 0.2               # 拖拽后按住
drag (0.52, 0.45) (0.52, 0.22)                  # 画布归一化坐标（录制产物）
drag (0.52, 0.45) (0.52, 0.22) 0.4              # 坐标模式 + 指定时长

# Panel 拖拽（翻页）
drag [bag_equip_detail].[bag_grid][1][1] down   # 下翻 1 行（显示下方内容）
drag [bag_equip_detail].[bag_grid][1][1] up     # 上翻 1 行（显示上方内容）
drag [bag_equip_detail].[bag_grid][1][1] down 3 # 下翻 3 行
drag [bag_equip_detail].[bag_grid][1][1] up $n  # 上翻 $n 行（动态）
drag [bag_equip_detail].[bag_grid][1][1] left   # 左翻 1 列（显示左侧内容）
drag [bag_equip_detail].[bag_grid][1][1] right 2 # 右翻 2 列
```

**参数说明**：

| 参数 | 语法 | 说明 |
|---|---|---|
| 时长 | 数字 或 `[min, max]` | 拖拽持续时间。省略则使用默认值；`[min, max]` 在范围内随机取值 |
| hold | `hold <秒数>` | 可选。到达目标位置后按住不放的时长 |
| 方向 | `up` / `down` / `left` / `right` | Panel 拖拽方向。`up`/`down` 按行计算，`left`/`right` 按列计算 |
| 距离 | 数字 或 `$var` | Panel 拖拽距离。`up`/`down` 为行数，`left`/`right` 为列数。省略默认为 1 |

**坐标模式**：`drag (rx1, ry1) (rx2, ry2) [时长]` 直接给出起点/终点的画布归一化坐标（与 `click (rx, ry)` 同源），无需引用 Arrow。这是录制功能（F8）生成的形式，可直接剪切复用。

**Panel 拖拽语义**：

- `up` = 手指从下往上划 = 内容向下滚动 = 看上面的内容（类似 PageUp）
- `down` = 手指从上往下划 = 内容向上滚动 = 看下面的内容（类似 PageDown）
- `left` = 手指从左往右划 = 内容向左滚动 = 看左侧的内容
- `right` = 手指从右往左划 = 内容向右滚动 = 看右侧的内容
- 拖拽起点为指定格子中心
- 垂直拖拽距离 = 行数 × 单行高度（panel 高度 / 行数）
- 水平拖拽距离 = 列数 × 单列宽度（panel 宽度 / 列数）

## 三、wait — 等待

暂停执行指定时间。支持命名延迟、固定秒数、动态变量和随机范围四种形式。

**语法**：

```
wait <delay_name>       # 命名延迟（从延迟配置中读取）
wait <秒数>             # 固定等待
wait $var               # 动态等待（变量值为秒数）
wait (min, max)         # 随机范围等待（在 min~max 秒之间随机取值）
```

**示例**：

```
wait page_refresh_wait      # 命名延迟（从配置读取）
wait 1.5                    # 固定等待 1.5 秒
wait 10                     # 固定等待 10 秒
wait $interval              # 动态等待，$interval 的值是秒数
wait (1, 2)                 # 随机等待 1~2 秒
wait (0.5, 1.5)             # 随机等待 0.5~1.5 秒
```

**说明**：

- `$var` 的值可以是数字（固定等待）或元组 `(min, max)`（随机范围等待）
- 配合 `eval $var = (min, max)` 或 `default $var = (min, max)` 赋值后使用

## 四、align — 面板自对齐

对 Panel 进行图像自对齐，计算实际网格间距并缓存格子中心坐标。

**语义**：`align` 是 `align_func(scene, panel)` 的语法糖。首次对 Panel 执行 `click [scene].[panel][r][c]` 时自动触发，无需手动调用。

**语法**：

```
align [scene].[panel]         # 对指定 Panel 执行图像自对齐
```

**示例**：

```
# 手动触发对齐（通常不需要，click 会自动触发）
align [bag_equip_detail].[bag_grid]

# 后续点击直接使用缓存坐标
click [bag_equip_detail].[bag_grid][1][1]     # 不再触发对齐
click [bag_equip_detail].[bag_grid][2][3]     # 直接查缓存
```

**工作原理**：

1. 对 Panel 区域截图
2. 通过图像处理算法（方差分析）检测实际网格间距
3. 计算每个格子的中心坐标并缓存
4. 后续 `click [scene].[panel][r][c]` 直接查缓存，无需重复截图

**说明**：

- 对齐结果缓存在 `_alignment_cache` 中，同一 Panel 只需对齐一次
- 窗口缩放、分辨率变化后需要重新对齐（引擎会自动检测）
- 对齐算法基于方差分析，自动检测网格间距，无需手动指定 `h_span`/`v_span`
