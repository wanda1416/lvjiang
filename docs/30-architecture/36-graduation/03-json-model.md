# JSON v2 Schema 契约

每个流派的每个毕业率方案对应一个 JSON 文件，存储编译后的计算模型。当前 schema 版本为 `2`。

## 文件命名

```
config/system/yysls/graduation/{流派}_{方案}.json
```

- `{流派}`：含中间点的完整流派名，如 `鸣金·虹`、`牵丝·玉`
- `{方案}`：方案名称，如 `基础方案`、`会心大外流`
- 示例：`鸣金·虹_基础方案.json`、`牵丝·玉_牵丝穿透流.json`

## 顶层结构

```json
{
  "schema_version": 2,
  "school": "鸣金·虹",
  "source": { ... },
  "baseline_attrs": { ... },
  "environment": { ... },
  "reference": { ... },
  "program": { ... }
}
```

### schema_version

整数，当前固定为 `2`。`GenericCalculator` 在加载时检查此字段，非 `2` 则抛出 `ValueError`。

### school

字符串，流派全名。必须与 `game_config.get_schools()` 返回的键完全一致。

### source

Excel 源文件溯源信息：

```json
{
  "file": "鸣金虹110级基础期望毕业率计算标准模板1.3.xlsx",
  "version": "1.3",
  "sha256": "70815ec8..."
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `file` | string | 原始 Excel 文件名 |
| `version` | string | 从文件名提取的版本号（如 `"1.3"`） |
| `sha256` | string | 文件内容的 SHA-256 哈希 |

## baseline_attrs

基准属性——即 Excel 满值表中各输入字段的满值。运行时用于初始化 `CombatAttributes`，也是 `get_graduation_scheme_inputs()` 返回的参考值。

```json
{
  "min_outer": 1722.4,
  "max_outer": 6332.4,
  "outer_pen": 63.5,
  "outer_bonus": 0.0,
  "min_mingjin": 491.3,
  "max_mingjin": 983.5,
  "mingjin_pen": 36.0,
  "mingjin_bonus": 0.15,
  "precision": 0.8271,
  "crit_rate": 0.1789,
  "intent_rate": 0.3929,
  "direct_crit": 0.0,
  "direct_intent": 0.023,
  "crit_dmg": 0.5,
  "intent_dmg": 0.402,
  "all_skill_bonus": 0.0852,
  "boss_bonus": 0.0887,
  "single_qs_bonus": 0.0,
  "group_qs_bonus": 0.0,
  "extra_attrs": {
    "剑武学增伤": 0.0852,
    "枪武学增伤": 0.0,
    "无名剑法蓄力技增伤": 0.32
  }
}
```

### 字段分类

| 分类 | 字段 | 单位 | 说明 |
|---|---|---|---|
| 外功 | `min_outer` / `max_outer` / `outer_pen` / `outer_bonus` | 点 / 点 / 点 / 比例 | 最小 / 最大外功攻击 / 穿透 / 增效 |
| 属攻（×6） | `min_mingjin` / `max_mingjin` / `mingjin_pen` / `mingjin_bonus` 等 | 同上 | 六大属性的攻击属性 |
| 判定 | `precision` / `crit_rate` / `intent_rate` / `direct_crit` / `direct_intent` | 比例 | 精准率 / 会心率 / 会意率 / 直接会心 / 直接会意 |
| 伤害 | `crit_dmg` / `intent_dmg` | 比例 | 会心伤害加成 / 会意伤害加成 |
| 增效 | `all_skill_bonus` / `boss_bonus` / `single_qs_bonus` / `group_qs_bonus` | 比例 | 全武学增效 / 首领增伤 / 单体奇术 / 群体奇术 |
| 动态词条 | `extra_attrs` | 比例 | 由别名解析确定的非固定字段（武学增效、指定技能增效等） |

### extra_attrs 说明

`extra_attrs` 中的键是**标准词条名**（非 Excel 简称），由别名解析阶段确定。这些字段不属于 `CombatAttributes` 的固定字段，而是通过 `attrs.extra_attrs` 字典访问。

典型的 `extra_attrs` 键：

- `{武器}武学增伤`：如 `剑武学增伤`、`枪武学增伤`（由流派武器决定）
- `{武功}蓄力技增伤`：指定技能增效的具体词条名（由流派分组决定，如 `无名剑法蓄力技增伤`）

## environment

环境配置——不参与编译，但影响计算上下文：

```json
{
  "food_bonus": {
    "min_outer": 0,
    "max_outer": 0
  },
  "fixed_damage_bonus": 0,
  "team_buffs": [
    { "name": "示例增益", "enabled": true }
  ],
  "monster": {
    "首领等级": 120
  },
  "combat_time": 109.1
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `food_bonus` | object | 食物加成（最小 / 最大外功攻击） |
| `fixed_damage_bonus` | number | 固定伤害加成 |
| `team_buffs` | array | 团队增益列表（名称 + 启用状态） |
| `monster` | object | 怪物属性（键值对，来自 Excel） |
| `combat_time` | number | 战斗时间（秒） |

## reference

基准参考值——用 Excel 公式模型（`FormulaModel`）直接求值的结果，用于验证编译后的节点程序正确性：

```json
{
  "combat_time": 109.1,
  "total_damage": 13154257.38,
  "dps": 120570.64,
  "graduation_rate": 1.0
}
```

`validate_model()` 会用 `ProgramRuntime` 以 `baseline_attrs` 为输入重新计算，结果必须与 `reference` 中的值一致（容差 `1e-6` 或 `1e-10` 相对误差）。

## program

编译后的节点程序：

```json
{
  "inputs": [
    { "kind": "field", "name": "min_outer" },
    { "kind": "field", "name": "max_outer" },
    { "kind": "affix", "name": "剑武学增伤" },
    ...
  ],
  "nodes": [
    ["const", 109.1],
    ["const", "鸣金"],
    ["input", 0],
    ["const", 200],
    ["add", 2, 3],
    ...
  ],
  "outputs": {
    "combat_time": 0,
    "total_damage": 2506,
    "dps": 2507,
    "graduation_rate": 2509
  }
}
```

### inputs

运行时输入规格列表，按顺序编号。每个输入有两种 kind：

| kind | 说明 | 数据来源 |
|---|---|---|
| `field` | `CombatAttributes` 的固定字段 | `getattr(attrs, name)` |
| `affix` | 动态词条 | `attrs.extra_attrs.get(name)` |

### nodes

节点数组，每个节点为 `[op, *依赖索引]`：

| 节点类型 | 格式 | 说明 |
|---|---|---|
| `const` | `["const", value]` | 常量（数值或字符串） |
| `input` | `["input", index]` | 运行时输入，`index` 指向 `inputs` 数组 |
| 运算 | `["add", left, right]` | 二元运算，`left` / `right` 是节点索引 |
| `if` | `["if", cond, when_true, when_false]` | 条件分支 |
| `iferror` | `["iferror", primary, fallback]` | 错误捕获 |

### outputs

命名输出到节点索引的映射。标准输出：

| 输出名 | 说明 |
|---|---|
| `combat_time` | 战斗时间（秒） |
| `total_damage` | 总伤害 |
| `dps` | 每秒伤害 |
| `graduation_rate` | 毕业率（0.0 ~ 1.0） |

## 百分比约定

所有百分比字段统一使用**小数比例**存储：

| Excel 显示 | JSON 存储 |
|---|---|
| `8.52%` | `0.0852` |
| `15%` | `0.15` |
| `100%` | `1.0` |

运行时不做 `/100` 转换——Excel 中的 `50%` 在词法阶段已被解析为 `0.5`。

## v1 → v2 迁移

v1 schema 包含完整的 `formula_language`、`inputs`（带 `type`/`unit`/`target`/`label_ref`）、`outputs`（带 `ref`）和 `sheets`（完整工作表数据）。v2 移除了冗余的公式结构，只保留编译后的 `program`。

迁移在 `graduation_converter.py` 的 `_compile_v2()` 中完成：读取 v1 工作簿模型 → 编译为节点程序 → 输出 v2 JSON。v1 格式的 JSON 不再被运行时支持。
