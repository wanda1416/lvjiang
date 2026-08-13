# DSL 语法文档

工作流 DSL（Domain Specific Language）语法规范，用于描述 `.wf` 工作流文件。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-basics.md](01-basics.md) | 词法与值、引用模型（`[]` vs `$`）、变量系统、表达式 |
| [02-concepts.md](02-concepts.md) | 领域概念：场景（Scene）、布局（Layout）、Area/Action、Panel |
| [03-interaction.md](03-interaction.md) | 交互指令：click、drag、wait、align |
| [04-data-flow.md](04-data-flow.md) | 数据指令：scan、recognize、find、collect、eval、call、log |
| [05-control-flow.md](05-control-flow.md) | 控制流指令（if/for/loop/while/continue/try-catch/break/return/goto）、条件表达式 |
| [06-functions.md](06-functions.md) | 内置函数全集：基础运算、字典/列表、字符串、装备处理、用户交互（confirm/pause/notify/input） |
| [07-subworkflows.md](07-subworkflows.md) | 模块化（import/def/call）、变量隔离、工作流参数声明 |
| [08-examples.md](08-examples.md) | 完整示例：装备分析、调律决策树、批量调律、异常重试与用户介入 |
| [09-data-channels.md](09-data-channels.md) | 数据通道：session/context/variables/output 的生命周期与隔离性 |
