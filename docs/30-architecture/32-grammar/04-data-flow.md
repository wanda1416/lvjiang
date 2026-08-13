# DSL 感知与数据指令

## 目录

- [一、scan — OCR 扫描](#一scan--ocr-扫描)
  - [返回值语义](#返回值语义)
  - [by 子句（短路识别）](#by-子句短路识别)
- [二、recognize — 图像识别](#二recognize--图像识别)
  - [返回值语义](#返回值语义-1)
  - [by 子句](#by-子句-1)
- [三、find — 文字定位](#三find--文字定位)
  - [返回值语义](#返回值语义-2)
  - [搜索区域](#搜索区域)
  - [by 子句](#by-子句-2)
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

### 返回值语义

`scan` 的返回值取决于 **目标类型**（region / panel）和 **是否带 by 子句**，共四种组合：

| 目标 | 无 by | 有 by |
|---|---|---|
| **Region**（一个或多个） | `dict` — `{area_key: ocr_text}` | `str` — 首个命中的 area_key，未命中为 `""` |
| **Panel 整面板** | `dict` — `{行: {列: ocr_text}}` 行列嵌套 | `dict` — `{"row": 行号, "col": 列号}`，未命中为 `{}` |

**Region 无 by**（最常见）：

```
scan [equip_weapon_detail] as $result
# $result = {"affix_1": "攻击+10", "affix_2": "防御+5", ...}
# 可用 $result.affix_1 取值，click [equip_weapon_detail].$key 点击
```

**Region 有 by**（短路匹配）：

```
scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3] as $key by contains "调律"
# $key = "sub_func_2"（首个命中的 area_key）或 ""（未命中）
# 直接 click [equip_weapon_detail].$key
```

**Panel 整面板无 by**（网格 OCR）：

```
scan [general_action].[actions] as $bags   # actions 是 6×2 panel
# $bags = {"1": {"1": "抱拳", "2": "作揖", ...}, "2": {...}}
# 用 $bags.[1].[2] 或 $bags.$r.$c 取值
# 用 [general_action].[actions][1][2] 点击对应格
```

**Panel 整面板有 by**（位置匹配）：

```
scan [general_action].[actions] as $pos by contains "背包"
# $pos = {"row": 1, "col": 2}（首个命中的行列位置）或 {}（未命中）
# 可用 $pos.row、$pos.col 取值
# 可点击：click [general_action].[actions][$pos.row][$pos.col]
```

> 整面板 + by 返回的是**位置**而非文本，与 Region + by 返回字段名的语义不同。

**说明**：

- 引擎自动将 Region 坐标元数据存入内部 `_coord_meta`，供后续 `click [scene].$key` 解析坐标
- 场景名支持 `[]`、`""`、`$var` 三种形式，语义等价
- region 与 panel 同名时 region 优先（保持既有语义）
- `[scene].[panel][行][列]` 单格形式是对整面板结果的 **key 过滤**，结果为该格文本（str）

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

### 返回值语义

`recognize` 的返回值与 `scan` 完全对称，区别仅在于 value 是**材料类型名**而非 OCR 文本：

| 目标 | 无 by | 有 by |
|---|---|---|
| **Region**（一个或多个） | `dict` — `{slot_key: material_type}` | `str` — 首个命中的 slot_key，未命中为 `""` |
| **Panel 整面板** | `dict` — `{行: {列: material_type}}` 行列嵌套 | `dict` — `{"row": 行号, "col": 列号}`，未命中为 `{}` |

**Region 无 by**（最常见）：

```
recognize [material_grid] as $mats
# $mats = {"slot_1": "玄铁", "slot_2": "精金", ...}
# 可用 $mats.slot_1 取值，click [material_grid].$key 点击
```

**Region 有 by**（短路匹配）：

```
recognize [equip_tune_detail].[material_1, material_2, material_3] as $slot by equals $material_name on group "调律材料"
# $slot = "material_2"（首个命中的 slot_key）或 ""（未命中）
```

**Panel 整面板无 by**（网格材料识别）：

```
recognize [bag_item_detail].[bag_grid] as $grid
# $grid = {"1": {"1": "金狗粮", "2": "玄铁", ...}, "2": {...}}
# 用 $grid.[1].[2] 或 $grid.$r.$c 取值
# 用 [bag_item_detail].[bag_grid][1][2] 点击对应格
```

**Panel 整面板有 by**（位置匹配）：

```
recognize [bag_item_detail].[bag_grid] as $pos by equals "金狗粮" on group "食物"
# $pos = {"row": 1, "col": 2}（首个命中的行列位置）或 {}（未命中）
# 可点击：click [bag_item_detail].[bag_grid][$pos.row][$pos.col]
```

### by 子句

`recognize` 的 `by` 子句与 `scan` 完全一致，将结果从 dict 变为 str（首个命中的 slot_key）。
四种匹配模式（`equals`、`contains`、`equals_any`、`contains_any`）和返回值语义参见 [scan by 子句](#by-子句短路识别)。

**说明**：

- 与 `scan` 一样，引擎自动将 slot Region 坐标元数据存入 `_coord_meta`
- 与 `scan` 一样，单一 key 命中 panel 时分派为整面板逐格识别
- 行列数取自对齐结果（实际检测到的网格），而非配置的 rows/cols
- 空格 value 为空字符串 `""`
- region 与 panel 同名时 region 优先
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

### 返回值语义

`find` 的返回值与 `scan`/`recognize` 不同——它始终返回**坐标对象**而非文本或材料名：

| 情况 | `$var` 类型 | 含义 | 条件判断 |
|---|---|---|---|
| 找到文字 | `FoundRegion` | 匹配区域的画布归一化坐标 | truthy |
| 未找到 | `str` | 空字符串 `""` | falsy |

`FoundRegion` 可直接用于 `click`：

```
find as $found by contains "调律"
# $found = FoundRegion(x=0.5, y=0.3, ...) 或 ""
if $found
    click $found                   # 直接点击找到的文字位置
end
```

与 `scan`/`recognize` 的对比：

| 指令 | 无 by | 有 by |
|---|---|---|
| `scan` | `dict` — `{area_key: ocr_text}` | `str` — 首个命中的 area_key |
| `recognize` | `dict` — `{slot_key: material_type}` | `str` — 首个命中的 slot_key |
| `find` | — | `FoundRegion` 或 `""` |

> `find` 必须带 `by` 子句（指定搜索目标），没有「无 by」形式。

**示例**：

```
# 全画布搜索，找到后直接点击
find as $found by contains "调律"
if $found
    click $found
end

# 在指定区域内搜索
find [general_action].[btn_area] as $btn by contains "确认"
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
