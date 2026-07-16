# 工作流 DSL 语法规范（v2）

`.wf` 文件为纯文本工作流描述，存放于 `config/system/workflows/`。支持顺序执行、条件分支、循环、跳转等控制流。

## 一、基础约定

- **注释**：`#` 开头整行为注释
- **空行**：忽略
- **缩进**：自由（解析器不依赖缩进），但建议 4 空格以提升可读性
- **块闭合**：靠 `end` 关键字，不靠缩进
- **字符串**：双引号 `"..."`，内部不支持转义
- **标识符**：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`
- **两种引用，语义完全不同**：
  - `[name]` → **静态配置引用**：引用场景名 / 区域名，来自 YAML 场景定义
  - `$name` → **运行时变量引用**：引用工作流执行过程中的动态变量
- **内置变量**：
  - 无全局内置变量；通过 `scan [scene] as $var` 声明，后续用 `$var` 引用

### 常量类型

| 类型 | 语法 | 示例 | 可用位置 |
|---|---|---|---|
| 字符串 | `"..."` | `"武器"`, `"slot_head"` | log、find 文本、collect alias、eval 参数、call 路径、contains/equals 比较、eval 字面量赋值 |
| 数字 | 整数或小数，支持负号 | `3`, `0.5`, `-10`, `-3.14` | loop 次数、wait 秒数、drag/hold 时长、数值比较、eval 字面量赋值 |

**不支持**：布尔值、null；eval 函数参数不能直接传数字常量（只能传 `$var` 或 `"string"`）。

## 二、变量系统

变量是工作流运行时的动态数据载体，存储在引擎的 `variables` 字典中，通过 `$name` 引用。

### 2.1 声明方式

| 方式 | 语法 | 说明 |
|---|---|---|
| scan 声明 | `scan [scene] as $var` | OCR 扫描结果存入 `$var`（dict，key 为区域名） |
| eval 函数赋值 | `eval $var = func(args...)` | 内置函数返回值存入 `$var` |
| eval 字面量赋值 | `eval $var = "str"` 或 `eval $var = 42` | 字面量直接存入 `$var` |
| for 循环变量 | `for item in [a, b, c]` | 每次迭代 `$item` 绑定当前值 |
| call 提取 | `call "sub.wf" read "key" as $var` | 从子工作流输出中提取值 |

变量**无需预先声明**，首次赋值即创建，后续引用即可。

### 2.2 变量类型

变量的实际类型由赋值来源决定：

| 来源 | 类型 | 示例 |
|---|---|---|
| `scan ... as $var` | `dict` | `$var` = `{"equip_type": "武器", "affix_gong": "会心+10%"}` |
| `eval $var = to_equipment(...)` | `dict` | 嵌套字典，支持链式字段访问 |
| `eval $var = "hello"` | `str` | 字符串 |
| `eval $var = 42` | `float` | 数字（内部统一为 float） |
| `for x in [...]` | `str` | 迭代元素为字符串 |

### 2.3 字段访问

当变量为 `dict` 类型时，可通过 `.` 访问其字段：

```
$var.field_name          # 单层访问
$var.affix_1.value       # 链式访问，逐层深入嵌套 dict
```

字段访问返回的是原始值（可能是 dict、str、int、float），在条件比较时自动转换为对应类型。

### 2.4 引用规则

- `$name` 在运行时从 `variables` 字典中查找，找不到则报错
- 变量名遵循标识符规则：`[a-zA-Z_\u4e00-\u9fff][a-zA-Z0-9_\u4e00-\u9fff]*`
- 变量作用域为当前工作流文件内，子工作流通过 `with/read` 显式传参，不共享变量

### 2.5 运行时状态

引擎持有三个核心状态：

| 状态 | 类型 | 作用 |
|---|---|---|
| `variables` | `dict` | 运行时变量空间，所有 `$var` 的存储 |
| `output` | `dict` | `collect` 指令的输出缓冲区，工作流结束后返回给调用方 |
| `scan_meta` | `dict` | scan 产出的区域坐标元数据，供 `find` 和 `click $var` 使用 |

**数据流全景**：

```
scan [scene] as $var  ──→  variables[$var] = dict
                         scan_meta[$var] = {field: Region}

eval $var = func(...)  ──→  variables[$var] = result

collect $var           ──→  output[key] = variables[$var]

