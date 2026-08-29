# DSL 语法速查

工作流 DSL（Domain Specific Language）语法规范，用于描述 `.wf` 工作流文件。

> 本文档汇总所有指令的语法格式。详细说明请点击对应链接。

---

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-basics.md](01-basics.md) | 词法与值、引用模型、变量系统、表达式 |
| [02-concepts.md](02-concepts.md) | 场景（Scene）、布局（Layout）、Area/Action、Panel |
| [03-interaction.md](03-interaction.md) | 交互指令概览：指令分类、隐藏延迟共性、press 状态管理 |
| [03.1-basic-commands.md](03.1-basic-commands.md) | 基础指令：collect、eval、default、call、log |
| [03.2-interaction.md](03.2-interaction.md) | 时间与辅助：wait、wait stable、align、screenshot |
| [03.3-mouse.md](03.3-mouse.md) | 鼠标操作：click、drag、后缀等待子句、隐藏延迟 |
| [03.4-keyboard.md](03.4-keyboard.md) | 键盘输入：press 四种模式、KeyStateRegistry、键名表 |
| [04-data-flow.md](04-data-flow.md) | 感知指令概览与对比表 |
| [04.1-scan.md](04.1-scan.md) | scan — OCR 文字扫描 |
| [04.2-recognize.md](04.2-recognize.md) | recognize — 图像材料识别 |
| [04.3-find.md](04.3-find.md) | find — 文字坐标定位 / 模板定位（by image） |
| [05-control-flow.md](05-control-flow.md) | 控制流概览与指令总表 |
| [05.1-loops-branches.md](05.1-loops-branches.md) | 分支与循环：if、for、loop、break、continue |
| [05.2-flow-jumps.md](05.2-flow-jumps.md) | 异常与跳转：try/catch、return、label/goto |
| [05.3-conditions.md](05.3-conditions.md) | 条件表达式：基础条件、组合条件、算术比较 |
| [06-functions.md](06-functions.md) | 内置函数总览与速查表 |
| [06.1-basic-functions.md](06.1-basic-functions.md) | 基础函数：算术、字典/列表、字符串 |
| [06.2-system-interaction.md](06.2-system-interaction.md) | 系统与交互函数 |
| [06.3-game-functions.md](06.3-game-functions.md) | 游戏相关函数 |
| [06.4-vision-functions.md](06.4-vision-functions.md) | 图色函数：取色、色占比、亮段、色心方位、同色图标、多点找色 |
| [07-subworkflows.md](07-subworkflows.md) | 模块化：import/def/call、变量隔离 |
| [07.1-metadata.md](07.1-metadata.md) | `.wf` 文件头元数据与外部参数声明 |
| [08-examples.md](08-examples.md) | 完整示例 |
| [09-data-channels.md](09-data-channels.md) | 数据通道：session/context/variables/output |

---

## 一、词法与值

```
# 注释
# 整行注释以 # 开头

# 字面量
"字符串"               # 双引号，不支持转义
42 / -3                # 整数（int）
1.5 / -3.14            # 浮点数（float）
true / false            # 布尔值
null                    # 空值
{"k": "v", "n": 3}     # 字典（key 限定 str）
[1, "a", null]          # 列表
(1, 2)                  # 泛化元组（支持数字/变量混合）
```

**续行**：行尾 `\` 显式续行；`{}`/`[]`/`()` 内部隐式续行。

## 二、引用模型

```
[name]                  # 配置引用（场景/Area/Action 名）
$var                    # 运行时变量引用
[scene].[area]          # 场景.区域
[scene].$var            # 场景.动态区域
$scene.[area]           # 动态场景.区域
[f1, f2, f3]            # 多 Area 列表（scan/recognize 用）
"text"                  # 字符串数据（用于 eval/log/by匹配/函数参数）
```

> `[name]` 表示配置引用，`$var` 表示变量引用。`"text"` 始终表示字符串数据，不用于配置引用。变量只是延迟求值的常量。

**Panel 索引区分**：`[scene].[panel][row][col]` 中，`scene`/`panel` 是配置引用（`[name]` 或 `$var`），而 `row`/`col` 是面板索引（`[INT]` 或 `[$var]`），不是 area 引用。row/col 不支持字符串，`["a"]` 语法不合法。

## 三、变量与表达式

```
# 赋值
eval $var = "hello"             # 显式赋值
$var = "hello"                  # 隐式 eval（等价）
eval $var = func(args)          # 函数返回值赋值
eval $var = $a + $b * 2        # 算术表达式
eval $var = {"k": "v"}          # 字典
eval $var = ["a", "b"]          # 列表
eval $var = (1, 2)              # 泛化元组
default $var = 10               # 仅当未从外部传入时赋值

