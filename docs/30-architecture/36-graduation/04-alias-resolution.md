# 别名解析规则

Excel 工作簿中使用简称（如"全武增""首领增""蓄力技定音"）引用装备词条。别名解析的任务是将这些简称映射为 `game_config.yaml` 中的标准词条名。这是一个多步约束求解过程，任何一步失败都会阻断导入。

## 输入与输出

**输入**：

- Excel 工作簿（`期望` 工作表第 15~21 行的 A 列简称）
- `game_config.yaml` 中的配置：`affix_caps`、`affix_aliases`、`weapon_types`、`schools`

**输出**：

```python
{
    "all_skill_bonus": ["剑武学增伤"],      # 标准词条名列表
    "boss_bonus": ["对首领单位增伤"],
    "weapon_bonus_primary": ["剑武学增伤"],
    "weapon_bonus_secondary": ["枪武学增伤"],
    "single_qs_bonus": ["奇术类增伤"],
    "group_qs_bonus": [],
    "special_bonus": ["无名剑法蓄力技增伤"],
}
```

## 约束求解流程

### 第 1 步：读取 Excel 简称

从 `期望` 工作表的 A15~A21 单元格读取各输入行的字段简称：

| 输入名 | Excel 位置 | 简称示例 |
|---|---|---|
| `all_skill_bonus` | A15 | `全武增` |
| `boss_bonus` | A16 | `首领增` |
| `weapon_bonus_primary` | A17 | `弓武增` |
| `weapon_bonus_secondary` | A18 | `枪武增` |
| `single_qs_bonus` | A19 | `蓄力技定音` |
| `group_qs_bonus` | A20 | `群体定音` |
| `special_bonus` | A21 | `牵丝蓄力技增伤` |

若简称为空：

- `special_bonus` 留空是合法的（部分流派没有指定技能增效）
- 其他字段留空会抛出 `RuntimeError`

### 第 2 步：构建别名索引

从 `game_config.yaml` 构建两个索引：

**alias_index**：别名 → 标准词条名列表（反向映射）

```yaml
# game_config.yaml 中的 affix_aliases 结构
affix_aliases:
  全武学增效:
    - 全武增
    - 全武学增效
    - 全武学效果
  对首领单位增伤:
    - 首领增
    - 对首领增伤
    - 对单位增效
```

经过反向映射后：`"全武增" → ["全武学增效"]`，`"首领增" → ["对首领单位增伤"]`

**canonical_names**：所有在 `affix_caps` 中出现过的标准词条名集合

### 第 3 步：按类别过滤

不同输入行对应不同的词条类别，候选词条必须同时满足：

1. 在 `alias_index` 中能匹配到该简称
2. 匹配到的标准词条名属于该输入行对应的类别

| 输入名 | 允许的类别 |
|---|---|
| `all_skill_bonus` | `全部武学增效` |
| `boss_bonus` | `对单位增效` |
| `weapon_bonus_primary` | 流派主 / 副武器的 `wuxue_affix` |
| `weapon_bonus_secondary` | 流派主 / 副武器的 `wuxue_affix` |
| `single_qs_bonus` | `奇术类增伤` |
| `group_qs_bonus` | `奇术类增伤` |

过滤后，每个输入名必须**唯一映射**到一个标准词条名。

### 第 4 步：指定技能增效特殊路径

`special_bonus` 不走通用类别过滤，而是走**流派分组**路径：

1. 从 `affix_caps.指定技能增效._aliases` 中取出当前流派的分组（如 `"鸣金·虹": ["鸣金蓄力技增伤", "鸣金虹蓄力技定音"]`）
2. 用 Excel 简称在 `affix_aliases` 中反查
3. 结果必须落在该流派的分组中

这确保了不同流派的"蓄力技增伤"简称被解析到各自流派对应的词条。

### 第 5 步：武学增效特殊路径

`weapon_bonus_primary` 和 `weapon_bonus_secondary` 的候选集合由流派的武器绑定决定：

```yaml
# game_config.yaml
schools:
  鸣金·虹:
    main:
      weapon: 剑
      martial_art: 无名剑法
    sub:
      weapon: 枪
      martial_art: 无名枪法
```

通过 `weapon_types` 查找武器的 `wuxue_affix`：

```yaml
weapon_types:
  - name: 剑
    wuxue_affix: 剑武学增伤
  - name: 枪
    wuxue_affix: 枪武学增伤
```

因此鸣金·虹的主武器增效映射到 `剑武学增伤`，副武器增效映射到 `枪武学增伤`。

## 错误处理

| 场景 | 错误类型 | 报错信息 |
|---|---|---|
| 简称为空（非 special_bonus） | `RuntimeError` | `{school} Excel 输入 {input_name} 的字段名为空` |
| 简称在 alias_index 中找不到 | 匹配结果为空 | 匹配 0 个 → 报错 |
| 简称匹配到多个候选 | 匹配结果不唯一 | `实际匹配 {matches!r}` |
| 匹配结果不在允许类别中 | 过滤后为空 | 同上 |
| special_bonus 流派分组中找不到 | 匹配结果为空 | `无法解析 Excel 指定技能增效简称` |
| affix_caps 缺少流派分组 | 配置不完整 | `没有流派「{school}」的指定技能增效分组` |
| affix_aliases 引用未知词条 | 配置错误 | `affix_aliases uses unknown affix` |

**所有错误都会阻断导入**，不会静默降级。

## 配置依赖链

```
affix_caps（类别定义 + 流派分组）
    ↓
affix_aliases（别名 → 标准词条名映射）
    ↓
weapon_types（武器 → 武学增效词条映射）
    ↓
schools（流派 → 主/副武器绑定）
```

任何一层配置缺失或错误都会导致别名解析失败。修改配置后需要重新导入 Excel 生成 JSON。

## 运行时行为

别名解析只在**导入阶段**执行。运行时（`GenericCalculator.calculate()`）只使用 JSON 中已确定的标准词条名（`program.inputs[].name`），不进行任何别名推断或包含匹配。
