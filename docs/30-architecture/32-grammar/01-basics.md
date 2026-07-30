# DSL 基础约定与变量系统

`.wf` 文件为纯文本工作流描述，存放于 `config/system/workflows/`。支持顺序执行、条件分支、循环、跳转等控制流。

## 目录

- [一、基础约定](#一基础约定)
  - [换行与续行](#换行与续行)
  - [核心指令语义模型](#核心指令语义模型)
  - [常量类型](#常量类型)
- [二、变量系统](#二变量系统)
  - [2.1 声明方式](#21-声明方式)
  - [2.2 变量类型](#22-变量类型)
  - [2.3 字段访问](#23-字段访问)
  - [2.4 类型系统与 null 语义](#24-类型系统与-null-语义)
  - [2.5 引用规则](#25-引用规则)
  - [2.6 运行时状态](#26-运行时状态)
- [三、文件元数据（front-matter）](#三文件元数据front-matter)
  - [3.1 语法](#31-语法)
  - [3.2 示例](#32-示例)
  - [3.3 与 workflows.yaml 的关系](#33-与-workflowsyaml-的关系)

## 一、基础约定

- **注释**：`#` 开头整行为注释
- **空行**：忽略
- **缩进**：自由（解析器不依赖缩进），但建议 4 空格以提升可读性
- **块闭合**：靠 `end` 关键字，不靠缩进
- **字符串**：双引号 `"..."`，内部不支持转义
- **标识符**：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`

### 换行与续行

DSL 默认每行一条语句，但支持两种换行续行方式：

**1. 显式续行** — 行尾 `\` 后紧跟换行，两行拼接为一条：

```
scan [scene].[field_1, field_2, \
    field_3, field_4] as $result
```

**2. 隐式续行** — 在 `{}`、`[]`、`()` 括号内部，换行自动替换为空格，无需 `\`：

```
scan [scene].[field_1, field_2,
    field_3, field_4] as $result

eval $dict = {
    "key1": "value1",
    "key2": "value2"
}
```

> 两种机制可以混用。隐式续行在字符串内的换行不生效（字符串内不支持换行）。
- **两种引用，语义不同**：
  - `[name]` → **静态配置引用**：引用场景名 / Area 名（Region 或 Point）/ Action 名，来自 Scene YAML 和 Layout JSON 定义
  - `$name` → **运行时变量引用**：引用工作流执行过程中的动态变量
  - 在非赋值语境中，`[]` 和 `""` 等价，都表示静态常量
- **内置变量**：
  - 无全局内置变量；通过 `scan [scene] as $var` 声明，后续用 `$var` 引用

### 核心指令语义模型

`click`、`scan`、`recognize`、`drag` 是引擎封装的四个语法糖函数。前三者操作 **Area**（空间实体），后者操作 **Action**（行为实体）：

```
click     → click_func(scene, area)          # 操作 Area（Region 或 Point）
scan      → scan_func(scene, area)           # 操作 Area（当前仅 Region）
recognize → recognize_func(scene, area)      # 操作 Area（当前仅 Region）
drag      → drag_func(scene, action, ...)    # 操作 Action（Arrow）
```

参数的来源有两种：
- `[]` 或 `""` → 编译期常量（直接写在代码里）
- `$var` → 运行时计算的常量（先解析变量值，再当作常量传入）

因此语法上，四大指令的场景名和 Area/Action 名都支持三种形式：

```
# 场景名（scene）
[scene]          # 括号常量
"scene"          # 字符串常量（等价于 [scene]）
$var             # 变量引用（运行时解析）

# Area / Action 名
[area]           # 括号常量
"area"           # 字符串常量
$var             # 变量引用
[f1, f2, ...]    # 多 Area 列表（仅 scan/recognize）

# 示例
scan [scene] as $var                    # 常量场景
scan $scene.[area] as $var              # 变量场景 + 常量 Area
scan "scene".$area as $var              # 字符串场景 + 变量 Area
recognize $config.scene.[slot] as $var  # 字段访问作为场景名
drag [scene].[arrow]                    # Action 名（Arrow）
click [scene].[panel][1][1]             # Panel 三级索引（点击格子中心）
drag [scene].[panel][1][1] down 3       # Panel 拖拽翻页
align [scene].[panel]               # 面板自对齐
```

变量只是**延迟求值的常量**，最终传给函数的都是字符串。

### 常量类型

| 类型 | 语法 | 示例 | 可用位置 |
|---|---|---|---|
| 字符串 | `"..."` | `"武器"`, `"head"` | log、contains/equals 比较、eval 参数、collect alias/source、call 路径、eval 字面量赋值 |
| 数字 | 整数或小数，支持负号 | `3`, `0.5`, `-10`, `-3.14` | loop 次数、wait 秒数、drag/hold 时长、drag 行数、数值比较、eval 字面量赋值、collect source、函数参数 |
| 布尔 | `true` / `false` | `eval $flag = true` | eval 字面量赋值、字典值、列表元素 |
| 空值 | `null` | `eval $x = null` | eval 字面量赋值、字典值、列表元素 |
| 字典 | `{"k": v, ...}` | `{}`, `{"a": "b", "count": 3, "ref": $var}` | eval 字面量赋值（key 为字符串，value 支持字符串、数字、bool、null、变量引用、嵌套字典、列表） |
| 列表 | `[item, ...]` | `["a", "b"]`, `[1, null, true]` | eval 字面量赋值（元素支持字符串、数字、bool、null、变量引用、嵌套字典、列表） |
| 范围元组 | `(min, max)` | `(1, 2)`, `(0.5, 1.5)` | eval 字面量赋值、default 字面量赋值（存储为元组，用于随机等待等场景） |

**函数参数形式**：`func(...)` 的参数支持 `"string"`、数字常量、`$var`、`$var.field` 字段访问四种形式，如 `substr($text, 0, 4)`、`add($count, 1)`。

## 二、变量系统

变量是工作流运行时的动态数据载体，存储在引擎的 `variables` 字典中，通过 `$name` 引用。

### 2.1 声明方式

| 方式 | 语法 | 说明 |
|---|---|---|
| scan 声明 | `scan scene_name as $var` | OCR 扫描结果存入 `$var`（dict，key 为 Area 名）。场景名支持 `[]`/`""`/`$var` |
| eval 函数赋值 | `eval $var = func(args...)` | 内置函数返回值存入 `$var` |
| eval 字面量赋值 | `eval $var = "str"` 或 `eval $var = 42` | 字面量直接存入 `$var` |
| eval 算术赋值 | `eval $var = $a + $b * 2` | 算术表达式求值后存入 `$var`（支持 `+` `-` `*` `/` 和 `()`） |
| eval 列表赋值 | `eval $var = ["a", "b", $c]` | 列表存入 `$var`，元素支持字符串、数字、变量引用、嵌套字典/列表 |
| eval 范围元组 | `eval $var = (1, 2)` | 范围元组存入 `$var`，用于 `wait $var` 随机等待 |
| eval 字典 | `eval $var = {"k": v}` | 字典存入 `$var`，key 为字符串，value 支持字符串、数字、变量引用、嵌套字典/列表；`{}` 为空字典 |
| default 赋值 | `default $var = <literal>` | 仅当变量未从外部传入时才赋值，支持字符串、数字、范围元组等字面量 |
| for 循环变量 | `for item in [a, b, c]` | 每次迭代 `$item` 绑定当前值 |
| call 提取 | `call "sub.wf" read "key" as $var` | 从子工作流输出中提取值 |

**隐式 eval**：任何没有指令关键字开头的语句，解析器自动视为 `eval`。以下两行完全等价：

```
eval $var = "hello"
$var = "hello"               # 隐式 eval，效果完全相同
```

隐式 eval 支持所有 eval 语法：字面量赋值、函数调用、字段赋值、链式赋值等。

变量**无需预先声明**，首次赋值即创建，后续引用即可。

**外部参数注入**：工作流可以通过 `workflows.yaml` 声明参数，由 UI 参数面板注入初始值。详见 [06-subworkflows.md](06-subworkflows.md#工作流参数声明)。也可直接在 `.wf` 文件内用 front-matter 声明（见下一节），便于外部加载。

### 2.2 变量类型

变量的实际类型由赋值来源决定：

| 来源 | 类型 | 示例 |
|---|---|---|
| `scan ... as $var` | `dict` | `$var` = `{"equip_type": "武器", "affix_gong": "会心+10%"}`（key 为 Area 名） |
| `scan ... as $var by ...` | `str` | 首个命中的 Area 名，未命中为 `""` |
| `recognize ... as $var by ...` | `str` | 首个命中的 Area 名，未命中为 `""` |
| `eval $var = to_equipment(...)` | `dict` | 嵌套字典，支持链式字段访问 |
| `eval $var = "hello"` | `str` | 字符串 |
| `eval $var = 42` | `float` | 数字（内部统一为 float） |
| `eval $var = true` | `bool` | 布尔字面量 `true` / `false` |
| `eval $var = null` | `null` | 空值字面量 |
| `eval $var = {}` | `dict` | 空字典，后续可通过 `eval $var.key = value` 填充 |
| `eval $var = {"k": v}` | `dict` | 字典字面量，value 支持字符串、数字、变量引用、嵌套字典/列表 |
| `eval $var = ["a", "b"]` | `list` | 列表，元素支持字符串、数字、变量引用、嵌套字典/列表 |
| `eval $var = (1, 2)` | `tuple` | 范围元组，用于随机等待等场景 |
| `eval $var = $other` | 同 `$other` | 变量引用赋值，类型跟随源变量 |
| `eval $var = $dict.field` | 同字段值 | 字段访问赋值，类型跟随字段值 |
| `for x in [...]` | `str` | 迭代元素为字符串 |

### 2.3 字段访问

当变量为 `dict` 或 `list` 类型时，可通过特定语法访问元素。字段访问既可用于**读取**（右侧），也可用于**赋值**（左侧）。

**字典访问**（用 `.` 表示成员/键访问）：

```
# 读取
$dict.$key               # 动态 key（变量）
$dict."key"              # 静态 key（字符串常量）
$dict.[key]              # 静态 key（括号常量，等价于 "key"）
$dict.field.subfield     # 链式访问，逐层深入嵌套 dict

# 赋值
eval $dict.key = value          # 单层赋值
eval $dict.a.b.c = value        # 链式赋值（自动创建中间层空字典）
eval $dict.$key = value         # 动态 key 赋值
eval $dict."key" = value        # 字符串 key 赋值
eval $dict.[key] = value        # 括号 key 赋值（等价于 "key"）
```

**列表索引**（直接 `[]` 表示索引访问）：

```
$list[$i]                # 动态索引（变量）
$list[0]                 # 静态索引（数字）
```

**语义区分**：`.` 表示成员/键访问（dict），`[]` 直接表示索引访问（list）。字段访问返回的是原始值（可能是 dict、str、int、float），在条件比较时自动转换为对应类型。

### 2.4 类型系统与 null 语义

DSL 支持 6 种值类型：

| 类型 | DSL 字面量 | Python 对应 | 示例 |
|---|---|---|---|
| `null` | `null` | `None` | `eval $x = null` |
| `bool` | `true` / `false` | `True` / `False` | `eval $flag = true` |
| `number` | `123` / `1.5` / `-3` | `float` | `eval $n = 42` |
| `str` | `"hello"` | `str` | `eval $s = "hello"` |
| `dict` | `{"k": "v"}` | `dict` | `eval $d = {"a": 1}` |
| `list` | `[1, 2, 3]` | `list` | `eval $l = [1, "a", null]` |

**null 的产生途径**：

- 显式赋值：`eval $x = null`，字典值 / 列表元素也可为 null
- 未定义变量引用返回 `null`（不回退为变量名字符串）：`eval $x = $undefined_var` → `$x = null`
- 缺失字段 / 列表越界返回 `null`：`eval $val = $dict.missing_key` → `null`
- `input()` 对话框取消返回 `null`

**null 在各上下文的行为**：

| 上下文 | null 行为 | 示例 |
|---|---|---|
| 条件判断 | falsy | `if $null_var` → 不进入 |
| `is_empty` | 视为空 | `$null_var is_empty` → true |
| 算术运算 | 视为 0.0 | `null + 5` → 5.0 |
| `concat` | 视为空字符串 | `concat("a", null, "b")` → "ab" |
| `equals` 比较 | 两侧 null 相等 | `null equals null` → true |
| 字符串字段访问 | 转为空字符串 | 字段链中 null → "" |
| `log` | 显示 "null" | `log $null_var` → 输出 "null" |
| `collect` | 收集 null 值 | `collect $null_var` → output 含 null |

**bool 的使用**：bool 值直接用于条件判断（`if $flag`）；部分内置函数返回 bool，如 `has_key`、`confirm`、`match`。

> **与旧行为的差异**：未定义 `$x` 旧版返回 `"x"` 字符串、`$dict.missing` 旧版返回 `""`，现均返回 `null`。字符串上下文中 `str(null)` = `""` 保持一致，仅极少数依赖变量名回退的脚本需改为显式字符串。

### 2.5 引用规则

- `$name` 在运行时从 `variables` 字典中查找，找不到返回 `null`
- 变量名遵循标识符规则：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`
- 变量作用域为当前工作流文件内，子工作流通过 `with/read` 显式传参，不共享变量

### 2.6 运行时状态

引擎持有三个核心状态：

| 状态 | 类型 | 作用 |
|---|---|---|
| `variables` | `dict` | 运行时变量空间，所有 `$var` 的存储 |
| `output` | `dict` | `collect` 指令的输出缓冲区，工作流结束后返回给调用方 |
| `_coord_meta` | `dict` | scan/recognize 产出的 Area 坐标元数据，供 `click [scene].$key` 解析坐标。引擎内部状态，DSL 不可直接访问 |

**数据流全景**：

```
scan scene_name as $var      ──→  variables[$var] = {area_key: ocr_text}
                                 _coord_meta[$var] = {area_key: Region}

recognize scene_name as $var ──→  variables[$var] = {area_key: material_type}
                                 _coord_meta[$var] = {area_key: Region}

eval $var = func(...)        ──→  variables[$var] = result

collect $var                 ──→  output[var_name] = variables[$var]      # name reification
collect $var as "label"      ──→  output["label"] = variables[$var]       # 静态 alias
collect $var as $alias       ──→  output[resolve($alias)] = variables[$var]  # 动态 alias
collect 0 as $exit_code      ──→  output["exit_code"] = 0.0               # 字面量数字
collect "ok" as $result      ──→  output["result"] = "ok"                 # 字面量字符串

call "sub.wf" read "k" as $v ──→  variables[$v] = sub_output["k"]
```

`output` 是工作流的**唯一对外出口**：`collect` 写入，工作流结束时整体返回。调用方通过 `read` 从中提取值。`_coord_meta` 是引擎内部状态，随 scan/recognize 自动存入，使 `click [scene].$key` 可以解析动态 Area 的坐标。

## 三、文件元数据（front-matter）

`.wf` 文件可在**任意位置（约定放文件顶部）**用 `#%` 前缀声明 YAML 元数据，用于向 GUI 暴露工作流的 **名字 / 所需场景 / 参数（含可选项）**。这样通过「加载工作流」打开的外部 `.wf` 文件，也能像 `workflows.yaml` 中注册的内置工作流一样拥有名字与参数面板。

### 3.1 语法

- 每行以 `#%` 开头（前缀后可紧跟一个空格），其余部分为 YAML 正文
- 由于每行都是 `#` 注释，DSL 引擎将其忽略，文件**仍可直接执行**
- 剥掉 `#%` 前缀后，所有行拼成一段 YAML；**缩进在前缀之后保留**
- schema 与 `workflows.yaml` 中单条 flow 完全一致（`name` / `required_scenes` / `parameters`），无需新概念
- 普通 `#` 注释不会被采集；YAML 非法时仅记录警告并忽略，不影响执行

### 3.2 示例

```
#% name: 单件装备调律
#% required_scenes: [game_main_page, equip_tune_detail]
#% parameters:
#%   - name: target_material
#%     label: 目标材料
#%     type: select
#%     default: 紫色狗粮
#%     options:
#%       - { value: "", label: 不添加 }
#%       - { value: 紫色狗粮, label: 紫色狗粮 }
#%   - name: bag_row
#%     label: 背包行号
#%     type: select
#%     default: "1"
#%     options: ["1", "2", "3"]

# 下面是正常的可执行 DSL
call "subcall/nav_main_to_equip.wf"
click [bag_equip_detail].$equip_slot
```

### 3.3 与 workflows.yaml 的关系

- **内置工作流**：仍以 `workflows.yaml` 为准（相对 `wf_file` 路径），无需迁移
- **外部加载**：GUI「加载工作流」打开任意 `.wf` 时，从其 front-matter 提取名字与参数；缺失字段回退默认值（名字回退为 `[外部] 文件名`，场景/参数回退为空）
- 解析入口：`src/lvjiang/workflows/metadata.py` 的 `parse_metadata` / `build_flow_config`