# 字段访问
$dict.field                     # 静态字段
$dict."field"                   # 字符串 key
$dict.$key                      # 动态 key
$dict.a.b.c                     # 链式访问
$list[0] / $list[$i]            # 列表索引

# 算术
$a + $b                         # 加（str 时自动拼接）
$a - $b                         # 减
$a * $b                         # 乘
$a / $b                         # 浮点除（除零返回 0.0）
```

## 四、领域概念

```
Scene   → 游戏页面/界面状态（YAML 定义于 scenes/）
Layout  → Area/Action 坐标映射（JSON 定义于 layouts/）
Region  → 有面积的矩形区域（可 click/scan/recognize）
Point   → 坐标点（可 click）
Action  → 拖拽行为实体（Arrow，可 drag）
Panel   → 可寻址网格容器（[r][c] 二维索引，r/c 从 1 开始）
```

## 五、交互指令

### click — 点击

```
click [scene].[region]                  # 点击区域中心
click [scene].$var                      # 动态区域
click [scene].[panel][r][c]             # Panel 格子中心
click [scene].[panel]                   # Panel 中心
click $var                              # CoordRef / find 结果
click (rx, ry)                          # 画布归一化坐标
click [scene].[region] after wait stable 5  # 点击后等待稳定
```

### drag — 拖拽

```
# Arrow 拖拽
drag [scene].[arrow]                    # 执行 Arrow 定义的拖拽
drag [scene].[arrow] 0.5                # 指定时长
drag [scene].[arrow] 0.5 hold 0.2       # 拖拽后按住

# Panel/Region 翻页
drag [scene].[panel][r][c] down [n]     # 下翻 n 行（默认 1）
drag [scene].[panel][r][c] up $var      # 上翻 $var 行
drag [scene].[panel][r][c] left/right   # 左/右翻

# 点对拖拽
drag [s1].[p1] [s2].[p2]               # 两点间拖拽
drag (rx1, ry1) (rx2, ry2)             # 坐标模式
```

### wait — 等待

```
wait @<delay_name>                      # 命名延迟（配置读取）
wait 1.5                                # 固定秒数
wait $var                               # 动态等待
wait (1, 2)                             # 随机范围
```

### wait stable — 等待画面稳定

```
wait stable <timeout>                                           # 基本形式
wait stable <timeout> threshold <v> interval <v> duration <v> least <v>  # 完整参数
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `timeout` | 必填 | 等待预算秒数（耗尽记警告并继续） |
| `threshold` | 0.02 | 像素差异率阈值 |
| `interval` | 0.3 | 截图间隔 |
| `duration` | 0.5 | 需持续稳定的时长 |
| `least` | 0.5 | 最低等待秒数 |

所有参数支持字面量 / `@命名延迟` / `$变量`。可作为 click/drag 子句：`click ... after wait stable 5`。

### align / screenshot

```
align [scene].[panel]                   # 手动触发面板自对齐
screenshot                              # 截图保存到 logs/image/
```

### press — 键盘输入

```
press "KEY"                             # 完整按键（down + up）
press "KEY" hold <秒数>                 # 按住指定时长
press "KEY" down                        # 按下保持
press "KEY" up                          # 释放
```

| 模式 | 语义 | 状态变化 |
|------|------|--------|
| PRESS | 一次完整按键 | down → up |
| HOLD | 按住 N 秒 | down → sleep → up |
| DOWN | 按下保持 | 键留在 registry |
| UP | 释放 | 键从 registry 移除 |

键名不区分大小写，支持别名（Escape→ESC, Control→CTRL）。组合键用 down/up 构造：

