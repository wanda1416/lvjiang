# 端到端数据流

## 管线总览

```
┌─────────────────────────────────────────────────────────────────────┐
│  策划维护的 Excel 工作簿（.xlsx）                                      │
│  data/temp/excel/*鸣金虹*.xlsx  …                                    │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  L2  convert_workbook()
                           │  ┌─────────────────────────────────────┐
                           │  │ 1. openpyxl 读取公式 + 缓存值          │
                           │  │ 2. L1 逐公式 parse_formula() → AST   │
                           │  │ 3. 别名解析 _affix_input_names()      │
                           │  │ 4. 提取环境 _extract_environment()    │
                           │  │ 5. L3 ProgramCompiler.compile()      │
                           │  │ 6. 验证：编译结果 vs Excel 缓存对账    │
                           │  └─────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  JSON v2 模型                                                       │
│  config/system/yysls/graduation/{流派}_{方案}.json                    │
│  包含：baseline_attrs / environment / reference / program            │
└──────────────────────────┬──────────────────────────────────────────┘
                           │  L4  GenericCalculator.calculate(attrs)
                           │  ┌─────────────────────────────────────┐
                           │  │ 1. 从 CombatAttributes 提取输入向量    │
                           │  │ 2. ProgramRuntime(program, values)   │
                           │  │ 3. 惰性递归求值 + 节点缓存             │
                           │  └─────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│  GraduationResult                                                   │
│  total_damage / dps / graduation_rate / baseline_dps / combat_time  │
└─────────────────────────────────────────────────────────────────────┘
```

## 各层输入 / 输出契约

| 层 | 输入 | 输出 | 触发时机 |
|---|---|---|---|
| **L1 公式解析** | Excel 公式字符串（如 `=B2*C2+D2`） | AST 节点（`{op, value/args/left/right}`） | L2 转换时逐公式调用 |
| **L2 模型转换** | `.xlsx` 文件路径 + 流派名 | JSON v2 模型（dict） | 用户导入 Excel 或批量脚本 |
| **L3 程序编译** | JSON 工作簿模型 + 输入绑定 | 紧凑节点程序（inputs / nodes / outputs） | L2 内部调用 |
| **L4 运行时** | 节点程序 + `CombatAttributes` | `GraduationResult` | 用户切换装备 / 方案时 |

### L1 公式解析

- **输入**：以 `=` 开头的 Excel 公式字符串
- **输出**：Pratt parser 产出的 AST，节点类型包括 `literal`、`ref`、`unary`、`binary`、`call`
- **缓存**：`parse_formula()` 使用 `@lru_cache(maxsize=100_000)` 全局缓存 AST
- **详见**：[02-formula-language.md](02-formula-language.md)

### L2 模型转换

- **输入**：Excel 文件路径 + 流派名（如 `"鸣金·虹"`）
- **输出**：完整的 JSON v2 模型，包含工作表数据、编译后的节点程序、基准属性、环境配置
- **关键步骤**：
  1. 用 openpyxl 分别加载公式模式（`data_only=False`）和缓存模式（`data_only=True`）
  2. 遍历所有工作表，将每个非空单元格提取为 `{formula, cached}` 或 `{value}`
  3. 调用别名解析将 Excel 简称映射为标准词条名
  4. 调用 `ProgramCompiler.compile()` 生成节点程序
  5. 用 `validate_model()` 对账：编译结果必须与 Excel 缓存值一致
- **详见**：[04-alias-resolution.md](04-alias-resolution.md)

### L3 程序编译

- **输入**：工作簿模型 + 绑定表（Excel 单元格地址 → `{kind, name}`）
- **输出**：紧凑节点程序
  - `inputs`：运行时输入规格列表
  - `nodes`：节点数组，每个节点为 `[op, *依赖索引]`
  - `outputs`：命名输出 → 节点索引映射
- **核心优化**：常量折叠 + 死代码消除，将数千个公式压缩为数百个节点
- **详见**：[05-compiler-runtime.md](05-compiler-runtime.md)

### L4 运行时

- **输入**：节点程序 + `CombatAttributes` 实例
- **输出**：`GraduationResult` 数据类
  - `total_damage`：总伤害
  - `dps`：每秒伤害
  - `graduation_rate`：毕业率（DPS / baseline_dps）
  - `baseline_dps`：基准 DPS（来自 Excel 满值表）
  - `combat_time`：战斗时间
- **缓存**：`GenericCalculator._load_data()` 使用 `@lru_cache` 按 `(school, scheme)` 缓存 JSON 加载

## 关键设计决策

### 为什么编译而非解释

初版使用 `FormulaModel` 直接解释执行 Excel 公式。性能分析发现：

- 大型模型（如鸣金·影，27K 公式）单次 `calculate()` 耗时 ~567ms
- 每次调用创建新的 `FormulaModel` 实例，AST 和范围缓存无法复用
- `value()` 被调用 601K 次，`_eval()` 被调用 1.77M 次

优化后引入 `ProgramCompiler` + `ProgramRuntime`：

- **编译期**（导入时一次）：部分求值消除所有常量分支，死代码消除移除不可达节点
- **运行期**：节点程序只有 `const` / `input` / 运算节点，惰性递归 + 缓存求值
- 性能与公式数量无关，只与实际依赖链长度相关

### 三元绑定约束

毕业率计算器要求三个标识严格一致：

1. `game_config.get_schools()` 返回的流派名（如 `"鸣金·虹"`，含中间点）
2. JSON 文件名（如 `鸣金·虹_基础方案.json`）
3. `get_graduation_calculator()` 的查询参数

任一层不一致会导致计算器返回 `None`，UI 显示"未实现"。

### 与外部计算器的关系

毕业率由本文描述的 Excel 编译管线在本地计算，纯 Python 实现，过程可审计。

律匠也支持把装备数据导出给外部计算器交叉比对，传输格式见
[装备数据外部交换格式](references/leoq7-data-format.md)。导出只涉及装备数据本身，
不引入外部的计算逻辑。
