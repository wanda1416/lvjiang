# 基础属性来源模型

回答「装备之外的战斗属性从哪来」。在此之前 `build_graduation_attrs()`
的 `base_attrs` 是个黑盒——要么来自毕业率 Excel 的 `baseline_attrs`，
要么用户手填，工具本身不知道其中哪一部分来自心法、武学还是套装。
本模块把这部分显式建模。

代码在 `apps/yysls/core/attr_model/`，配置在
`config/system/yysls/attr_model/`。装备词条依旧走原有的
`equipment_attrs` 通道，既有模块零改动。

## 数据流

```text
config/system/yysls/attr_model/*.yaml   一类来源一个文件
  → parsing            schema 校验（从严，未知字段即失败）
  → StatEffect[]       条目化的来源贡献
  → resolver           两趟求值 + breakdown
  → panel_attrs / combat_attrs
       ↘ build_graduation_attrs(base_attrs=combat_attrs)
```

## 来源类别

`models.SOURCE_KINDS` 声明序即 breakdown 展示序：

| kind | 含义 |
|---|---|
| `base` | 角色等级底子 |
| `breakthrough` | 突破 |
| `dimension` | 五维 → 战斗属性转换 |
| `martial_art` | 武学天赋 |
| `inner_way` | 心法 |
| `gear_set` | 套装（含弓玦） |
| `arsenal` | 武备 |
| `divinecraft` | 神工 |
| `oddity` | 奇物 |
| `script` | 秘籍 |
| `food` | 吃食 |

## 取值三形态

```yaml
kind: inner_way
entries:
  易水歌·二重:
    full_affix: 外功攻击          # 一整条词条
  易水歌·五重:
    stats: { direct_crit: 0.046 } # 常数
  某武学·外功增幅:
    stats:
      min_outer:                  # 公式
        formula: { source: dim_min, multiplier: 0.2639, max: 73.9 }
```

`full_affix` 的数值不写在配置里，由 `game_config.yaml` 的
`affix_caps[等级]` 生成。游戏事实：心法给出的一整条词条按 **1 : 2**
拆成最小 / 最大，两者之和等于该等级该词条满值。已在两个独立数据点
验证：

| 等级 | 拆出的 min / max | 和 | affix_caps |
|---|---|---|---|
| 110 | 40.5 / 80.9 | 121.4 | 外功攻击 **121.4** |
| 96 | 25.9 / 51.9 | 77.8 | 外功攻击 **77.8** |

所以换赛季只改 `affix_caps` 一处，几十个心法条目自动跟上，不必重填。
不符合该规律的条目写 `split` 覆盖比例，或直接写 `stats` 常数。

属性攻击是词组，`full_affix: 属性攻击` 按当前流派解析到对应的属攻
字段（鸣金 / 牵丝 / 裂石 / 破竹 / 通用即无相）。

## 两趟求值

先把全部常数与整条词条加完，再算公式。公式因此总能读到源字段的
最终值，结果与 YAML 里来源的书写顺序无关——否则「敏 → 外功攻击」
这类武学天赋会因为恰好排在五维来源之前而只读到一半的敏。

五维（`dim_jin` / `dim_shi` / `dim_min` / `dim_ti` / `dim_yu`）是求值
的工作字段，公式可以引用，但不属于 `CombatAttributes`，投影时丢弃。

## 双出口

同一份来源清单求值两次：

- `panel_attrs` 只含 `scope: panel` 的贡献，用于和游戏角色面板对账；
- `combat_attrs` 含全部贡献，作为毕业率的 `base_attrs`。

差集就是吃食一类只在战斗内生效、不进角色面板的加成。

## 未建模条目与反解

未填数值的条目标 `modeled: false`，贡献 0 并登记进 `unmodeled`；
`solve_residual()` 反解出一份「手填补足」，使 `panel_attrs` 等于实测
面板。已建模来源走推导、未建模部分由补足兜底，总量始终正确——
所以来源只建了一半也是可用状态，不必等全部填完。

补足是逐轮修正而非一次相减：公式来源会让改一个字段影响另一个字段。

## 校验

面板对不上时靠 breakdown 定位，而不是只知道总数不对：

- `ResolveResult.modifiers_for(field)` —— 该字段收到的每一次贡献
  （来源、前值、后值）；
- `ResolveResult.contribution_by_kind(field)` —— 按来源类别汇总；
- `diff_against_panel(result, panel)` —— 与实测面板逐字段比对，
  只列出不一致的。

实测面板由 `core/role_attr_parser` 从角色详情页 OCR 产出，字段与
`COMBAT_ATTR_FIELDS` 对齐，配合 `scan_role_base_attr.wf` 可全自动。

离线靶子是 11 份毕业率方案 JSON 的 `baseline_attrs`——11 个流派即
11 组独立配置，不进游戏即可回归。

## 现状

骨架已就位，来源配置基本为空（`inner_way.yaml` 里 `易水歌·二重`
是已验证的模板）。填数值的顺序建议：五维转换 → 突破 → 心法 →
武学 → 套装 / 武备 / 神工 / 奇物。
