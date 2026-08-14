# DSL 交互指令

> 操作对象（Entity / CoordRef / Panel）的概念说明见 [02-concepts.md](02-concepts.md)。

## 目录

- [一、click — 点击](#一click--点击)
- [二、drag — 拖拽](#二drag--拖拽)
- [三、wait — 等待](#三wait--等待)
- [四、wait stable — 等待画面稳定](#四wait-stable--等待画面稳定)
- [五、align — 面板自对齐](#五align--面板自对齐)
- [六、screenshot — 截图](#六screenshot--截图)
- [七、失败语义](#七失败语义)

## 一、click — 点击

点击屏幕上的 Area（Region 或 Point）或 Panel 格子，或 CoordRef 变量。

**语义**：`click` 将目标解析为 CoordRef，取其中心点（+ 抖动）后点击。

**语法**：

```
click [name].[name]            # 配置引用（场景.区域）
click [scene].[panel][r][c]    # Panel 三级索引（点击格子中心）
click [scene].[panel]          # 点击 Panel 中心（无 row/col）
click $var                     # 点击 CoordRef / find 产出的文字区域
```

**示例**：

```
click [scene].[region]         # 常量.常量
click [scene].$var             # 常量.变量
click $scene.[region]          # 变量.常量
click $scene.$var              # 变量.变量
click (0.52, 0.38)             # 画布归一化坐标（录制产物）

# 点击 find 指令找到的文字位置
find as $found by contains "调律"
click $found                   # 直接点击找到的文字

# Panel 三级索引
click [bag_equip_detail].[bag_grid][1][1]     # 点击第 1 行第 1 列的格子中心
click [scene].[panel][$row][$col]             # 动态行列（变量指定）

# 点击 Panel 中心
click [general_action].[actions]              # 点击面板中心（用于点击空白处）
```

**说明**：

- `[scene].[area]`：从场景配置中取出 Area 的中心坐标并点击（Region 优先，未命中回退 Point，再回退 Panel）
- `[scene].$var`：Area 名在运行时由变量值决定。引擎会先从 `_coord_meta` 查找该 key 对应的 Region（由 scan/recognize 自动存入），找到则直接点击其屏幕坐标；找不到则回退到场景配置中查找同名 Area
- `click (rx, ry)`：**画布归一化坐标模式**。`rx`/`ry ∈ [0,1]`，表示画布内容区域内相对位置。回放时按「窗口偏移 + 画布原点 + 比例 × 画布尺寸」动态反算屏幕坐标，窗口缩放/移动后仍准确。这是录制功能（F8）生成的坐标字面量，可直接剪切复用，与 `scene.area` 引用形式混用
- `[name]` 表示配置引用，`$var` 表示变量引用。`"text"` 始终表示字符串数据，不用于配置引用
- `[scene].[panel][r][c]`：**Panel 三级索引模式**。`r`/`c` 从 1 开始计数。首次执行时自动触发图像自对齐（`align`），缓存格子中心坐标；后续点击直接查缓存。详见 [align](#五align--面板自对齐)

> **重要区分**：`scene`/`panel` 是配置引用（用 `[name]` 或 `$var`），而 `row`/`col` 是面板索引（用 `[INT]` 或 `[$var]`）。row/col 不是 area 引用，不支持字符串，`["a"]` 语法不合法。正确示例：`[scene].$panel_ref[$row][5]`，错误示例：`[scene].$panel_ref["a"]["b"]`。
- `[scene].[panel]`：**Panel 中心点击模式**。点击面板中心点，用于点击空白处或重置面板状态
- `click $var`：**CoordRef / find 结果点击模式**。`$var` 可以是：
  - `CoordRef` 类型（包括 `RectCoordRef` / `CircleCoordRef`）：点击其中心点（+ 抖动）
  - `find` 指令产出的 `FoundRegion`：点击文字中心坐标
  - 变量未定义或不是可点击类型时报错。详见 [04-3-find.md](04-3-find.md)

## 二、drag — 拖拽

执行拖拽动作。支持 Arrow 拖拽、Panel/Region 翻页、Point 点对拖拽。

**语义**：`drag` 将目标解析为 Entity：
- **Arrow** → 直接执行其定义的拖拽（起点 → 终点）
- **Region / Panel** → 隐式转为单向 Arrow（利用 w/h 计算终点）
- **Point** → 不允许单独 drag（无 w/h 无法计算终点），必须使用两点模式

**语法**：

```
# Arrow 拖拽（基于 Layout Action 定义）
drag literal.literal                            # literal 可以是 [] 或 $var
drag literal.literal 0.5                        # 指定时长 0.5s
drag literal.literal [0.3, 0.8]                 # 随机时长范围 0.3~0.8s
drag literal.literal 0.5 hold 0.2               # 拖拽后按住 0.2s

# Panel/Region 拖拽（翻页）
drag [scene].[panel][r][c] up [n]              # 上翻 n 行（默认 1）
drag [scene].[panel][r][c] down [n]            # 下翻 n 行（默认 1）
drag [scene].[panel][r][c] left [n]            # 左翻 n 列（默认 1）
drag [scene].[panel][r][c] right [n]           # 右翻 n 列（默认 1）
drag [scene].[panel][r][c] up $var             # 上翻 $var 行（动态）
drag [scene].[panel][r][c] down $var           # 下翻 $var 行（动态）

# Panel/Region 中心拖拽（无 row/col）
drag [scene].[panel] up [n]                    # 从面板中心上翻
drag [scene].[region] up [n]                   # 从区域中心上翻（使用区域高度作为步长）
drag [scene].[region]                          # 从区域中心默认向上拖拽

# 点对拖拽
drag [scene1].[point1] [scene2].[point2]       # 两点之间拖拽
```

**示例**：

```
# Arrow 拖拽
drag [scene].[arrow]                            # 常量.常量
drag [scene]."arrow"                            # 常量.字符串
drag [scene].$var                               # 常量.变量
drag $scene.[arrow]                             # 变量.常量
drag [scene].[arrow] 0.5                        # 指定时长
drag [scene].[arrow] 0.5 hold 0.2               # 拖拽后按住
drag (0.52, 0.45) (0.52, 0.22)                  # 画布归一化坐标（录制产物）
drag (0.52, 0.45) (0.52, 0.22) 0.4              # 坐标模式 + 指定时长

# Panel 拖拽（翻页）
drag [bag_equip_detail].[bag_grid][1][1] down   # 下翻 1 行
drag [bag_equip_detail].[bag_grid][1][1] up $n  # 上翻 $n 行（动态）
drag [bag_equip_detail].[bag_grid][1][1] right 2 # 右翻 2 列

# Panel/Region 中心拖拽
drag [general_action].[actions] down            # 从面板中心下翻
drag [scene].[scroll_area] up 2                 # 从区域中心上翻 2 次

# 点对拖拽
drag [scene].[point_a] [scene].[point_b]        # 两点之间拖拽
```

**参数说明**：

| 参数 | 语法 | 说明 |
|---|---|---|
| 时长 | 数字 或 `[min, max]` | 拖拽持续时间。省略则使用默认值；`[min, max]` 在范围内随机取值 |
| hold | `hold <秒数>` | 可选。到达目标位置后按住不放的时长 |
| 方向 | `up` / `down` / `left` / `right` | Panel/Region 拖拽方向。`up`/`down` 按行计算，`left`/`right` 按列计算 |
| 距离 | 数字 或 `$var` | Panel/Region 拖拽距离。`up`/`down` 为行数，`left`/`right` 为列数。省略默认为 1 |

**坐标模式**：`drag (rx1, ry1) (rx2, ry2) [时长]` 直接给出起点/终点的画布归一化坐标（与 `click (rx, ry)` 同源），无需引用 Arrow。这是录制功能（F8）生成的形式，可直接剪切复用。

**Panel/Region 拖拽语义**：

- `up` = 手指从下往上划 = 内容向下滚动 = 看上面的内容（类似 PageUp）
- `down` = 手指从上往下划 = 内容向上滚动 = 看下面的内容（类似 PageDown）
- `left` / `right` 类推
- 拖拽起点为指定格子/区域中心
- Panel 垂直拖拽距离 = 行数 × 单行高度（panel 高度 / 行数）
- Panel 水平拖拽距离 = 列数 × 单列宽度（panel 宽度 / 列数）
- Region 拖拽距离 = 区域高度/宽度 × 距离参数

**查找顺序**：

- `drag [scene].[key]`：先查 arrow，未命中再查 region，最后查 point
- `drag [scene].[key] direction`：先查 panel，未命中再查 region
- `drag [scene].[point]`（单独）：**不允许**，Point 无 w/h 无法计算终点，请使用两点模式 `drag [scene].[point1] [scene].[point2]`

## 三、wait — 等待

暂停执行指定时间。支持命名延迟、固定秒数、动态变量和随机范围四种形式。

**语法**：

```
wait @<delay_name>      # 命名延迟（从延迟配置中读取，@ 前缀必须）
wait <秒数>             # 固定等待
wait $var               # 动态等待（变量值为秒数）
wait (min, max)         # 随机范围等待（在 min~max 秒之间随机取值）
```

**示例**：

```
wait @page_refresh     # 命名延迟（从配置读取）
wait 1.5                    # 固定等待 1.5 秒
wait $interval              # 动态等待，$interval 的值是秒数
wait (1, 2)                 # 随机等待 1~2 秒
```

**说明**：

- `$var` 的值可以是数字（固定等待）或元组 `(min, max)`（随机范围等待）
- 配合 `eval $var = (min, max)` 或 `default $var = (min, max)` 赋值后使用
- `wait @<delay_name>` 引用的命名延迟必须已在「配置管理 → 等待参数」中定义，否则报错终止（详见[失败语义](#六失败语义)）
- 命名延迟必须使用 `@` 前缀，裸标识符（如 `wait page_refresh`）是语法错误

## 四、wait stable — 等待画面稳定

连续截图对比，当画面在指定时长内没有明显变化时，认为「加载完成 / 动画结束」，继续执行下一步。适用于加载时间不确定的场景，替代固定延迟等待。

**语法**：

```
wait stable <timeout>                                                      # 基本形式
wait stable <timeout> threshold <value>                                    # 自定义差异阈值
wait stable <timeout> interval <value>                                     # 自定义截图间隔
wait stable <timeout> duration <value>                                     # 自定义稳定持续时长
wait stable <timeout> least <value>                                        # 自定义最低等待时间
wait stable <timeout> threshold <v> interval <v> duration <v> least <v>    # 任意组合
```

**参数值形式**：

所有参数（timeout / threshold / interval / duration / least）均支持三种形式：

| 形式 | 示例 | 说明 |
|------|------|------|
| 字面量数字 | `wait stable 5` | 直接指定秒数 |
| `@命名延迟` | `wait stable @page_load` | 从延迟配置中读取（取范围中值） |
| `$变量引用` | `wait stable $my_timeout` | 运行时从变量读取 |

**参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `timeout` | （必填） | 等待预算秒数；预算内未稳定则记警告并继续执行（不报错） |
| `threshold` | 0.02 | 像素差异率阈值（0~1），低于此值认为「画面没变」 |
| `interval` | 0.3 | 截图对比间隔秒数 |
| `duration` | 0.5 | 画面需持续稳定的时长（秒），连续差异低于阈值达到此时长后返回 |
| `least` | 0.5 | 最低等待秒数，点击后至少等这么久再开始检测稳定，防止转场动画未开始就被误判为「画面已稳定」 |

**示例**：

```
wait stable 5                    # 等待画面稳定，最多 5 秒
wait stable 3 threshold 0.01     # 严格模式，差异 < 1% 视为稳定
wait stable 10 interval 0.5      # 每 0.5 秒检测一次，最多等 10 秒
wait stable 5 duration 1.0       # 画面需连续稳定 1 秒才算完成
wait stable 5 least 1.0          # 点击后至少等 1 秒再开始检测（慢加载页面）
wait stable 10 threshold 0.03 interval 0.5 duration 1.0 least 0.3  # 完整参数

# 也可作为 click/drag 的内联等待子句（before/after/around 均可）
click [activity_jianghu].[btn] after wait stable 8 least 0.5
```

**说明**：

- 工作原理：每 `interval` 秒截图一次，与上一帧计算像素差异率（`cv2.absdiff.mean() / 255`），连续差异低于 `threshold` 的时长达到 `duration` 后返回
- `timeout` 是等待预算而非硬性断言：预算耗尽仍未稳定时记录警告并继续执行。游戏画面常有持续动画，永远达不到阈值是常态；实际书写时建议把 `timeout` 设得宽裕些（如 8~10），至少不会比固定等待差
- `least` 期间只截图建立基准，不进行稳定判定。防止点击后转场动画尚未开始，画面恰好「没变」就被误判为稳定
- 等待期间持续检查停止标志，F10 / 停止按钮可立即中断
- 可作为 click/drag 的后缀等待子句：`click ... after wait stable <timeout> ...`，与普通 wait 子句展开规则一致（around 前后各执行一次）
- 所有参数支持字面量数字、`@命名延迟`、`$变量引用` 三种形式
- `threshold`、`interval`、`duration` 和 `least` 可以任意顺序书写

## 五、align — 面板自对齐

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

**说明**：

- 对齐结果缓存在 `_alignment_cache` 中，同一 Panel 只需对齐一次
- 窗口缩放、分辨率变化后需要重新对齐（引擎会自动检测）
- 对齐算法基于方差分析，自动检测网格间距
- 详见 [02-concepts.md — 自对齐机制](02-concepts.md#自对齐机制)

## 六、screenshot — 截图

截取当前画面并保存到 `logs/image/` 目录，用于调试和记录。

**语法**：

```
screenshot
```

**示例**：

```
# 在关键步骤后截图，便于调试
scan [equip_detail].[affixes] as $affixes
screenshot                    # 保存当前画面
click [equip_detail].[confirm]
```

**说明**：

- 文件命名格式：`image_YYYYMMDD_HHMMSS_mmm.png`（精确到毫秒）
- 保存目录：`logs/image/`（自动创建）
- 日志输出保存的文件名，便于事后查找
- 截图失败时仅记录警告，不中断执行

## 七、失败语义

交互指令的失败按原因分两类，不再混作一谈：

**配置错误 → 抛错终止**（脚本或布局配错，继续执行只会在错误的页面上乱点）：

| 情况 | 报错时机 |
|---|---|
| Region / Point / Panel 在当前布局未绑定坐标 | `click` |
| Arrow / Region / Point 在当前布局未绑定坐标 | `drag` |
| Point 单独作为 drag 目标（无 w/h 无法计算终点） | `drag` |
| Arrow 的起点或吸附态终点 Point 丢失 | `drag` |
| `$var` 未定义或取到空值 | `click $scene.$area` / `drag $scene.$arrow` |
| Panel 未在布局中定义 | `click` / `drag` / `align` |
| Panel 行列索引不是数值、拖拽距离不是数值 | `click` / `drag` |
| `wait @<delay_name>` 的命名延迟未定义 | `wait` |
| 拿不到截屏尺寸（截屏后端不可用） | 任何需要换算坐标的指令 |

**运行时状态 → 记日志后跳过**（不是配置问题，脚本本来就要靠它判断边界）：

- Panel 索引越界：脚本遍历网格时用越界作为终止条件
- Panel 对齐失败（页面未加载完、列表为空，`detect_grid` 检测不到 slot）

抛出的错误可以被 `try` / `catch` 捕获，需要容错的片段自行包裹即可。

上表前两行（未绑定的 Region / Point / Arrow / Panel）在**执行前**就会被静态
检查拦下：引擎解析完 `.wf` 先把全部静态引用与当前布局比一遍，一次性列出
所有问题（含文件名与行号），不等执行到那一行；这一层报错在 `try` 之前，
捕获不到。详见 [33-engine/02-static-check.md](../33-engine/02-static-check.md)。
