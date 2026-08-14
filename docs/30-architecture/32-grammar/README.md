# DSL 语法文档

工作流 DSL（Domain Specific Language）语法规范，用于描述 `.wf` 工作流文件。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-basics.md](01-basics.md) | 词法与值、引用模型（`[]` vs `$`）、变量系统、表达式 |
| [02-concepts.md](02-concepts.md) | 领域概念：场景（Scene）、布局（Layout）、Area/Action、Panel |
| [03-1-interaction.md](03-1-interaction.md) | 交互指令：click、drag、wait、align、screenshot |
| [03-2-basic-commands.md](03-2-basic-commands.md) | 基础指令：collect、eval、call、log |
| [04-data-flow.md](04-data-flow.md) | 感知指令概览与对比表 |
| [04-1-scan.md](04-1-scan.md) | scan — OCR 文字扫描 |
| [04-2-recognize.md](04-2-recognize.md) | recognize — 图像材料识别 |
| [04-3-find.md](04-3-find.md) | find — 文字坐标定位 |
| [05-control-flow.md](05-control-flow.md) | 控制流指令（if/for/loop/while/continue/try-catch/break/return/goto）、条件表达式 |
| [06-functions.md](06-functions.md) | 内置函数总览与速查表 |
| [06-1-basic-functions.md](06-1-basic-functions.md) | 基础函数：算术运算、字典/列表操作、字符串处理 |
| [06-2-system-interaction.md](06-2-system-interaction.md) | 系统与交互函数：用户交互、系统函数、玩家档案 |
| [06-3-game-functions.md](06-3-game-functions.md) | 游戏相关函数：装备处理、背包遍历、综合示例 |
| [07-subworkflows.md](07-subworkflows.md) | 模块化（import/def/call）、变量隔离、工作流参数声明 |
| [08-examples.md](08-examples.md) | 完整示例：装备分析、调律决策树、批量调律、异常重试与用户介入 |
| [09-data-channels.md](09-data-channels.md) | 数据通道：session/context/variables/output 的生命周期与隔离性 |