call "sub.wf" read "k" as $v  ──→  variables[$v] = sub_output["k"]
```

`output` 是工作流的**唯一对外出口**：`collect` 写入，工作流结束时整体返回。调用方通过 `read` 从中提取值。

## 三、指令集

### 3.1 基础指令

| 指令 | 语法 | 说明 |
|---|---|---|
| click | `click [scene].[region]` 或 `click $var` | 点击静态区域，或点击 find 产出的动态坐标 |
| drag | `drag [scene].[arrow] [时长] [hold 秒数]` | 执行 arrow 定义的拖拽。时长可选：固定秒数或 `[min, max]` 范围；hold 可选，到达后按住不放的时长 |
| wait | `wait <delay_name>` 或 `wait <秒数>` | 命名延迟或固定秒数 |
| scan | `scan [scene] as $var` 或 `scan [scene].[f1, f2, ...] as $var` | OCR 扫描场景，结果存入 `$var`（dict，key 为区域名）。后者仅扫描指定字段 |
| find | `find $source "文本" as $coord [error "错误信息"]` | 在 scan 结果中查找文本，将坐标存入 `$coord` |
| collect | `collect $var` 或 `collect $var as "label"` | 将变量值追加到工作流输出。后者以 `{label: value}` 形式追加 |
| eval | `eval $var = func(args...)` 或 `eval $var = 字面量` | 调用内置函数或将字面量存入变量 |
| call | `call "sub.wf" with $x as arg1 read "key" as $var` | 调用子工作流，传入参数并提取返回值 |
| log | `log "消息"` | 输出日志 |

### 3.2 控制流指令

| 指令 | 语法 | 说明 |
|---|---|---|
| if | `if <cond> ... [else ...] end` | 条件分支，可嵌套 |
| for | `for <var> in [a, b, c] ... end` | 枚举循环，迭代时通过 `$var` 提供当前值 |
| loop | `loop <N> ... end` | 计数循环，N 为正整数或变量名 |
| break | `break` | 跳出最内层 for/loop |
| return | `return` | 提前结束当前工作流 |
| label | `@label_name` | 标签，goto 的目标 |
| goto | `goto label_name` | 同文件内无条件跳转 |

## 四、条件表达式

### 4.1 基础条件

| 形式 | 说明 |
|---|---|
| `$var.field contains "文本"` | 字段值包含子串 |
| `$var.field equals "文本"` | 字段值完全相等 |
| `$var.field in ["文本1", "文本2", ...]` | 字段值等于列表中任一项（等价于多个 equals 的 or 组合） |
| `$var.field is_empty` | 字段不存在或为空字符串 |
| `$var.field > N` / `< N` / `>= N` / `<= N` / `== N` / `!= N` | 字段数值与 N 比较（N 为整数或浮点数） |
| `not <基础条件>` | 取反任意一种 |

**字段访问支持链式嵌套**：`$var.f1.f2.f3`，每层从上一层结果中取字段。

示例：

```
if $scan.equip_type in ["万仞山披膊", "天罡战袍"]
    log "命中目标装备类型"
end
```

### 4.2 组合条件

- `and`：逻辑与，优先级高于 `or`
- `or`：逻辑或
- **短路求值**：`and` 遇 false 停、`or` 遇 true 停

示例：

```
if $scan.affix_gong contains "会心" and not $scan.affix_shang is_empty
    log "命中目标词条"
end

if $scan.x is_empty or ($scan.y equals "A" and $scan.z contains "B")
    click [scene].[field]
end
```

### 4.3 变量引用

`for` 循环变量在循环体内通过 `$var` 引用：

```
for slot in [slot_head, slot_chest]
    if $slot equals "slot_head"        # 条件表达式里用 $slot
        log "当前是头部槽位"
    end
    log "当前迭代: $slot"          # log 只接受字符串字面量
end
```

自定义变量通过 `scan ... as $var` 声明，后续通过 `$var` 引用：

```
scan [equip_weapon_detail] as $weapon_data
if $weapon_data.affix_gong contains "会心"
    log "武器命中会心"
