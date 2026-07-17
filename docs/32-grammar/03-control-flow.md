# DSL 控制流与条件表达式

## 一、控制流指令总览

| 指令 | 语法 | 说明 |
|---|---|---|
| if | `if <cond> ... [else ...] end` | 条件分支，可嵌套 |
| for | `for $var in [a, b, c] ... end` 或 `for $var in $list ... end` | 枚举循环，迭代静态列表或列表变量 |
| loop | `loop <N> ... end` | 计数循环，N 为正整数或变量名 |
| break | `break` | 跳出最内层 for/loop |
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
for slot in [slot_head, slot_chest]
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

## 四、loop — 计数循环

重复执行固定次数。N 可以是数字字面量或变量名：

```
loop 3
    click [equip_tune_detail].[retry]
    wait step_interval
end
```

## 五、break — 跳出循环

跳出最内层的 `for` 或 `loop`：

```
loop 10
    scan [equip_tune_result] as $result
    if $result.result equals "成功"
        break
    end
end
```

## 六、return — 结束工作流

提前结束当前工作流的执行。若当前是子工作流，则返回到调用方：

```
if not $gold_pos
    log "未找到目标，终止流程"
    return
end
```

## 七、label / goto — 标签跳转

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

## 八、条件表达式

### 8.1 基础条件

| 形式 | 说明 |
|---|---|
| `$var.field contains "文本"` | 字段值包含子串 |
| `$var.field equals "文本"` | 字段值完全相等 |
| `$var.field in ["文本1", "文本2", ...]` | 字段值等于列表中任一项（等价于多个 equals 的 or 组合） |
| `$var in ["文本1", "文本2", ...]` | 变量值等于列表中任一项 |
| `$var.field is_empty` | 字段不存在或为空字符串 |
| `$var.field > N` / `< N` / `>= N` / `<= N` / `== N` / `!= N` | 字段数值与 N 比较（N 为整数或浮点数） |
| `$var > N` / `< N` / `>= N` / `<= N` / `== N` / `!= N` | 变量数值与 N 比较（变量值为数字或可转数字的字符串） |
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

### 8.2 组合条件

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