```
press "CTRL" down
press "C"
press "CTRL" up
```

工作流退出时 `release_all()` 自动释放所有残留按键。详见 [03.4-keyboard.md](03.4-keyboard.md)。

## 六、基础指令

```
# collect — 存入输出字典
collect $var                            # 以变量名为 key
collect $var as "label"                 # 以静态 label 为 key
collect $var as $alias                  # 以动态 alias 为 key
collect session.field                   # 以字段名为 key

# eval — 赋值（可省略 eval 关键字）
eval $var = func(args)
eval $var.field = value

# call — 调用子过程
call proc_name()
call proc_name($arg1, $arg2)
call $result = proc_name()              # 接收返回值
call proc_name() as $output             # 接收 output dict
call $result = proc() as $output        # 同时接收

# log — 日志输出
log "消息"                              # 默认 info 级别
log debug / info / warn / error "消息"  # 指定级别
log concat("a", $var)                   # 函数返回值
```

## 七、感知指令

> 详细返回值与修饰子句见 [04-data-flow.md](04-data-flow.md) / [04-1](04.1-scan.md) / [04-2](04.2-recognize.md) / [04-3](04.3-find.md)。

### scan — OCR 文字扫描

```
scan [scene] as $var                                    # 扫描所有 region → dict
scan [scene].[r1, r2] as $var                           # 指定 region → dict
scan [scene].[panel] as $var                            # 整面板 → {行: {列: 文本}}
scan [scene].[panel][r][c] as $var                      # 单格 → str

# 带 by（短路匹配 → str 或 {row, col}）
scan [scene].[r1, r2] as $var by contains "文本"
scan [scene].[r1, r2] as $var by equals_any $list

# 带 where（置信度过滤）
scan [scene].[r1, r2] as $var where confidence >= 0.8
```

### recognize — 图像材料识别

```
recognize [scene].[s1, s2] as $var                      # 识别 slot → dict
recognize [scene].[panel] as $var                       # 整面板 → {行: {列: 材料名}}
recognize [scene].[panel][r][c] as $var                 # 单格 → str

# 带 by（短路匹配）
recognize [scene].[s1, s2] as $var by equals "材料名" on group "分组"

# 带 as rich（富返回值）
recognize [scene].[s1, s2] as rich $var                 # → {key: 富dict}
recognize [scene].[s1, s2] as rich $var with yysls_rich_parse

# 组合
recognize [scene].[s1, s2] as rich $var with func where confidence >= 0.8 on group "分组"
```

### find — 文字坐标定位

```
find as $var by contains "文字"                         # 全画布搜索 → FoundRegion
find [scene].[area] as $var by contains "文字"          # 指定区域搜索
find $scene.$region as $var by contains_any $list       # 动态区域

# 带 where
find as $var by contains "文字" where confidence >= 0.8

# 模板定位（仅 find）：config/system/templates/<name>.png，where 作匹配分门槛
find as $var by image "extract_icon"
find [scene].[area] as $var by image "extract_icon" where confidence >= 0.85
```

### 修饰子句速查

| 子句 | 方向 | 适用 |
|------|------|------|
| `by <mode> <target>` | 降级：dict → str/位置 | scan / recognize / find |
| `as rich` | 升级：str → dict | 仅 recognize |
| `with <func>` | 配合 rich | 仅 recognize |
| `where confidence >= <n>` | 过滤 | scan / recognize / find |
| `on group "<name>"` | 限定分组 | 仅 recognize |

by 模式：`equals "文本"` / `contains "文本"` / `equals_any $list` / `contains_any $list` / `image "模板名"`（仅 find）

### 与 click 的配合

| 产出 | click 用法 |
|------|-----------|
| scan/recognize 有 by → str | `click [scene].$var` |
| find → FoundRegion | `click $var` |
| CoordRef 变量 | `click $var` |
| scan/recognize 无 by → dict | 不可直接 click |
| panel by → {row, col} | `click [scene].[panel][$var.row][$var.col]` |

## 八、控制流

