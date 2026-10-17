# 别名解析规则

Excel 工作簿中使用简称（如"全武增""首领增""蓄力技定音"）引用装备词条。别名解析的任务是将这些简称映射为 `game_config.yaml` 中的标准词条名。这是一个多步约束求解过程，任何一步失败都会阻断导入。

## 输入与输出

**输入**：

- Excel 工作簿（`期望` 工作表第 15 行起到「固伤加成」行之前的 A 列简称）
- `game_config.yaml` 中的配置：`affix_caps`、`affix_aliases`、`weapon_types`、`schools`

**输出**：

```python
{
    "all_skill_bonus": ["全武学增效"],      # 标准词条名列表
    "boss_bonus": ["对首领单位增伤"],
    "weapon_bonus_primary": ["剑武学增伤"],
    "weapon_bonus_secondary": ["枪武学增伤"],
    "single_qs_bonus": ["单体类奇术增伤"],  # 两行奇术布局才有
    "group_qs_bonus": ["群体类奇术增伤"],   # 两行奇术布局才有
    "all_qs_bonus": ["全奇术增伤"],         # 合并布局才有，与上面两行互斥
    "special_bonus": ["无名剑法蓄力技增伤"],
}
```

## 约束求解流程

### 第 1 步：读取 Excel 简称

`期望` 页第 2~14 行是各流派完全一致的部位属性，位置固定。第 15 行起到
「固伤加成」行之前是**装备词条区**，行数随表格版本变化：115 起游戏把单体类、
群体类奇术增伤合并成全奇术增伤，一部分流派因此少一行，定音从 B21 上移到 B20；
另一部分流派仍保留两行奇术、定音仍在 B21（破竹·风另有 B22 的老鼠定音）。

所以词条区**不能按行号读取**，每一行的语义由 A 列简称反查到的词条决定：

| 简称反查到的词条 | 输入名 |
|---|---|
| `全部武学增效` 类别 | `all_skill_bonus` |
| `对单位增效` 类别 | `boss_bonus` |
| 流派主 / 副武器的 `wuxue_affix` | `weapon_bonus_primary` / `weapon_bonus_secondary`（按出现顺序） |
| `单体类奇术增伤` | `single_qs_bonus` |
| `群体类奇术增伤` | `group_qs_bonus` |
| `全奇术增伤` | `all_qs_bonus` |
| 流派「指定技能增效」分组内的词条 | `special_bonus`、`special_bonus_2`…（按出现顺序） |

词条名到输入名的对应关系复用 `combat_attrs.AFFIX_TO_ATTR`，本模块不再另写一份
词条名单。空行不参与解析；无法归入任何一类的简称会阻断导入，不会被静默跳过。

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

| 输入名 | 允许的词条 |
|---|---|
| `all_skill_bonus` | `全部武学增效` 类别（`AFFIX_TO_ATTR` 中映射到该字段的词条） |
| `boss_bonus` | `对单位增效` 类别 |
| `weapon_bonus_primary` | 流派主 / 副武器的 `wuxue_affix` |
| `weapon_bonus_secondary` | 流派主 / 副武器的 `wuxue_affix` |
| `single_qs_bonus` | `单体类奇术增伤` |
| `group_qs_bonus` | `群体类奇术增伤` |
| `all_qs_bonus` | `全奇术增伤` |

奇术三路各自精确到具体词条，不共用整个 `奇术类增伤` 类别：115 的合并布局只能
出现 `all_qs_bonus`，两行布局只能出现 `single_qs_bonus` + `group_qs_bonus`，
两者同时出现说明表格或配置被改坏了。

过滤后，每个输入名必须**唯一映射**到一个标准词条名。同一简称对应多个流派的同类
词条是正常情况（如「蓄力技定音」同时挂在四个流派上），只要它们落在同一类输入里。

### 第 4 步：指定技能增效特殊路径

`special_bonus` 不走通用类别过滤，而是走**流派分组**路径：

1. 从 `affix_caps.指定技能增效._aliases` 中取出当前流派的分组（如 `"鸣金·虹": ["无名剑法武学技增伤", "无名剑法蓄力技增伤", …]`）
2. 用 Excel 简称在 `affix_aliases` 中反查
3. 结果必须落在该流派的分组中，按配置顺序取第一个

这确保了不同流派的"蓄力技定音"简称被解析到各自流派对应的词条。一个流派有多个
定音行时（破竹·风、裂石·钧），首个占用 `special_bonus`，后续依次编号为
`special_bonus_2`、`special_bonus_3`。

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
| 简称在 alias_index 中找不到 | `RuntimeError` | `Excel 期望页第 {row} 行的输入简称 {label!r} 不在词组别名配置中` |
| 简称跨了多类输入 | `RuntimeError` | `应唯一映射到一类毕业率输入，实际匹配 …` |
| 同一类输入出现两行 | `RuntimeError` | `与 B{row} 同属 {input_name} 输入，无法确定应以哪一行为准` |
| 简称为空（非 special_bonus） | `RuntimeError` | `{school} Excel 输入 {input_name} 的字段名为空` |
| 匹配结果不在允许类别中 | `RuntimeError` | `Excel 输入 {input_name}（简称 {label!r}）应唯一映射到一个精准词条，实际匹配 …` |
| special_bonus 流派分组中找不到 | `RuntimeError` | `无法解析 Excel 输入 {input_name} 的指定技能增效简称` |
| affix_caps 缺少流派分组 | 配置不完整 | `没有流派「{school}」的指定技能增效分组` |
| affix_aliases 引用未知词条 | 配置错误 | `affix_aliases uses unknown affix` |

每条报错都带**输入名**（或行号）。只给简称会让用户以为词组配置缺别名，实际可能
只是某一行读错了位置——115 的奇术合并正是这样被误判成「配置没配」。

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
