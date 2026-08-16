# Excel 公式子集规范

公式语言标识为 `excel_subset_v1`。解析器不依赖任何外部库，仅使用正则表达式词法分析 + Pratt 递归下降语法分析。运行时不使用 `eval()`。

## 词法规则

词法分析器通过 `_TOKEN_RE` 正则表达式将公式文本拆分为以下 token 类型：

| Token 类型 | 模式 | 示例 |
|---|---|---|
| `string` | `"(?:[^"]\|"")*"` | `"hello"` |
| `ref` | `[sheet!]cell[:cell]` 或 `[sheet!]col:col` | `B2`、`期望!B2`、`A1:C10`、`A:A` |
| `number` | `(?:\d+(?:\.\d*)?\|\.\d+)([Ee][+-]?\d+)?%?` | `42`、`3.14`、`.5`、`50%` |
| `op` | `<>` `<=` `>=` 或单字符 `+\-*/^&=<>(),` | `+`、`<=`、`<>` |
| `name` | `[A-Za-z_][A-Za-z0-9_.]*` | `IF`、`VLOOKUP` |

**特殊规则**：

- 公式开头的 `=` 被自动剥离
- 百分号后缀（如 `50%`）在词法阶段转换为 `0.5`
- 单元格引用中的 `$` 符号被忽略（`$B$2` 等同于 `B2`）
- 工作表名含单引号时使用 `''` 转义（`'Sheet''1'!A1`）

## 语法（Pratt Parser）

语法分析器使用 Pratt 算法处理运算符优先级和结合性：

| 优先级 | 运算符 | 结合性 | 说明 |
|---|---|---|---|
| 1 | `=` `<>` `<` `>` `<=` `>=` | 左结合 | 比较运算 |
| 2 | `&` | 左结合 | 字符串连接 |
| 3 | `+` `-` | 左结合 | 加减 |
| 4 | `*` `/` | 左结合 | 乘除 |
| 5 | `^` | **右结合** | 乘方 |
| 6 | 一元 `+` `-` | 前缀 | 正负号 |

### AST 节点类型

| 节点 op | 字段 | 说明 |
|---|---|---|
| `literal` | `value` | 数值、布尔或字符串常量（已去引号） |
| `ref` | `value` | 单元格或范围引用 |
| `unary` | `operator`, `arg` | 一元正 / 负 |
| `binary` | `operator`, `left`, `right` | 二元运算 |
| `call` | `name`, `args` | 函数调用 |

## 支持的函数

| 函数 | 参数 | 说明 |
|---|---|---|
| `IF` | `condition, when_true[, when_false]` | 条件分支；`when_false` 缺省返回 `False` |
| `IFERROR` | `primary, fallback` | 主表达式抛出 `ArithmeticError` / `FormulaError` 时返回 fallback |
| `OR` | `arg1, arg2, ...` | 逻辑或；任意参数为真则返回 `True` |
| `MIN` | `arg1, arg2, ...` | 最小值；参数可以是范围 |
| `MAX` | `arg1, arg2, ...` | 最大值；参数可以是范围 |
| `SUM` | `arg1, arg2, ...` | 求和；参数可以是范围 |
| `VLOOKUP` | `needle, table_range, col_index[, match_mode]` | 垂直查找；支持精确和近似匹配 |
| `XLOOKUP` | `needle, lookup_range, return_range[, default]` | 现代查找；精确匹配，支持默认值 |

### VLOOKUP 行为细节

- `match_mode` 缺省或为 `FALSE`/`0` 时为精确匹配
- `match_mode` 为 `TRUE`/`1` 时为近似匹配（`<=` 语义，返回最后一个满足条件的行）
- 查找表必须是矩形范围
- 列索引从 1 开始

### XLOOKUP 行为细节

- 仅支持精确匹配
- `needle` 可以是单个值或范围（批量查找）
- 查找失败时，若有 `default` 参数则返回默认值，否则抛出 `FormulaError`

## 不支持的语法

以下 Excel 特性**不在**公式子集范围内。遇到这些语法时，解析器会抛出 `FormulaError`：

| 不支持项 | 示例 | 说明 |
|---|---|---|
| 命名范围 | `=MyRange+1` | 只支持 `Sheet!Cell` 形式的直接引用 |
| 数组公式 | `{=SUM(A1:A10*B1:B10)}` | 不支持 CSE 数组公式 |
| 条件格式函数 | `COUNTIF`、`SUMIF`、`AVERAGEIF` | 未实现 |
| 文本函数 | `LEFT`、`RIGHT`、`MID`、`LEN`、`CONCATENATE` | 仅支持 `&` 连接 |
| 日期函数 | `DATE`、`TODAY`、`NOW` | 不涉及日期计算 |
| 查找函数 | `INDEX`、`MATCH`、`HLOOKUP`、`OFFSET` | 仅支持 `VLOOKUP` 和 `XLOOKUP` |
| 统计函数 | `AVERAGE`、`COUNT`、`STDEV` | 未实现 |
| 逻辑函数 | `AND`、`NOT`、`TRUE`、`FALSE` | `TRUE`/`FALSE` 作为字面量支持，`AND`/`NOT` 未实现 |
| 嵌套工作表引用 | 跨文件引用 `[Book1.xlsx]Sheet1!A1` | 不支持 |

## 单元格引用规则

### 地址规范化

所有引用在内部通过 `_canonical()` 方法规范化为 `SheetName!COORD` 格式：

- 去除 `$` 符号
- 转为大写
- 补全工作表前缀
- 单引号包裹的工作表名去除外层引号，`''` 还原为 `'`

### 引用类型

| 类型 | 示例 | 说明 |
|---|---|---|
| 单单元格 | `期望!B2` | 返回标量值 |
| 矩形范围 | `期望!A1:C10` | 返回二维数组 |
| 整列范围 | `期望!A:A` | 从第 1 行到工作表最大行 |
| 跨表引用 | `'技能表'!B5` | 单引号包裹的工作表名 |

## 比较运算语义

比较运算符遵循以下类型转换规则：

- **双方均为数值**：按数值比较
- **任一方为字符串**：双方 `casefold()` 后按字符串比较
- **布尔值**：`True` 视为 `1`，`False`/`None`/`""` 视为 `0`

## 扩展流程

当 Excel 工作簿引入新函数时，需要按以下步骤扩展。
完整操作指引与注意事项详见 [06-operations.md](06-operations.md#新增函数扩展)。

1. **公式解析器**（`excel_formula.py`）：`FormulaParser` 自动为任意函数名生成 `call` AST 节点，通常无需修改
2. **公式模型**（`excel_formula.py`）：在 `FormulaModel._call()` 中添加求值逻辑
3. **程序编译器**（`graduation_program.py`）：在 `ProgramCompiler._call()` 中添加编译期处理（常量折叠 / 特殊展开）
4. **运行时**（`graduation_program.py`）：在 `evaluate_operation()` 中添加 opcode 执行语义
5. **测试**（`tests/test_graduation_excel_model.py`）：添加新函数的单元测试

**重要约束**：扩展期间不得静默读取旧缓存值替代计算。遇到未实现函数时，转换阶段必须直接失败并抛出 `FormulaError`。
