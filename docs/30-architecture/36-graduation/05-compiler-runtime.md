# 编译器与运行时

`ProgramCompiler` 在导入阶段将 Excel 工作簿模型编译为紧凑的节点程序；`ProgramRuntime` 在运行时执行该程序。两者共同构成了 L3 / L4 层。

## ProgramCompiler

### 设计目标

- **部分求值**：导入时已知的值（常量单元格、查找表）在编译期折叠为常量
- **死代码消除**：只保留从输出可达的节点
- **循环引用检测**：编译期发现循环依赖并报错
- **输入最小化**：只保留实际影响输出的输入变量

### 编译流程

```
工作簿模型 + 绑定表
    ↓
_ref() 递归展开单元格依赖
    ↓
_expr() 将 AST 转为节点图
    ↓
_emit() 尝试常量折叠
    ↓
_prune() 可达性分析 + 紧凑化
    ↓
节点程序 {inputs, nodes, outputs}
```

### 绑定表

绑定表定义了 Excel 单元格地址到运行时输入的映射：

```python
bindings = {
    "期望!B2": {"kind": "field", "name": "min_outer"},
    "期望!B3": {"kind": "field", "name": "min_mingjin"},
    "期望!B19": {"kind": "affix", "name": "剑武学增伤"},
    ...
}
```

当 `_ref()` 遇到绑定地址时，生成 `input` 节点而非 `const` 节点。

### 常量折叠

`_emit()` 在生成运算节点前检查所有操作数是否为常量：

```python
def _emit(self, op, args):
    constants = [self._const_value(arg) for arg in args]
    if all(item[0] for item in constants):
        try:
            return self._constant(evaluate_operation(op, [item[1] for item in constants]))
        except (ArithmeticError, FormulaError, TypeError, ValueError):
            if op != "iferror":
                raise
    return self._node(op, *args)
```

效果：`IF(TRUE, A, B)` 在编译期直接折叠为 `A` 的节点，不生成 `if` 节点。

### 死代码消除

`_prune()` 从输出节点出发做可达性分析：

1. 从所有输出节点开始深度优先遍历
2. 标记所有可达节点
3. 按原始顺序保留可达节点，重新编号
4. 只保留被使用的输入

### 循环引用检测

`_ref()` 维护 `self.active` 集合跟踪当前展开路径。遇到已在 `active` 中的地址时抛出 `FormulaError("circular reference at {key}")`。

### VLOOKUP / XLOOKUP 的特殊编译

查找函数在编译期有特殊的处理策略：

- **VLOOKUP**：不展开整个查找表的所有列，而是根据列索引参数（必须是常量）只展开目标列
- **XLOOKUP**：查找键必须是常量，直接定位匹配行并返回对应节点的引用
- 查找表数据在编译期完全解析为常量节点，运行时不参与查找

这意味着查找表是**静态的**——如果 Excel 中的查找表引用了运行时输入，编译会失败。

### 节点去重

`_node()` 使用 `self.node_index` 字典做全局去重。相同 `(op, *args)` 的节点只生成一次，后续引用复用索引。这自然实现了公共子表达式消除（CSE）。

## 节点程序结构

```json
{
  "inputs": [
    {"kind": "field", "name": "min_outer"},
    {"kind": "affix", "name": "剑武学增伤"},
  ],
  "nodes": [
    ["const", 109.1],
    ["input", 0],
    ["const", 200],
    ["add", 1, 2],
    ["mul", 3, 0],
    ["if", 4, 1, 2],
    ["iferror", 3, 0]
  ],
  "outputs": {
    "dps": 5,
    "graduation_rate": 6
  }
}
```

每个节点是一个数组：

- 第一个元素是 opcode（字符串）
- 后续元素是依赖节点的索引（整数）或常量值

## opcode 语义表

### 一元运算

| opcode | 格式 | 语义 |
|---|---|---|
| `pos` | `["pos", x]` | `+x`（数值化） |
| `neg` | `["neg", x]` | `-x` |

### 二元运算

