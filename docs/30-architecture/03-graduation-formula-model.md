# 毕业率公式模型

## 数据流

`data/temp/excel/*.xlsx` 是 DPS 与毕业率的唯一真源。开发期转换脚本读取公式和
Excel 缓存值，生成 `config/system/yysls/graduation/{流派}_{方案}.json`；应用运行时只加载 JSON，
计算阶段不依赖 Excel 或 openpyxl；只有“游戏配置 → 流派配置 → 方案管理”的
导入动作需要 openpyxl。

```text
Excel 工作簿
  → scripts/extract_graduation_data.py
  → 词组配置 affix_aliases 严格校验
  → JSON 公式模型
  → FormulaModel（受限公式执行器）
  → DPS / RDPS / 毕业率
```

## 更新表格

替换 `data/temp/excel` 中对应文件后执行：

```powershell
.venv\Scripts\python.exe scripts\extract_graduation_data.py --check
.venv\Scripts\python.exe scripts\extract_graduation_data.py
.venv\Scripts\python.exe -m pytest tests\test_graduation_excel_model.py -q -p no:cacheprovider
```

`--check` 会在不写文件的情况下解析全部公式，并将 JSON 引擎结果与 Excel 保存的
缓存结果对账。对账失败时不得发布新 JSON。

也可以只检查或生成一个流派：

```powershell
.venv\Scripts\python.exe scripts\extract_graduation_data.py --check --school "鸣金·虹"
```

脚本默认生成“基础方案”，可用 `--scheme "方案名"` 指定其他名称。桌面端也可以在
“方案管理”中选择 Excel、编辑方案名并直接生成；成功后会把方案注册到对应流派的
`schools.<流派>.schemes`。主面板在弓玦之后显示方案下拉框，并将当前方案传给计算器。

## JSON 契约

- `schema_version`：模型结构版本。
- `formula_language`：当前为 `excel_subset_v1`。
- `model.source`：Excel 文件名、版本与 SHA-256。
- `inputs`：语义字段、单位及目标单元格。
- `outputs`：战斗时间、总伤害、DPS、RDPS、毕业率引用。
- `sheets`：工作表尺寸，以及非空单元格的常量、公式和 Excel 缓存值。

百分比统一使用小数比例，例如 `8.52%` 保存为 `0.0852`。

## 词条简称

Excel 中的“全武增”“首领增”“拳甲增”“蓄力技定音”等简称不在毕业率
模块维护映射。唯一权威来源是 `game_config.yaml` 顶层的 `affix_aliases`，也可在
“游戏配置 → 词组配置”中对每个具体词条编辑多个别名。

转换阶段严格通过词组配置将 Excel 简称解析成一个或多个精准词条名，并以
`affix_names` 写入格式化 JSON。运行时只使用这些标准名称汇总当前装备数值，不进行
包含、前后缀或流派推断。转换阶段遇到未配置的 Excel 简称会直接失败。

## 公式安全边界

运行时不使用 `eval()`。当前仅实现工作簿实际需要的：

- 四则运算、乘方、连接与比较；
- 单元格、矩形区域和整列引用；
- `IF`、`IFERROR`、`OR`、`MIN`、`MAX`、`SUM`；
- `VLOOKUP`、`XLOOKUP`。

未来 Excel 引入新函数时，转换阶段会直接失败。应先在公式引擎中实现并增加测试，
不能静默读取旧缓存值替代计算。