end
```

## 五、内置函数与 eval

DSL 通过 `eval` 调用引擎内置函数，支持数据清洗、条件判定等能力。

### 5.1 eval 语法

```
eval $var = func_name(arg1, arg2, ...)   # 调用函数，结果存入变量
eval $var = "字符串"                        # 字面量赋值
eval $var = 42                              # 数字字面量赋值（支持负数）
eval func_name(arg1, arg2, ...)            # 调用函数，丢弃返回值
```

- 赋值目标 `$var =` 可选，省略则丢弃返回值
- 右侧可以是函数调用、字符串字面量或数字字面量
- 函数参数可以是 `$var`（变量引用，运行时解析为实际值）或 `"literal"`（字面量字符串）

### 5.2 内置函数列表

| 函数 | 签名 | 说明 |
|---|---|---|
| `to_equipment` | `(raw_data: dict) -> dict` | 解析装备 OCR 原始数据为标准装备字典，支持链式字段访问 |
| `contains` | `(scan_result: dict, text: str) -> bool` | 检查 scan 结果中是否有任意字段包含指定文本 |
| `count` | `(scan_result: dict) -> int` | 统计 scan 结果中非空字段数量 |
| `is_good_equip` | `(scan_result: dict) -> bool` | 判定装备是否值得保留（基于高价值词条） |

### 5.3 装备解析示例

```
# scan → eval 解析 → collect 标准三步模式
scan [equip_weapon_detail] as $scan_result
eval $main_weapon = to_equipment($scan_result)
collect $main_weapon
```

`to_equipment` 纯基于 OCR 文字分析装备类型，不依赖场景信息。
返回字典支持链式字段访问：

```
if $main_weapon.affix_1.value > 100
    log "首词条数值超过 100"
end
```

## 六、完整示例

### 6.1 装备分析（if/else + 顺序执行）

```
# 装备分析工作流（逐个点击扫描）

