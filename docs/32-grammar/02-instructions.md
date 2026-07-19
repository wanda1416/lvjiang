# DSL 指令集

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

---

## 一、click — 点击

点击屏幕上的 Area（Region 或 Point）。

**语义**：`click` 是 `click_func(scene, area)` 的语法糖，接收两个常量参数查坐标表。

**语法**：

```
click literal.literal          # literal 可以是 []、"" 或 $var
```

**示例**：

```
click [scene].[region]         # 常量.常量
click [scene].$var             # 常量.变量
click $scene.[region]          # 变量.常量
click $scene.$var              # 变量.变量
click "scene"."region"         # 字符串常量（等价于 [scene].[region]）
click (0.52, 0.38)             # 画布归一化坐标（录制产物）
```

**说明**：

- `[scene].[area]`：从场景配置中取出 Area 的中心坐标并点击（Region 优先，未命中回退 Point）
- `[scene].$var`：Area 名在运行时由变量值决定。引擎会先从 `_coord_meta` 查找该 key 对应的 Region（由 scan/recognize 自动存入），找到则直接点击其屏幕坐标；找不到则回退到场景配置中查找同名 Area
- `click (rx, ry)`：**画布归一化坐标模式**。`rx`/`ry ∈ [0,1]`，表示画布内容区域内的相对位置。回放时按「窗口偏移 + 画布原点 + 比例 × 画布尺寸」动态反算屏幕坐标，窗口缩放/移动后仍准确。这是录制功能（F8）生成的坐标字面量，可直接剪切复用，与 `scene.area` 引用形式混用
- `[]` 和 `""` 在非赋值语境等价，都表示静态常量

## 二、drag — 拖拽

执行基于 Arrow 定义的拖拽动作。Arrow 是 Layout Action 层定义的一次拖拽交互（起点 → 终点）。

**语义**：`drag` 是 `drag_func(scene, action, ...)` 的语法糖。场景名和 Arrow 名都支持 `[]`/`""`/`$var` 三种形式。

**语法**：

```
drag literal.literal                            # literal 可以是 []、"" 或 $var
drag literal.literal 0.5                        # 指定时长 0.5s
drag literal.literal [0.3, 0.8]                 # 随机时长范围 0.3~0.8s
drag literal.literal 0.5 hold 0.2               # 拖拽后按住 0.2s
```

**示例**：

```
drag [scene].[arrow]                            # 常量.常量
drag [scene]."arrow"                            # 常量.字符串
drag [scene].$var                               # 常量.变量
drag $scene.[arrow]                             # 变量.常量
drag $scene.$arrow                              # 变量.变量
drag [scene].[arrow] 0.5                        # 指定时长
drag [scene].[arrow] 0.5 hold 0.2               # 拖拽后按住
drag (0.52, 0.45) (0.52, 0.22)                  # 画布归一化坐标（录制产物）
drag (0.52, 0.45) (0.52, 0.22) 0.4              # 坐标模式 + 指定时长
```

**参数说明**：

| 参数 | 语法 | 说明 |
|---|---|---|
| 时长 | 数字 或 `[min, max]` | 拖拽持续时间。省略则使用默认值；`[min, max]` 在范围内随机取值 |
| hold | `hold <秒数>` | 可选。到达目标位置后按住不放的时长 |

**坐标模式**：`drag (rx1, ry1) (rx2, ry2) [时长]` 直接给出起点/终点的画布归一化坐标（与 `click (rx, ry)` 同源），无需引用 Arrow。这是录制功能（F8）生成的形式，可直接剪切复用。

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

## 四、scan — OCR 扫描

对指定场景中的 Area 截图，逐 Region 裁切后执行 OCR 文字识别，结果存入变量。

**语义**：`scan` 是 `scan_func(scene, area)` 的语法糖。场景名和 Area 名都可以是常量或变量。

**语法**：

