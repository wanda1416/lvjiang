# DSL 控制流概览

> 基础指令（collect/eval/call/log/default）见 [03.1-basic-commands.md](03.1-basic-commands.md)。

## 指令总览

| 指令 | 语法 | 说明 | 详见 |
|---|---|---|---|
| if | `if <cond> ... [else ...] end` | 条件分支，可嵌套 | [05.1](05.1-loops-branches.md#一if--else--条件分支) |
| for | `for $var in [a, b, c] ... end` 或 `for $var in $list ... end` | 枚举循环，迭代静态列表或列表变量 | [05.1](05.1-loops-branches.md#二for--枚举循环) |
| loop | `loop <N> ... end` | 计数循环，N 为正整数或变量引用 | [05.1](05.1-loops-branches.md#loop-n--计数循环) |
| loop while | `loop while <cond> ... end` | 条件循环，每轮前求值，truthy 则执行 | [05.1](05.1-loops-branches.md#loop-while--条件为真时循环) |
| loop until | `loop until <cond> ... end` | 条件循环，先执行再求值，truthy 则退出（至少执行一次） | [05.1](05.1-loops-branches.md#loop-until--条件为真时退出至少执行一次) |
| break | `break` | 跳出最内层 for/loop | [05.1](05.1-loops-branches.md#break--跳出循环) |
| continue | `continue` | 跳过当前迭代，进入下一轮 | [05.1](05.1-loops-branches.md#continue--跳过当前迭代) |
| try/catch | `try ... catch [$err] ... end` | 异常捕获与兜底 | [05.2](05.2-flow-jumps.md#一try--catch--异常处理) |
| return | `return` 或 `return <value>` | 结束当前工作流或子过程，可携带返回值 | [05.2](05.2-flow-jumps.md#二return--结束工作流或返回子过程) |
| label | `@label_name` | 标签，goto 的目标 | [05.2](05.2-flow-jumps.md#三label--goto--标签跳转) |
| goto | `goto label_name` | 同文件内无条件跳转 | [05.2](05.2-flow-jumps.md#三label--goto--标签跳转) |

`if` / `loop while` / `loop until` 的条件写法（`contains` / `equals` / `and` / `or` /
算术比较等）三者通用，集中在 [05.3-conditions.md](05.3-conditions.md)。

## 两类指令的区别

| | 分支与循环 | 异常与跳转 |
|---|---|---|
| 指令 | `if` / `for` / `loop` / `break` / `continue` | `try` / `return` / `goto` |
| 作用 | 在**块结构内**决定执行哪一段、执行几遍 | **跳出**当前顺序执行，转交控制权 |
| 边界 | 不越过所在的块 | `return` 结束整个过程；`goto` 跨语句跳转；`try` 拦截异常 |

`break` / `continue` / `return` / `goto` 在引擎内都实现为控制流信号，
因此会**穿透** `try/catch` 而不被当成异常拦截——细节见
[05.2 捕获范围](05.2-flow-jumps.md#捕获范围)。

## 详细文档

- [05.1-loops-branches.md](05.1-loops-branches.md) — if / for / loop / break / continue
- [05.2-flow-jumps.md](05.2-flow-jumps.md) — try / catch、return、label / goto
- [05.3-conditions.md](05.3-conditions.md) — 条件表达式：基础条件、组合条件、算术比较