click [bag_equip_detail].[slot_main_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $main_weapon_scan
eval $main_weapon = to_equipment($main_weapon_scan)
collect $main_weapon

click [bag_equip_detail].[slot_sub_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $sub_weapon_scan
eval $sub_weapon = to_equipment($sub_weapon_scan)
collect $sub_weapon

# ... 其他部位类似
```

### 6.2 调律决策树骨架（loop + if + goto + break）

```
@tune_start
click [equip_tune_detail].[one_click_add]
wait step_interval
click [equip_tune_detail].[tune_btn]
wait after_tune_wait
scan [equip_tune_result] as $tune_result

if $tune_result.result equals "成功"
    log "调律成功"
    goto tune_done
end

if $tune_result.result contains "失败"
    loop 3
        click [equip_tune_detail].[retry]
        wait step_interval
    end
    goto tune_start
end

log "未知结果，停止"
break

@tune_done
click [equip_tune_result].[close_btn]
```

## 七、子工作流调用（call）

`call` 指令用于调用另一个 `.wf` 文件作为子工作流，实现逻辑复用和模块化。父子工作流之间通过 `with` 传入参数、通过 `read` 取回结果，变量完全隔离。

### 7.1 语法总览

```
# 最简调用：无参数、无返回值
call "sub.wf"

# 完整调用：传入参数 + 提取返回值
call "sub.wf" with $x as param1, $y as param2 read "key1" as $out1, "key2" as $out2
```

语法结构：

| 部分 | 语法 | 是否必须 | 说明 |
|---|---|---|---|
| 路径 | `"path.wf"` | 必须 | 子工作流文件路径（字符串字面量） |
| with 子句 | `with $var as name [, ...]` | 可选 | 向子工作流注入参数 |
| read 子句 | `read "key" as $var [, ...]` | 可选 | 从子工作流输出中提取值 |

- `with` 和 `read` 均可独立使用或同时使用
- 多个参数/返回值用逗号分隔

### 7.2 参数传入（with）

`with` 子句将父工作流的变量值传入子工作流，子工作流内部通过参数名访问：

```
# 父工作流
eval $slot = "slot_head"
eval $mode = "quick"
call "check.wf" with $slot as target_slot, $mode as run_mode
```

子工作流 `check.wf` 内部直接使用 `$target_slot` 和 `$run_mode`：

```
# check.wf
if $target_slot equals "slot_head"
    log "检查头部装备"
end
```

**传参规则**：

- `with` 后面是 `$var as name`，`$var` 是父级变量引用，`name` 是子级参数名
- 传值时机：call 执行时从父 `variables` 中取值，之后父子互不影响
- 可以传任意类型的变量值（字符串、数字、dict 等）
- 子工作流中未通过 `with` 注入的变量不可访问（隔离机制）

### 7.3 获取返回值（read）

子工作流通过 `collect` 指令将数据写入自身的 `output` 字典。父工作流通过 `read` 子句从 `output` 中提取值。

**子工作流写入 output**：

```
# 子工作流内部
collect $scan_result as "equip_data"    # output["equip_data"] = $scan_result 的值
eval $status = "ok"
collect $status as "status"             # output["status"] = "ok"
```

**父工作流读取 output**：

```
call "sub.wf" read "equip_data" as $data, "status" as $status
# 执行后：$data = 子工作流的 equip_data 值，$status = "ok"
```

**read 规则**：

- `read` 后面是 `"key" as $var`，`"key"` 对应子工作流 `collect ... as "key"` 的标签名
- 若子工作流 output 中不存在指定 key，父级 `$var` 不会被赋值（保持原值或不存在），并输出警告日志
- 若子工作流提前 `return`（未执行到 `collect`），则 output 为空，所有 read 均取不到值

### 7.4 隔离机制

每次 `call` 创建一个**独立的子 engine 实例**，拥有自己的 `variables`、`output`、`scan_meta`。父子之间完全隔离，不会互相污染。

**调用流程**：

```
父 engine
  │
  ├─ 1. 从 variables 中取 $x 的值
  ├─ 2. 创建子 engine，注入 {param_name: value} 到子 variables
  ├─ 3. 子 engine 执行 sub.wf，产出 sub_output（子 engine 的 output 字典）
  ├─ 4. 从 sub_output 提取 read 的 key，写入父 variables
  └─ 5. 子 engine 销毁
```

关键点：

- 子工作流的 `scan`、`eval` 等操作只影响子 engine 的 variables
- 子工作流的 `collect` 只写入子 engine 的 output
- 父工作流只能通过 `read` 获取子工作流显式输出的数据

### 7.5 路径解析

子工作流路径基于**当前 wf 所在目录**解析，支持相对路径：

```
# 若当前 wf 在 config/system/workflows/main.wf
call "subcall/navigate.wf"   # → config/system/workflows/subcall/navigate.wf

# 若当前 wf 在 config/system/workflows/subcall/inner.wf
call "other.wf"              # → config/system/workflows/subcall/other.wf
```

项目中子工作流统一存放在 `subcall/` 子目录下。

### 7.6 契约模式：成功/失败约定

子工作流应遵循**契约模式**——通过 output 中的 `status` 字段告知调用方执行结果：

**子工作流**（`subcall/nav_equip_to_tune.wf`）：

```
# 前置：在装备详情页
# 成功：进入调律页，输出 status = "ok"
# 失败：仍在装备详情页，无输出（直接 return）

click [equip_weapon_detail].[more_func]
wait step_interval

scan [equip_weapon_detail].[sub_func_1, sub_func_2, sub_func_3, sub_func_4] as $tune_scan
find $tune_scan "调律" as $tune_pos
if not $tune_pos
    log "未找到调律按钮"
    return                          # 失败：直接返回，不 collect
end
click $tune_pos
wait step_interval

eval $result = "ok"
collect $result as "status"         # 成功：写入 status
```

**父工作流**调用并检测失败：

```
call "subcall/nav_equip_to_tune.wf" read "status" as $tune_status
if not $tune_status
    log "未进入调律页面，终止流程"
    return
end
# 后续逻辑...
```

当子工作流失败时不执行 `collect`，父级 `read` 取不到 `$tune_status`（变量不存在），`if not $tune_status` 判定为 true，从而检测失败。

### 7.7 完整示例

```
# ── 父工作流：装备调律 ──

# 1. 导航到装备页（无需参数和返回值）
call "subcall/nav_main_to_equip.wf"

# 2. 点击具体装备并扫描
click [bag_equip_detail].[slot_main_weapon]
wait page_refresh_wait
scan [equip_weapon_detail] as $scan_result

# 3. 导航到调律页（带返回值检测）
call "subcall/nav_equip_to_tune.wf" read "status" as $tune_status
if not $tune_status
    log "未进入调律页面"
    return
end

# 4. 执行调律操作...
```

```
# ── 子工作流：nav_main_to_equip.wf ──
# 从主界面导航到装备培养页面

click [game_main_page].[menu]
wait step_interval

click [game_menu_page].[bag]
wait step_interval

scan [bag_equip_detail].[sub_equip] as $bag_scan
if not $bag_scan.sub_equip contains "装备"
    click [bag_equip_detail].[training]
    wait step_interval
end
```