```
scan scene_name as $var                    # 扫描场景所有 Region
scan scene_name.[a1, a2, ...] as $var      # 仅扫描指定 Area
scan scene_name.$var as $result            # 动态 Area（变量指定单个 Area 名）

# 带 by 子句（短路识别，返回字段名）
scan scene_name.[a1, a2, ...] as $var by equals "文本"
scan scene_name.[a1, a2, ...] as $var by contains "文本"
scan scene_name.[a1, a2, ...] as $var by equals_any $list
scan scene_name.[a1, a2, ...] as $var by contains_any $list

# scene_name 可以是：
#   [scene]    — 括号常量
#   "scene"    — 字符串常量（等价于 [scene]）
#   $var       — 变量引用（运行时解析为场景名）
```

**示例**：

```
# 静态场景名
scan [equip_weapon_detail] as $scan_result
scan [equip_weapon_detail].[affix_1, affix_2] as $scan_result

# 动态场景名
eval $scene_name = "equip_weapon_detail"
scan $scene_name.[affix_1] as $scan_result
scan $scene_name.$region as $result

# 字符串常量场景名
scan "equip_weapon_detail".[affix_1] as $scan_result

# by 子句短路识别
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3] as $key by contains "调律"
if $key
    click [equip_weapon_detail].$key
end
```

**说明**：

- 扫描结果 `$var` 为字典，key 为 Area 名，value 为 OCR 识别文本
- 引擎自动将 Region 坐标元数据存入内部 `_coord_meta`，供后续 `click [scene].$key` 解析坐标
- 场景名支持 `[]`、`""`、`$var` 三种形式，语义等价

### by 子句（短路识别）

`by` 子句附加在 `scan` / `recognize` 末尾，将扫描结果从 **dict** 变为 **str**（首个命中的字段名）。

**语义**：一次截图 → 逐字段识别 → 首个命中即返回字段名，不再存入字典。

**四种匹配模式**：

| 模式 | 说明 | target 类型 |
|---|---|---|
| `equals "文本"` | 字段 OCR 文本完全等于目标 | 字符串 |
| `contains "文本"` | 字段 OCR 文本包含目标子串 | 字符串 |
| `equals_any $list` | 字段 OCR 文本等于列表中任一项 | 列表变量 |
| `contains_any $list` | 字段 OCR 文本包含列表中任一项 | 列表变量 |

**返回值类型契约**：

| 形式 | `$var` 类型 | 含义 |
|---|---|---|
| `scan ... as $var` | `dict` | `{area_key: ocr_text}` |
| `scan ... as $var by ...` | `str` | 首个命中的 area_key，未命中为空字符串 `""` |

**示例**：

```
# 在多个按钮中找到"调律"按钮
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_key by contains "调律"
if $tune_key
    click [equip_weapon_detail].$tune_key
end

# 在材料格中找到指定材料
recognize [equip_tune_detail].[material_1, material_2, material_3] as $slot by equals $material_name
if $slot
    click [equip_tune_detail].$slot
end

# 匹配多个目标之一
eval $keywords = ["攻击", "防御"]
scan [scene].[field_1, field_2, field_3] as $found by contains_any $keywords
```

> **与 find_key 的关系**：`by` 子句是 `scan` + `find_key` 的语法糖。以下两行等价：
> ```
> scan [scene].[a, b, c] as $key by contains "文本"
> scan [scene].[a, b, c] as $tmp / eval $key = find_key($tmp, "文本")
> ```
> 推荐使用 `by` 子句，更简洁且只需一次截图。

## 五、recognize — 图像识别

对指定场景中的 Area 截图，逐 slot 裁切后通过 ORB 特征匹配识别材料类型，结果存入变量。

**语义**：`recognize` 是 `recognize_func(scene, area)` 的语法糖。场景名和 Area 名都可以是常量或变量。

**语法**：

```
recognize scene_name as $var                   # 识别场景所有 slot
recognize scene_name.[a1, a2] as $var          # 仅识别指定 Area
recognize scene_name.$var as $result           # 动态 Area（变量指定单个 Area 名）

# 带 by 子句（短路识别，返回字段名）
recognize scene_name.[a1, a2] as $var by equals "文本"
recognize scene_name.[a1, a2] as $var by contains "文本"

# scene_name 可以是：
#   [scene]    — 括号常量
#   "scene"    — 字符串常量（等价于 [scene]）
#   $var       — 变量引用（运行时解析为场景名）
```

**示例**：