```
# 条件分支
if <cond>
    ...
else if <cond>
    ...
else
    ...
end

# 枚举循环
for $var in [a, b, c] ... end
for $var in $list ... end

# 计数循环
loop <N> ... end                      # N 为数字或 $var

# 条件循环
loop while <cond> ... end             # 先判断后执行
loop until <cond> ... end             # 先执行后判断（至少一次）

# 循环控制
break                                 # 跳出最内层循环
continue                              # 跳过当前迭代

# 异常处理
try
    ...
catch $err
    ...
end

# 返回
return                                # 结束工作流/子过程
return -1                             # 异常终止（主工作流）
return <value>                        # 返回值给调用方

# 标签跳转
@label_name                           # 定义标签
goto label_name                       # 跳转到标签
```

## 九、条件表达式

```
# 基础条件
$var contains "文本"                  # 包含子串
$var equals "文本"                    # 完全相等
$var in ["a", "b"]                    # 等于列表中任一项
$var is_empty                         # 为空或不存在
$var > N / < N / >= N / <= N         # 数值比较
$var == N / != N                      # 容差比较（浮点安全）
$var                                  # truthy 检查
not <条件>                            # 取反

# 组合条件
<cond> and <cond>                     # 逻辑与（优先级高于 or）
<cond> or <cond>                      # 逻辑或
(<cond>)                              # 括号分组

# 算术条件
$a + 1 > $b * 2                       # 两侧支持 + - * /
($x + $y) / 2 >= 60
```

falsy 值：`null` / `false` / `""` / `0` / `{}` / `[]`

## 十、内置函数（72 个）

> 完整签名与说明见 [06-functions.md](06-functions.md)。

| 类别 | 函数 |
|------|------|
| **基础运算（8）** | `add` `sub` `mul` `div` `mod` `min` `max` `abs` |
| **字典/列表（12）** | `len` `keys` `values` `has_key` `del_key` `remove` `slice` `range` `count_nonempty` `contains` `find_key` `append` |
| **字符串（9）** | `concat` `substr` `split` `replace` `match` `trim` `upper` `lower` `to_num` |
| **装备（6）** | `to_equipment` `make_fingerprint` `affix_cap` `chengyin_cap` `is_good_equip` `evaluate` |
| **背包（3）** | `check_scroll` `notify_scroll` `scroll_advance` |
| **时间（2）** | `clock` `datetime` |
| **系统/交互（7）** | `confirm` `pause` `notify` `input` `save` `panel_rows` `panel_cols` |
| **图色（7）** | `pixel` `bright` `color_ratio` `bright_segs` `color_vec` `find_icons` `find_multi_color` |
| **玩家档案（5）** | `profile_get` `profile_set` `profile_inc` `profile_model` `profile_all` |

## 十一、模块化

```
# import — 引入外部 def
import "subcall/navigation.wf"

# def — 定义子过程
def process_slot($row, $col)
    ...
end

# call — 调用（变量隔离，子过程不影响调用方）
call proc_name($arg1, $arg2)
call $result = proc_name()            # 接收返回值

# 工作流参数由 .wf 文件头的 #% 元数据声明
# 详见 07.1-metadata.md
```

> 子过程异常返回约定：`return -1` 表示失败，调用方检查 `$result < 0`。详见 [07-subworkflows.md](07-subworkflows.md)。

## 十二、数据通道

| 通道 | 生命周期 | 隔离性 | 用途 |
|------|----------|--------|------|
| **session** | 永久（跨执行） | 共享引用 | 角色级持久状态 |
| **context** | 单次执行 | 共享引用 | 过程间数据传递 |
| **variables** | 单次执行 | 按 call 隔离 | 过程局部计算 |
| **output** | 单次执行 | 按 call 隔离 | 返回给上层调度者 |

```
# session — 持久化
eval session.key = value
eval $val = session.key
eval save()                           # 强制落盘

# context — 跨过程共享
eval context.key = value
eval $val = context.key

# output — 返回结果
collect $var as "label"               # 写入 output dict
call proc() as $output                # 接收子过程 output
```

> Profile（玩家档案）通过 `profile_get` / `profile_set` / `profile_inc` 函数访问，独立于四通道。详见 [09-data-channels.md](09-data-channels.md)。
