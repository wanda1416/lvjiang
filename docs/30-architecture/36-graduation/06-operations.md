# 操作指南

## 批量导入

将所有流派的 Excel 源文件放入 `data/temp/excel/` 后执行批量导入：

```powershell
# 检查模式：解析全部公式，与 Excel 缓存值对账，不写入文件
.venv\Scripts\python.exe scripts\extract_graduation_data.py --check

# 正式导入：生成 JSON 文件到 config/system/yysls/graduation/
.venv\Scripts\python.exe scripts\extract_graduation_data.py

# 运行测试验证
.venv\Scripts\python.exe -m pytest tests\test_graduation_excel_model.py -q -p no:cacheprovider
```

### 批量脚本的文件命名约定

脚本通过文件名前缀匹配流派：

| 前缀 | 流派 |
|---|---|
| `*鸣金虹*` | 鸣金·虹 |
| `*鸣金影*` | 鸣金·影 |
| `*裂石威*` | 裂石·威 |
| `*裂石钧*` | 裂石·钧 |
| `*牵丝玉*` | 牵丝·玉 |
| `*牵丝霖*` | 牵丝·霖 |
| `*牵丝翊*` | 牵丝·翊 |
| `*破竹尘*` | 破竹·尘 |
| `*破竹风*` | 破竹·风 |
| `*破竹鸢*` | 破竹·鸢 |
| `*破竹樽*` | 破竹·樽 |

每个流派目录下**必须恰好一个**匹配文件（排除含"副本"的文件）。多匹配或少匹配都会报错。

### 单流派操作

```powershell
# 只检查鸣金·虹
.venv\Scripts\python.exe scripts\extract_graduation_data.py --check --school "鸣金·虹"

# 只导入鸣金·虹
.venv\Scripts\python.exe scripts\extract_graduation_data.py --school "鸣金·虹"
```

### 自定义方案名

批量脚本默认生成"基础方案"。指定其他方案名：

```powershell
.venv\Scripts\python.exe scripts\extract_graduation_data.py --scheme "会心大外流"
```

## 单流派导入（UI）

在桌面端通过"流派配置 → 方案管理"导入：

1. 选择目标流派
2. 点击"导入"，选择 `.xlsx` 文件
3. 输入方案名称
4. 系统执行 `import_graduation_scheme()` → 别名解析 → 编译 → 验证 → 写入 JSON
5. 成功后自动注册到流派的 `schemes` 列表

导入完成后会清除 `GenericCalculator._load_data` 的 LRU 缓存，确保后续计算使用新数据。

## 验证对账

### 导入时自动验证

`_compile_v2()` 在编译完成后执行双重验证：

1. **Excel 缓存对账**：用 `FormulaModel` 直接求值的结果与 Excel 保存的缓存值对比
2. **编译结果对账**：用 `ProgramRuntime` 以满值输入执行的结果与 `FormulaModel` 结果对比

两者容差均为 `max(1e-6, abs(expected) * 1e-10)`。任一验证失败都会阻断导入。

### 批量脚本验证

`extract_graduation_data.py` 额外检查：用满值输入计算出的毕业率必须是 `100.00%`。否则说明 Excel 满值表本身有误。

### 独立验证

```python
from lvjiang.apps.yysls.core.graduation.graduation_converter import validate_model
import json

model = json.load(open("config/system/yysls/graduation/鸣金·虹_基础方案.json", encoding="utf-8"))
results = validate_model(model)  # 返回 dict，或抛出 FormulaError
```

## 新增函数扩展

当 Excel 工作簿引入不在支持列表中的函数时：

1. **公式解析器**（`excel_formula.py`）：`FormulaParser` 自动为任意函数名生成 `call` AST 节点，通常无需修改
2. **公式模型**（`excel_formula.py`）：在 `FormulaModel._call()` 中添加求值逻辑
3. **程序编译器**（`graduation_program.py`）：在 `ProgramCompiler._call()` 中添加编译期处理
   - 如果函数参数全是常量，可以在编译期直接求值（常量折叠）
   - 如果需要运行时输入，生成对应的运算节点
4. **运行时**（`graduation_program.py`）：在 `evaluate_operation()` 中添加 opcode 执行语义
5. **测试**（`tests/test_graduation_excel_model.py`）：添加新函数的单元测试

**约束**：不得静默读取旧缓存值替代计算。未实现函数必须抛出 `FormulaError`。

函数语言规范详见 [02-formula-language.md](02-formula-language.md#扩展流程)。

## 缓存管理

`GenericCalculator._load_data()` 使用 `@lru_cache` 按 `(school, scheme)` 缓存 JSON 加载结果。以下场景需要清除缓存：

- 覆写方案 JSON 文件后
- 重新导入 Excel 后

```python
from lvjiang.apps.yysls.core.graduation import invalidate_graduation_cache
invalidate_graduation_cache()
```

UI 的方案导入流程会自动调用此函数。

## 故障排查

| 症状 | 可能原因 | 排查步骤 |
|---|---|---|
| UI 显示"未实现" | 流派名不匹配（中间点缺失） | 检查 `game_config.get_schools()` 返回值与 JSON 文件名是否一致 |
| 导入失败：`FormulaError` | Excel 使用了不支持的函数 | 查看错误信息中的函数名，按扩展流程添加支持 |
| 导入失败：`RuntimeError` 别名解析 | Excel 简称未在 `affix_aliases` 中配置 | 在词组配置中添加对应别名 |
| 导入失败：毕业率不是 100% | Excel 满值表有误 | 检查 Excel 源文件中的满值输入 |
| 计算结果与 Excel 不一致 | 编译后程序与公式结果偏差 | 运行 `--check` 模式查看详细偏差 |
| 修改 Excel 后计算不变 | LRU 缓存未清除 | 调用 `invalidate_graduation_cache()` 或重启应用 |
| JSON 加载失败：`unsupported schema version` | JSON 文件是 v1 格式 | 重新从 Excel 导入生成 v2 格式 |
