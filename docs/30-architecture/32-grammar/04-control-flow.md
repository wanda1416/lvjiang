# DSL 控制流与条件表达式

## 目录

- [一、控制流指令总览](#一控制流指令总览)
- [二、if / else — 条件分支](#二if--else--条件分支)
- [三、for — 枚举循环](#三for--枚举循环)
- [四、loop — 计数与条件循环](#四loop--计数与条件循环)
  - [loop N — 计数循环](#loop-n--计数循环)
  - [loop while — 条件为真时循环](#loop-while--条件为真时循环)
  - [loop until — 条件为真时退出](#loop-until--条件为真时退出至少执行一次)
- [五、default — 默认值赋值](#五default--默认值赋值)
- [六、break / continue — 循环控制](#六break--continue--循环控制)
  - [break — 跳出循环](#break--跳出循环)
  - [continue — 跳过当前迭代](#continue--跳过当前迭代)
- [七、try / catch — 异常处理](#七try--catch--异常处理)
- [八、return — 结束工作流](#八return--结束工作流)
- [九、label / goto — 标签跳转](#九label--goto--标签跳转)
- [十、条件表达式](#十条件表达式)
  - [10.1 基础条件](#101-基础条件)
  - [10.2 组合条件](#102-组合条件)
  - [10.3 算术表达式条件](#103-算术表达式条件)

## 一、控制流指令总览

| 指令 | 语法 | 说明 |
|---|---|---|
| if | `if <cond> ... [else ...] end` | 条件分支，可嵌套 |
| for | `for $var in [a, b, c] ... end` 或 `for $var in $list ... end` | 枚举循环，迭代静态列表或列表变量 |
| loop | `loop <N> ... end` | 计数循环，N 为正整数或变量引用 |
| loop while | `loop while <cond> ... end` | 条件循环，每轮前求值，truthy 则执行 |
| loop until | `loop until <cond> ... end` | 条件循环，先执行再求值，truthy 则退出（至少执行一次） |
| break | `break` | 跳出最内层 for/loop |
| continue | `continue` | 跳过当前迭代，进入下一轮 |
| try/catch | `try ... catch [$err] ... end` | 异常捕获与兜底 |
| return | `return` | 提前结束当前工作流 |
| label | `@label_name` | 标签，goto 的目标 |
| goto | `goto label_name` | 同文件内无条件跳转 |

## 二、if / else — 条件分支

```
if $scan.affix_gong contains "会心"
    log "命中目标词条"
else
    log "未命中"
end
```

支持 `and` / `or` / `not` 组合条件，`and` 优先级高于 `or`，支持短路求值。可多层嵌套：

```
if $result equals "成功"
    if $result.quality > 80
        log "高质量成功"
    end
else
    log "失败"
end
```

## 三、for — 枚举循环

支持两种迭代源：

**静态列表**（元素为裸标识符、字符串或变量引用）：

```
for slot in [head, chest]
    click [scene].$slot
end

# 混合变量引用
for item in ["a", $var, "c"]
    log $item
end
```

**列表变量**（遍历 `eval` 赋值的列表变量）：

```
eval $slots = ["bag_1_1", "bag_1_2", "bag_1_3"]
for slot in $slots
    click [bag_equip_detail].$slot
end
```

循环变量在循环体内通过 `$var` 引用。

## 四、loop — 计数与条件循环

### loop N — 计数循环

重复执行指定次数。N 可以是数字字面量或变量引用：

```
# 固定次数
loop 3
    click [equip_tune_detail].[retry]
    wait step_interval
end

# 变量引用（运行时从变量取值）
loop $execute_times
    # ... 执行逻辑
end
```

### loop while — 条件为真时循环

每次迭代前求值条件，结果为 truthy 则执行循环体，为 falsy 则退出：

```
eval $x = 0
loop while $x < 10
    eval $x = $x + 1
end
# $x == 10
```

### loop until — 条件为真时退出（至少执行一次）

先执行循环体，再求值条件；条件为 truthy 则退出，为 falsy 则继续：

```
eval $x = 0
loop until $x >= 5
    eval $x = $x + 1
end
# $x == 5
```

选择 `loop while` / `loop until` 而非独立关键字 `while` / `until`，与现有 `loop N` 同族，词法不增加新关键字。

> **安全限制**：`loop while` / `loop until` 均有 1,000,000 次迭代上限，超过后强制退出并记录错误日志，防止死循环。

## 五、default — 默认值赋值

仅当变量**未从外部传入**时才赋默认值。如果变量已通过 `call ... with` 或工作流参数传入，则跳过赋值。

**语法**：

```
default $var = <literal>     # literal 可以是字符串、数字、范围元组等
```

**示例**：

```
default $execute_times = 10          # 未传入时使用默认值 10
default $step_interval = (1, 2)      # 未传入时使用默认范围 1~2 秒
```

配合 `loop $var` 实现参数化循环：

```
default $execute_times = 10
loop $execute_times
    # ... 每轮逻辑
end
```

> **与 `eval` 的区别**：`eval $var = 10` 每次都会覆盖变量值；`default $var = 10` 仅在变量不存在时赋值，保留外部传入的值。

## 六、break / continue — 循环控制

### break — 跳出循环

跳出最内层的 `for` 或 `loop`（含 `loop while` / `loop until`）：

```
loop 10
    scan [equip_tune_result] as $result
    if $result.result equals "成功"
        break
    end
end
```

### continue — 跳过当前迭代

在 `for` / `loop` / `loop while` / `loop until` 的循环体内使用，立即跳过当前迭代剩余语句，进入下一轮迭代。嵌套循环中只影响最内层：

```
eval $sum = 0
for item in $items
    if $item equals "skip"
        continue
    end
    eval $sum = $sum + 1
end
```

> `continue` 在非循环上下文中使用会导致运行时错误。

## 七、try / catch — 异常处理

```
try
    # 可能出错的代码
catch $err
    # 出错时的兜底逻辑，$err 接收错误消息
end
```

- `catch` 后可选绑定一个变量名，接收错误消息字符串。
- `catch` 子句本身可选——不写 `catch` 则仅静默吞掉异常。

### 捕获范围

| 异常类型 | 是否捕获 | 说明 |
|---|---|---|
| `WorkflowUserError` | 是 | DSL 用户可见错误（如字符串字段访问） |
| `KeyError` / `ValueError` / `TypeError` | 是 | 字典 key 不存在、值转换失败、类型不匹配 |
| 控制流信号（break/return/goto/continue） | **否** | 穿透 try/catch，否则在 try 块内会失效 |
| `KeyboardInterrupt` | **否** | 系统中断，穿透 |

### 示例

```
# 基本捕获
try
    eval $x = $result.not_exist_field
catch $err
    log concat("捕获错误: ", $err)
end

# 无变量绑定
try
    eval $x = $data.missing_key
catch
    log "出错了，使用默认值"
    eval $x = "default"
end

# try 内 break 穿透：直接退出 loop，不被 catch 拦截
loop 10
    try
        eval $x = $data.risky_field
        break
    catch $err
        log $err
    end
end
```

## 八、return — 结束工作流

提前结束当前工作流的执行。若当前是子工作流，则返回到调用方：

```
if not $gold_pos
    log "未找到目标，终止流程"
    return
end
```

## 九、label / goto — 标签跳转

`@label` 定义跳转目标，`goto` 无条件跳转到该标签。仅限同一工作流文件内：

```
@tune_start
click [equip_tune_detail].[tune_btn]
wait (1, 2)
scan [equip_tune_result] as $result

if $result.result contains "失败"
    goto tune_start
end
```

## 十、条件表达式

### 10.1 基础条件

| 形式 | 说明 |
|---|---|
| `$var.field contains "文本"` | 字段值包含子串 |
| `$var.field equals "文本"` | 字段值完全相等 |
| `$var.field in ["文本1", "文本2", ...]` | 字段值等于列表中任一项（等价于多个 equals 的 or 组合） |
| `$var in ["文本1", "文本2", ...]` | 变量值等于列表中任一项 |
| `$var.field is_empty` | 字段不存在或为空字符串 |
| `$var.field > N` / `< N` / `>= N` / `<= N` / `== N` / `!= N` | 字段数值与 N 比较（N 为整数或浮点数）。`==` / `!=` 为容差比较（差值 < 1e-9 视为相等），避免浮点误差（如 `0.1 + 0.2 == 0.3` 为 true） |
| `$var > N` / `< N` / `>= N` / `<= N` / `== N` / `!= N` | 变量数值与 N 比较（变量值为数字或可转数字的字符串） |
| `expr > expr` / `< / == / ...` | 算术表达式比较：两侧支持 `+` `-` `*` `/` 运算，如 `$a + 1 > $b * 2` |
| `$var` | truthy 检查：变量存在且非空时为 true，不存在或为空时为 false |
| `not <基础条件>` | 取反任意一种 |

**字段访问**：
- 静态字段：`$var.field`、`$var."field"`、`$var.[field]`（三者等价）
- 动态字段：`$var.$key`（用变量值作为 key）
- 列表索引：`$list[$i]`（直接 `[]` 表示索引）
- 链式嵌套：`$var.f1.f2.f3`，每层从上一层结果中取字段

`$var` 的 truthy 检查常用于判断子工作流是否成功返回值：

```
call "sub.wf" read "status" as $result
if not $result
    log "子工作流未返回 status，执行失败"
    return
end
```

### 10.2 组合条件

- `and`：逻辑与，优先级高于 `or`
- `or`：逻辑或
- **短路求值**：`and` 遇 false 停、`or` 遇 true 停

```
if $scan.affix_gong contains "会心" and not $scan.affix_shang is_empty
    log "命中目标词条"
end

if $scan.x is_empty or ($scan.y equals "A" and $scan.z contains "B")
    click [scene].[field]
end
```

### 10.3 算术表达式条件

数值比较的两侧支持算术表达式（`+` `-` `*` `/`），可用括号改变优先级：

```
if $a + 1 > $b * 2
    log "a+1 大于 b*2"
end

if ($x + $y) / 2 >= 60
    log "平均值达标"
end

if $col + 1 == 6
    log "已是最后一列"
end
```

> 算术表达式的运算规则与 eval 赋值一致：浮点除法、除零返回 0。详见 [02-data-flow.md](02-data-flow.md#算术表达式)。
