# DSL 基础指令

collect、eval、call、log 等通用基础指令。感知类指令（scan/recognize/find/where）见 [04-data-flow.md](04-data-flow.md)。

## 目录

- [一、collect — 收集输出](#一collect--收集输出)
- [二、eval — 赋值](#二eval--赋值)
- [三、call — 调用子过程](#三call--调用子过程)
- [四、log — 日志输出](#四log--日志输出)

## 一、collect — 收集输出

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

## 二、eval — 赋值

调用内置函数、字面量赋值、字典字段赋值。

**语法**：

```
eval $var = func(args...)         # 函数调用并赋值
eval $var = "字符串"              # 字面量赋值
eval $var = 123                   # 数字赋值
eval $var = {}                    # 初始化空字典
eval $var = {"k": "v"}              # 字典字面量（支持嵌套）
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

## 三、call — 调用子过程

调用同文件内 `def` 定义的子过程，或 `import` 引入的外部子过程。

**语法**：

```
call proc_name()                                  # 简单调用
call proc_name($arg1, $arg2)                      # 传入参数
call $result = proc_name()                        # 获取返回值
call proc_name() as $output                       # 获取 output dict
call $result = proc_name() as $output             # 同时获取返回值与 output
```

详细说明见 [07-subworkflows.md](07-subworkflows.md)。

## 四、log — 日志输出

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
