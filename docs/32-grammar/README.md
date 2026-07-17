# DSL 语法文档

工作流 DSL（Domain Specific Language）语法规范，用于描述 `.wf` 工作流文件。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-basics.md](01-basics.md) | 基础约定（注释、标识符、引用）与变量系统（声明、类型、字段访问、运行时状态） |
| [02-instructions.md](02-instructions.md) | 操作对象语义（Area/Action）、指令集逐条详解（click/drag/wait/scan/recognize/collect/eval/call/log）、find_key 内置函数 |
| [03-control-flow.md](03-control-flow.md) | 控制流指令（if/for/loop/break/return/goto）、条件表达式（基础/组合/短路求值） |
| [04-eval-and-functions.md](04-eval-and-functions.md) | eval 语法、内置函数列表、字典变量、列表变量与 for 遍历 |
| [05-subworkflows.md](05-subworkflows.md) | 子工作流调用（call with/read）、隔离机制、路径解析、契约模式、工作流参数声明 |
| [06-examples.md](06-examples.md) | 完整示例：装备分析、调律决策树、批量调律 |
