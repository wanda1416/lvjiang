# DSL 语言基础

`.wf` 文件为纯文本工作流描述，存放于 `config/system/workflows/`。支持顺序执行、条件分支、循环、跳转等控制流。

## 目录

- [一、词法与值](#一词法与值)
  - [词法规则](#词法规则)
  - [字面量](#字面量)
  - [类型系统](#类型系统)
  - [null 语义](#null-语义)
  - [bool 语义](#bool-语义)
- [二、引用模型](#二引用模型)
  - [两种引用](#两种引用)
  - [参数形式](#参数形式)
  - [字段访问](#字段访问)
- [三、变量](#三变量)
  - [声明与赋值](#声明与赋值)
  - [类型推导](#类型推导)
  - [作用域与查找](#作用域与查找)
- [四、表达式](#四表达式)
  - [算术表达式](#算术表达式)
  - [函数调用](#函数调用)

## 一、词法与值

### 词法规则

- **注释**：`#` 开头整行为注释
- **空行**：忽略
- **缩进**：自由（解析器不依赖缩进），但建议 4 空格以提升可读性
- **块闭合**：靠 `end` 关键字，不靠缩进
- **字符串**：双引号 `"..."`，内部不支持转义
- **标识符**：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`

**换行与续行**：DSL 默认每行一条语句，支持两种续行方式：

1. **显式续行** — 行尾 `\` 后紧跟换行，两行拼接为一条：

```
scan [scene].[field_1, field_2, \
    field_3, field_4] as $result
```

2. **隐式续行** — 在 `{}`、`[]`、`()` 括号内部，换行自动替换为空格，无需 `\`：

```
eval $dict = {
    "key1": "value1",
    "key2": "value2"
}
```

> 两种机制可以混用。隐式续行在字符串内的换行不生效（字符串内不支持换行）。

### 字面量

| 类型 | 语法 | 示例 |
|---|---|---|
| 字符串 | `"..."` | `"武器"`, `"head"` |
| 数字 | 整数或小数，支持负号 | `3`, `0.5`, `-10`, `-3.14` |
| 布尔 | `true` / `false` | `eval $flag = true` |
| 空值 | `null` | `eval $x = null` |
| 字典 | `{"k": v, ...}` | `{}`, `{"a": "b", "count": 3, "ref": $var}` |
| 列表 | `[item, ...]` | `["a", "b"]`, `[1, null, true]` |
| 泛化元组 | `(min, max)` | `(1, 2)`, `(0.5, 1.5)`, `($a, $b)`, `(1, $b)` |

字典 key 限定为字符串，value 支持字符串、数字、bool、null、变量引用、嵌套字典、列表。列表元素同理。

### 类型系统

DSL 支持 6 种值类型，变量的实际类型由赋值来源决定（动态类型）：

| 类型 | DSL 字面量 | Python 对应 | 示例 |
|---|---|---|---|
| `null` | `null` | `None` | `eval $x = null` |
| `bool` | `true` / `false` | `True` / `False` | `eval $flag = true` |
| `int` | `123` / `-3` | `int` | `eval $n = 42` |
| `float` | `1.5` / `-3.14` | `float` | `eval $f = 1.5` |
| `str` | `"hello"` | `str` | `eval $s = "hello"` |
| `dict` | `{"k": "v"}` | `dict` | `eval $d = {"a": 1}` |
| `list` | `[1, 2, 3]` | `list` | `eval $l = [1, "a", null]` |

> 数字保持原始类型：整数字面量 `1` 为 int，小数字面量 `1.0` 为 float。算术运算保持类型传播（int+int→int，int+float→float）。

### null 语义

**产生途径**：

- 显式赋值：`eval $x = null`
- 未定义变量引用返回 `null`（不回退为变量名字符串）：`eval $x = $undefined_var` → `$x = null`
- 缺失字段 / 列表越界返回 `null`：`eval $val = $dict.missing_key` → `null`
- `input()` 对话框取消返回 `null`

**各上下文行为**：

| 上下文 | null 行为 | 示例 |
|---|---|---|
| 条件判断 | falsy | `if $null_var` → 不进入 |
| `is_empty` | 视为空 | `$null_var is_empty` → true |
| 算术运算 | 视为 0（int） | `null + 5` → 5 |
| `concat` | 视为空字符串 | `concat("a", null, "b")` → "ab" |
| `equals` 比较 | 两侧 null 相等 | `null equals null` → true |
| 字符串字段访问 | 转为空字符串 | 字段链中 null → "" |
| `log` | 显示 "null" | `log $null_var` → 输出 "null" |
| `collect` | 未定义变量被跳过 | `collect $undefined` → output 不含该 key |

### bool 语义

bool 值直接用于条件判断（`if $flag`）。部分内置函数返回 bool，如 `has_key`、`confirm`、`match`。

在条件上下文中，以下值为 **falsy**：`null`、`false`、`""`（空字符串）、`0`、`{}`（空字典）、`[]`（空列表）。其余为 **truthy**。

**常见场景**：

| 变量值 | `if $var` | `if not $var` |
|---|---|---|
| `""` / `null` / `0` / `false` | 不进入 | 进入 |
| `"文本"` / `42` / `true` | 进入 | 不进入 |
| `{}` (空字典) | 不进入 | 进入 |
| `{"row": 1, "col": 2}` | 进入 | 不进入 |
| `[]` (空列表) | 不进入 | 进入 |
| `["a", "b"]` | 进入 | 不进入 |

> 整面板 + by 返回 `{"row": r, "col": c}` 或 `{}`，可直接用 `if $pos` 判断是否命中。

## 二、引用模型

### 两种引用

DSL 有两套引用体系，语义完全不同：

| 语法 | 名称 | 含义 | 来源 |
|---|---|---|---|
| `[name]` | 静态配置引用 | 引用场景名 / Area 名 / Action 名 | Scene YAML + Layout JSON |
| `$name` | 运行时变量引用 | 引用工作流执行中的动态变量 | 运行时 `variables` 字典 |

`"text"` 始终表示字符串数据（用于 eval 赋值、log、by 匹配目标、函数参数等），不用于配置引用。

> 场景、Area、Action、Panel 的完整概念说明见 [02-concepts.md](02-concepts.md)。

### 参数形式

四大指令（`click`、`scan`、`recognize`、`drag`）的场景名和 Area/Action 名支持两种形式：

```
# 场景名
[scene]          # 配置引用
$var             # 变量引用（运行时解析）

# Area / Action 名
[area]           # 配置引用
$var             # 变量引用
[f1, f2, ...]    # 多 Area 列表（仅 scan/recognize）

# 示例
scan [scene] as $var                    # 常量场景
scan $scene.[area] as $var              # 变量场景 + 常量 Area
recognize $config.scene.[slot] as $var  # 字段访问作为场景名
drag [scene].[arrow]                    # Action 名（Arrow）
click [scene].[panel][1][1]             # Panel 三级索引
```

变量只是**延迟求值的常量**，最终传给函数的都是字符串。

### 字段访问

当变量为 `dict` 或 `list` 类型时，可通过特定语法访问元素。`.` 表示成员/键访问（dict），`[]` 直接表示索引访问（list）。

**字典访问**：

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
```

**列表索引**：

```
$list[$i]                # 动态索引（变量）
$list[0]                 # 静态索引（数字）
```

**语义区分**：字段访问返回的是原始值（可能是 dict、str、number），在条件比较时自动转换为对应类型。

## 三、变量

变量是工作流运行时的动态数据载体，通过 `$name` 引用。变量**无需预先声明**，首次赋值即创建。

### 声明与赋值

| 方式 | 语法 | 说明 |
|---|---|---|
| eval 字面量 | `eval $var = "str"` / `eval $var = 42` | 字面量赋值 |
| eval 函数 | `eval $var = func(args...)` | 函数返回值赋值 |
| eval 算术 | `eval $var = $a + $b * 2` | 算术表达式赋值 |
| eval 字典 | `eval $var = {"k": "v"}` | 字典赋值 |
| eval 列表 | `eval $var = ["a", "b"]` | 列表赋值 |
| eval 元组 | `eval $var = (1, 2)` | 泛化元组赋值（支持 `($a, $b)` 混合引用） |
| default | `default $var = <literal>` | 仅当变量未从外部传入时赋值 |
| scan | `scan scene as $var` | OCR 扫描结果存入 `$var`（dict） |
| for 循环 | `for item in [a, b, c]` | 每次迭代 `$item` 绑定当前值 |
| call 返回值 | `call $v = proc()` | 从子过程调用中接收返回值 |

**隐式 eval**：任何没有指令关键字开头的语句，解析器自动视为 `eval`。以下两行完全等价：

```
eval $var = "hello"
$var = "hello"               # 隐式 eval，效果完全相同
```

隐式 eval 支持所有 eval 语法：字面量赋值、函数调用、字段赋值、链式赋值等。

**外部参数注入**：工作流可以通过 `workflows.yaml` 声明参数，由 UI 参数面板注入初始值。详见 [07-subworkflows.md](07-subworkflows.md#六工作流参数声明)。也可直接在 `.wf` 文件内用 front-matter 声明。

### 类型推导

| 来源 | 类型 | 示例 |
|---|---|---|
| `eval $var = "hello"` | `str` | 字符串 |
| `eval $var = 42` | `int` | 整数 |
| `eval $var = 1.5` | `float` | 浮点数 |
| `eval $var = true` | `bool` | 布尔 |
| `eval $var = null` | `null` | 空值 |
| `eval $var = {}` / `{"k": v}` | `dict` | 字典 |
| `eval $var = ["a"]` | `list` | 列表 |
| `eval $var = (1, 2)` | `tuple` | 泛化元组（支持数字/变量混合：`($a, $b)`、`(1, $b)`） |
| `scan ... as $var` | `dict` | OCR 结果字典 |
| `scan ... as $var by ...` | `str` | 首个命中的 Area 名 |
| `eval $var = $other` | 同 `$other` | 类型跟随源变量 |
| `for x in [...]` | 原始类型 | 迭代元素保持原始类型（int/str 等） |

### 作用域与查找

- `$name` 在运行时从 `variables` 字典中查找，找不到返回 `null`
- 变量名遵循标识符规则：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`
- 变量作用域为当前工作流文件内，过程调用通过 import/def/call 模块化（见 [07-subworkflows.md](07-subworkflows.md)）

## 四、表达式

### 算术表达式

eval 赋值和 if 条件比较均支持 `+` `-` `*` `/` 四则运算：

```
eval $x = $a + $b                   # 加法
eval $x = $a * $b / 2              # 乘除混合
eval $x = ($a + $b) * ($c - 1)     # 括号改变优先级

if $a + 1 > $b * 2                 # 条件中使用算术表达式
    log "达标"
end
```

**规则**：
- 优先级：`*` `/` 高于 `+` `-`，支持 `()` 改变优先级
- 除法为浮点除（`10 / 3 = 3.333...`），除零返回 `0.0`
- 运算两侧可以是数字、变量引用、字段访问、函数调用
- **字符串拼接**：`+` 任一侧为 str 时自动拼接（如 `"hello" + " world"`、`"item_" + $idx`）

### 函数调用

`func(...)` 的参数支持四种形式：

| 形式 | 示例 |
|---|---|
| 字符串常量 | `substr($text, 0, 4)` |
| 数字常量 | `add($count, 1)` |
| 变量引用 | `has_key($data, $key)` |
| 字段访问 | `concat($result.status, "!")` |

内置函数全集见 [06-functions.md](06-functions.md)。

---

**后续阅读**：

- 场景、布局与 Panel 概念 → [02-concepts.md](02-concepts.md)
- 交互指令（click/drag） → [03.3-mouse.md](03.3-mouse.md)
- 时间与辅助指令（wait/align/screenshot） → [03.2-interaction.md](03.2-interaction.md)
- 基础指令（collect/eval/call/log） → [03.1-basic-commands.md](03.1-basic-commands.md)
- 感知指令（scan/recognize/find/where） → [04-data-flow.md](04-data-flow.md) 概览，详见 04-1 / 04-2 / 04-3
- 控制流与条件表达式 → [05-control-flow.md](05-control-flow.md)