```
# 静态场景名
recognize [material_grid] as $mats
recognize [material_grid].[slot_1, slot_2] as $mats

# 动态场景名
eval $scene = "material_grid"
recognize $scene.[slot_1] as $mats
recognize $scene.$slot_var as $result

# by 子句短路识别（一次截图，找到首个匹配的材料格）
recognize [equip_tune_detail].[
        material_1, material_2, material_3
    ] as $slot by equals $material_name
```

**说明**：

- 识别结果 `$var` 为字典，key 为 Area 名，value 为材料类型名
- 与 `scan` 一样，引擎自动将 slot Region 坐标元数据存入 `_coord_meta`
- 场景名支持 `[]`、`""`、`$var` 三种形式，语义等价
- **by 子句**：与 `scan` 的 `by` 子句完全一致，返回首个命中的字段名（str），详见上方 [by 子句说明](#by-子句短路识别)

## 六、collect — 收集输出

将变量值存入工作流的输出字典。

**语法**：

```
collect $var                      # 以变量名为 key 存入
collect $var as "label"           # 以静态 label 为 key 存入
collect $var as $alias            # 以动态 alias 值为 key 存入
```

**示例**：

```
collect $main_weapon              # output["main_weapon"] = $main_weapon
collect $result as "status"       # output["status"] = $result

# 动态 alias
eval $key_name = "weapon_data"
collect $result as $key_name      # output["weapon_data"] = $result（$key_name 的值作为 key）
```

**说明**：

- 不带 `as`：将 `$var` 的值以变量名为 key 存入输出字典（name reification）
- 带 `as "label"`：以静态字符串为 key 存入
- 带 `as $alias`：以 `$alias` 的运行时值为 key 存入

## 七、eval — 赋值与函数调用

调用内置函数、字面量赋值、初始化字典/列表、字典字段赋值。

**语法**：

```
eval $var = func(args...)         # 函数调用并赋值
eval $var = "字符串"              # 字面量赋值
eval $var = 123                   # 数字赋值
eval $var = {}                    # 初始化空字典
eval $var = ["a", "b"]           # 列表赋值
eval $var.field = value           # 字典字段赋值（单层）
eval $var.f1.f2 = value           # 字典链式字段赋值（自动创建中间层）
eval $var.$key = value            # 动态字段名赋值
eval func(args...)                # 调用函数，丢弃返回值
```

详细说明见 [04-eval-and-functions.md](04-eval-and-functions.md)。

### find_key — 查找字典中匹配项的 key

在 `scan` / `recognize` 产出的字典中查找 value 包含目标文本的项，返回其 key 名。

```
eval $key = find_key($scan_result, "调律")     # 找到则返回 key 名，否则返回 ""
```

配合 `click [scene].$key` 使用：

```
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_scan
eval $tune_key = find_key($tune_scan, "调律")
if $tune_key
    click [equip_weapon_detail].$tune_key
end
```

> **推荐**：上述模式可用 `by` 子句一步完成，无需中间字典变量：
> ```
> scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_key by contains "调律"
> if $tune_key
>     click [equip_weapon_detail].$tune_key
> end
> ```
> `find_key` 仍适用于需要对扫描结果做多次查找或复杂处理的场景。

## 八、call — 调用子工作流

调用另一个 `.wf` 文件作为子工作流，支持传入参数和提取返回值。

**语法**：

```
call "sub.wf"                                       # 简单调用
call "sub.wf" with $x as "arg1"                     # 传入参数
call "sub.wf" read "key" as $var                    # 提取返回值
call "sub.wf" with $x as "arg1" read "key" as $var  # 传参 + 提取
```

详细说明见 [05-subworkflows.md](05-subworkflows.md)。

## 九、log — 日志输出

输出一条日志消息。参数可以是任何能求值为字符串的表达式。

**语法**：

```
log "消息文本"                    # 输出固定文本
log $var                          # 输出变量值
log $dict.field                   # 输出字段值
log func(args...)                 # 输出函数返回值（如 concat 拼接）
```

**示例**：

```
log "开始执行调律流程"
log $current_slot
log $result.status
log concat("当前槽位：", $slot)
```

控制流与条件表达式详见 [03-control-flow.md](03-control-flow.md)。