| opcode | 格式 | 语义 |
|---|---|---|
| `add` | `["add", a, b]` | `a + b` |
| `sub` | `["sub", a, b]` | `a - b` |
| `mul` | `["mul", a, b]` | `a * b` |
| `div` | `["div", a, b]` | `a / b`（除零抛异常） |
| `pow` | `["pow", a, b]` | `a ** b` |
| `concat` | `["concat", a, b]` | `f"{a}{b}"`（字符串连接） |

### 比较运算

| opcode | 格式 | 语义 |
|---|---|---|
| `eq` | `["eq", a, b]` | `a = b` |
| `ne` | `["ne", a, b]` | `a <> b` |
| `lt` | `["lt", a, b]` | `a < b` |
| `gt` | `["gt", a, b]` | `a > b` |
| `le` | `["le", a, b]` | `a <= b` |
| `ge` | `["ge", a, b]` | `a >= b` |

比较运算的类型转换规则：双方均为数值时按数值比较；任一方为字符串时 `casefold()` 后按字符串比较。

### 控制流

| opcode | 格式 | 语义 |
|---|---|---|
| `if` | `["if", cond, when_true, when_false]` | `when_true if cond else when_false` |
| `iferror` | `["iferror", primary, fallback]` | 主表达式异常时返回 fallback |

### 聚合运算

| opcode | 格式 | 语义 |
|---|---|---|
| `sum` | `["sum", a, b, ...]` | 所有参数求和 |
| `min` | `["min", a, b, ...]` | 最小值（空参数返回 0） |
| `max` | `["max", a, b, ...]` | 最大值 |
| `or` | `["or", a, b, ...]` | 逻辑或（任意参数为真） |

### 基础节点

| opcode | 格式 | 语义 |
|---|---|---|
| `const` | `["const", value]` | 常量（数值或字符串） |
| `input` | `["input", index]` | 运行时输入（`index` 指向 `inputs` 数组） |

## ProgramRuntime

### 执行模型

```python
class ProgramRuntime:
    def __init__(self, program, inputs):
        self.program = program
        self.inputs = inputs    # list[float]，按位置对应 program["inputs"]
        self.cache = {}         # 节点索引 → 求值结果

    def value(self, node):
        if node in self.cache:
            return self.cache[node]
        entry = self.program["nodes"][node]
        op = entry[0]
        if op == "const":
            result = entry[1]
        elif op == "input":
            result = self.inputs[entry[1]]
        elif op == "if":
            condition = self.value(entry[1])
            result = self.value(entry[2] if condition else entry[3])
        elif op == "iferror":
            try:
                result = self.value(entry[1])
            except (ArithmeticError, FormulaError, TypeError, ValueError):
                result = self.value(entry[2])
        else:
            result = evaluate_operation(op, [self.value(i) for i in entry[1:]])
        self.cache[node] = result
        return result

    def outputs(self):
        return {name: float(self.value(node))
                for name, node in self.program["outputs"].items()}
```

### 关键特征

- **惰性递归**：从输出节点出发，按需递归求值依赖节点
- **记忆化缓存**：每个节点只求值一次，后续通过 `self.cache` 直接返回
- **短路求值**：`if` 节点只求值选中的分支，不求值未选中的分支
- **异常捕获**：`iferror` 节点捕获主分支的所有异常

### 性能特征

| 指标 | 值 | 说明 |
|---|---|---|
| 编译时间 | ~100ms | 导入时一次，包含公式解析 + 常量折叠 + 死代码消除 |
| 运行时间 | < 1ms | 与公式数量无关，只与实际依赖链长度相关 |
| 缓存命中 | O(1) | 每个节点只求值一次 |
| 内存占用 | ~数十 KB | 节点程序紧凑存储 |

对比优化前的 `FormulaModel` 解释执行：

| 指标 | FormulaModel（旧） | ProgramRuntime（新） |
|---|---|---|
| 单次计算 | ~567ms（鸣金·影） | < 1ms |
| AST 缓存 | 无（每次重建） | 编译期固化 |
| 公式遍历 | 全部公式 | 只遍历可达节点 |
