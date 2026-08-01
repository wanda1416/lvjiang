# DSL 感知与数据指令

## 目录

- [一、scan — OCR 扫描](#一scan--ocr-扫描)
  - [整面板扫描](#整面板扫描)
  - [by 子句（短路识别）](#by-子句短路识别)
- [二、recognize — 图像识别](#二recognize--图像识别)
- [三、find — 文字定位](#三find--文字定位)
  - [搜索区域](#搜索区域)
  - [by 子句](#by-子句)
  - [结果与点击](#结果与点击)
- [四、collect — 收集输出](#四collect--收集输出)
- [五、eval — 赋值](#五eval--赋值)
- [六、call — 调用子工作流](#六call--调用子工作流)
- [七、log — 日志输出](#七log--日志输出)

## 一、scan — OCR 扫描

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
```

**示例**：

```
# 静态场景名
scan [equip_weapon_detail] as $scan_result
scan [equip_weapon_detail].[affix_1, affix_2] as $scan_result

# 动态场景名
eval $scene_name = "equip_weapon_detail"
scan $scene_name.[affix_1] as $scan_result

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

### 整面板扫描

`scan [scene].[key]` 的 key 命中场景的 **panel**（而非 region）时，自动分派为
整面板逐格 OCR：自动 align 对齐后只截一次图，所有格从同一帧裁剪识别。

**结果结构**：行列嵌套 dict，key 为 1-based 数字字符串，用 `$var.[行].[列]` 取值：

```
scan [action_control].[actions] as $bags   # actions 是 6×2 panel
collect $bags                              # {"1": {"1": "抱拳", "2": "作揖", ...}, "2": {...}}

log $bags.[1].[2]                          # 1 行 2 列的文本（静态数字 key）

for r in [1...2]                           # 动态遍历：$r/$c 是 int，自动归一化命中 "1" 字符串 key
    for c in [1...6]
        log $bags.$r.$c
    end
end

if $bags.[1].[2] contains "背包"           # 命中后可直接点对应格
    click [action_control].[actions][1][2]
end
```

**说明**：

- 行列数取自对齐结果（实际检测到的网格），而非配置的 rows/cols
- 空格 value 为空字符串 `""`
- region 与 panel 同名时 region 优先（保持既有语义）
- `[scene].[panel][行][列]` 单格形式是对整面板结果的 **key 过滤**，
  结果为该格文本（str），与 `$var.[行].[列]` 取值格式一致
- 整面板扫描**不支持 by 子句**（嵌套 dict 与短路返回字段名语义不兼容），
  需要短路匹配时用 `[scene].[panel][行][列]` 单格形式
- `recognize [scene].[panel名]` 同样支持整面板分派，结果结构一致，value 为材料类型名；
  单格 `recognize [scene].[panel][行][列]` 结果为该格材料类型名（str）

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

# 匹配多个目标之一
eval $keywords = ["攻击", "防御"]
scan [scene].[field_1, field_2, field_3] as $found by contains_any $keywords
```

> **与 find_key 的关系**：`by` 子句是 `scan` + `find_key` 的语法糖。推荐使用 `by` 子句，更简洁且只需一次截图。

> **未绑定的区域直接报错**：点名的字段（含 `[scene].$var` 动态解析出的 key）
> 若在当前布局里没绑定坐标，`scan` / `recognize` 立即抛错终止，而不是静默跳过
> 退化成「未命中」——否则绑定丢失会被当成正常分支，流程带着错误状态跑下去。
> `[scene].$var` 的变量取到空值时同样报错。

## 二、recognize — 图像识别

对指定场景中的 Area 截图，逐 slot 裁切后通过 ORB 特征匹配识别材料类型，结果存入变量。

**语义**：`recognize` 是 `recognize_func(scene, area)` 的语法糖。场景名和 Area 名都可以是常量或变量。

**语法**：

```
recognize scene_name as $var                   # 识别场景所有 slot
recognize scene_name.[a1, a2] as $var          # 仅识别指定 Area
recognize scene_name.$var as $result           # 动态 Area

# 带 by 子句（短路识别）
recognize scene_name.[a1, a2] as $var by equals "文本"
recognize scene_name.[a1, a2] as $var by contains "文本"

# 带 on group 子句（限定材料分组范围）
recognize scene_name.[a1, a2] as $var on group "分组名"
recognize scene_name.[a1, a2] as $var by equals $name on group "分组名"
```

**示例**：

```
# 静态场景名
recognize [material_grid] as $mats
recognize [material_grid].[slot_1, slot_2] as $mats

# 动态场景名
eval $scene = "material_grid"
recognize $scene.[slot_1] as $mats

# by 子句 + on group
recognize [equip_tune_detail].[
        material_1, material_2, material_3
    ] as $slot by equals $material_name on group "调律材料"
```

**说明**：

- 识别结果 `$var` 为字典，key 为 Area 名，value 为材料类型名
- 与 `scan` 一样，引擎自动将 slot Region 坐标元数据存入 `_coord_meta`
- 与 `scan` 一样，单一 key 命中 panel 时分派为整面板逐格识别（见[整面板扫描](#整面板扫描)）
- **by 子句**：与 `scan` 的 `by` 子句完全一致，返回首个命中的字段名（str）
- **on group 子句**：限定材料识别的分组范围，仅在指定分组的参考材料中匹配。支持字符串常量和变量引用

## 三、find — 文字定位

在屏幕上搜索特定文字，找到后返回该文字所在区域的坐标，供后续 `click` 直接点击。

**语义**：`find` 对当前屏幕截图执行 OCR，搜索目标文字，将匹配区域的画布归一化坐标存入变量。与 `scan`/`recognize` 共享 `scene_target` + `by_clause` 语法体系。

**语法**：

```
# 全画布搜索
find as $var by contains "文字"

# 指定区域搜索
find [scene].[area] as $var by contains "文字"

# 顺序匹配（支持列表）
find as $var by contains_any $list
find [scene].[area] as $var by equals_any $list
```

**示例**：

```
# 全画布搜索，找到后直接点击
find as $found by contains "调律"
if $found
    click $found
end

# 在指定区域内搜索
find [action_control].[btn_area] as $btn by contains "确认"
if $btn
    click $btn
end

# 动态场景和区域
find $scene.$region as $close by contains "关闭"

# 顺序匹配：在多个目标中找第一个命中的
eval $buttons = ["确认", "确定", "OK"]
find as $btn by contains_any $buttons
if $btn
    click $btn
end
```

### 搜索区域

| 形式 | 语法 | 说明 |
|---|---|---|
| 全画布 | `find as $var by ...` | 在整个屏幕截图中搜索 |
| 指定区域 | `find [scene].[area] as $var by ...` | 仅在布局定义的区域内搜索 |
| 指定面板 | `find [scene].[panel] as $var by ...` | 仅在布局定义的面板内搜索（与 region 等价） |
| 动态区域 | `find $scene.$region as $var by ...` | 场景和区域由变量指定 |

指定区域搜索时，`[scene].[area]` 必须在当前布局中绑定坐标（region 或 panel 均可），否则报错。Region 和 Panel 对 find 等价，都提供矩形裁剪区域。

### by 子句

`find` 的 `by` 子句与 `scan`/`recognize` 完全一致，**必填**，指定匹配模式和搜索目标：

| 模式 | 说明 | target 类型 |
|---|---|---|
| `equals "文字"` | OCR 文本完全等于目标 | 字符串 |
| `contains "文字"` | OCR 文本包含目标子串 | 字符串 |
| `equals_any $list` | OCR 文本等于列表中任一项 | 列表变量 |
| `contains_any $list` | OCR 文本包含列表中任一项 | 列表变量 |

`contains_any` / `equals_any` 支持**顺序匹配**：按列表顺序逐个尝试，返回第一个命中的文字位置。

### 结果与点击

`find` 的结果变量是 `FoundRegion` 类型，存储文字区域的画布归一化坐标。可以直接用于 `click`：

```
find as $found by contains "调律"
click $found                   # 直接点击找到的文字位置
```

**结果变量行为**：

| 情况 | 变量值 | 条件判断 |
|---|---|---|
| 找到文字 | `FoundRegion` 对象 | truthy |
| 未找到 | 空字符串 `""` | falsy |

因此可以直接用 `if $found` 判断是否找到。

**与 scan 的关系**：

| 特性 | `find` | `scan` |
|---|---|---|
| 语法风格 | 共享 `scene_target` + `by_clause` | 相同 |
| 返回类型 | `FoundRegion` 或 `""` | `dict` 或 `str`（by 子句） |
| 适用场景 | 文字定位 + 点击 | 批量区域 OCR |
| 可直接点击 | `click $found` | `click [scene].$key` |

## 四、collect — 收集输出

将值存入工作流的输出字典。

**语法**：

```
collect $var                      # 以变量名为 key 存入
collect $var as "label"           # 以静态 label 为 key 存入
collect $var as $alias            # 以动态 alias 值为 key 存入
collect session.field             # 以字段名为 key 存入
collect 0 as $exit_code           # 字面量数字存入
collect "ok" as $result           # 字面量字符串存入
```

**示例**：

```
collect $main_weapon              # output["main_weapon"] = $main_weapon
collect $result as "status"       # output["status"] = $result

# 字面量
collect 0 as $exit_code           # output["exit_code"] = 0.0
collect "ok" as $result           # output["result"] = "ok"

# 动态 alias
eval $key_name = "weapon_data"
collect $result as $key_name      # output["weapon_data"] = $result
```

**说明**：

- 不带 `as`：将源值以变量名/字段名为 key 存入输出字典（name reification）
- 带 `as "label"`：以静态字符串为 key 存入
- 带 `as $alias`：以 `$alias` 的运行时值为 key 存入
- 字面量源必须带 `as` 子句，否则默认 key 为 `"value"`

## 五、eval — 赋值

调用内置函数、字面量赋值、字典字段赋值。

**语法**：

```
eval $var = func(args...)         # 函数调用并赋值
eval $var = "字符串"              # 字面量赋值
eval $var = 123                   # 数字赋值
eval $var = {}                    # 初始化空字典
eval $var = {"k": v}              # 字典字面量（支持嵌套）
eval $var = ["a", "b"]           # 列表赋值
eval $var.field = value           # 字典字段赋值（单层）
eval $var.f1.f2 = value           # 字典链式字段赋值（自动创建中间层）
eval $var.$key = value            # 动态字段名赋值
eval func(args...)                # 调用函数，丢弃返回值
```

> 算术表达式（`+` `-` `*` `/`）和隐式 eval 的详细说明见 [01-basics.md](01-basics.md#四表达式)。

### 字典字面量

eval 赋值右侧支持字典字面量初始化，key 限定为字符串，value 支持多种类型：

```
eval $d = {}                                  # 空字典
eval $d = {"a": "b", "c": "d"}              # 字符串值
eval $d = {"count": 3, "name": $user}       # 数字值 + 变量引用
eval $d = {"nested": {"k": "v"}}            # 嵌套字典
eval $d = {"list": [1, 2, $var]}            # 列表值
```

列表字面量同样支持嵌套字典：

```
eval $list = [{"k": "v"}, {"k2": "v2"}]     # 列表含字典元素
```

内置函数全集见 [06-functions.md](06-functions.md)。

## 六、call — 调用子工作流

调用另一个 `.wf` 文件作为子工作流，支持传入参数和提取返回值。

**语法**：

```
call "sub.wf"                                       # 简单调用
call "sub.wf" with $x as "arg1"                     # 传入参数
call "sub.wf" read "key" as $var                    # 提取返回值
call "sub.wf" with $x as "arg1" read "key" as $var  # 传参 + 提取
```

详细说明见 [07-subworkflows.md](07-subworkflows.md)。

## 七、log — 日志输出

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

控制流与条件表达式详见 [05-control-flow.md](05-control-flow.md)。
