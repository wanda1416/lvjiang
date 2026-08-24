# 毕业率计算引擎

毕业率计算引擎是一套从 Excel 工作簿到 DPS / 毕业率数值的四层管线。它将策划维护的 Excel 期望表编译为紧凑的节点程序，运行时只需注入战斗属性即可求值，不依赖 Excel 或 openpyxl。

## 四层管线

| 层 | 模块 | 职责 |
|---|---|---|
| **L1 公式解析** | `excel_formula.py` | Excel 公式子集的词法 / 语法分析器（Pratt parser），输出 AST |
| **L2 模型转换** | `graduation_converter.py` | Excel → JSON 工作簿模型；别名解析；v2 编译与验证 |
| **L3 程序编译** | `graduation_program.py` | `ProgramCompiler`：部分求值 + 死代码消除，生成紧凑节点程序 |
| **L4 运行时** | `graduation.py` | `GenericCalculator` + `ProgramRuntime`：注入战斗属性，执行节点程序 |

## 代码文件索引

| 文件 | 路径 |
|---|---|
| 公式解析器 | `src/lvjiang/apps/yysls/evaluator/excel_formula.py` |
| 模型转换 | `src/lvjiang/apps/yysls/evaluator/graduation_converter.py` |
| 程序编译器 | `src/lvjiang/apps/yysls/evaluator/graduation_program.py` |
| 运行时 | `src/lvjiang/apps/yysls/evaluator/graduation.py` |
| 批量提取脚本 | `scripts/extract_graduation_data.py` |
| JSON 数据目录 | `config/system/yysls/graduation/` |

## 子文档导航

| 文档 | 内容 |
|---|---|
| [01-data-flow.md](01-data-flow.md) | 端到端数据流与各层职责 |
| [02-formula-language.md](02-formula-language.md) | Excel 公式子集规范：词法、语法、函数清单、扩展流程 |
| [03-json-model.md](03-json-model.md) | JSON v2 Schema 契约：字段定义、类型、约束 |
| [04-alias-resolution.md](04-alias-resolution.md) | 别名解析规则：约束求解流程、错误处理、配置依赖 |
| [05-compiler-runtime.md](05-compiler-runtime.md) | 编译器与运行时：部分求值、opcode 语义表、执行模型 |
| [06-operations.md](06-operations.md) | 操作指南：导入、验证、对账、扩展、故障排查 |
| [07-skill-rotation.md](07-skill-rotation.md) | 技能轴查看：从原始 Excel 读竞速轴，把 DPS 拆回每个技能 |
