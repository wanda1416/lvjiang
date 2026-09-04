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

同一份来源清单求值两次，各自成一个 `ScopeResult`（属性值 + 产生它的
明细绑在同一个对象上）：

- `result.panel` 只含 `scope: panel` 的贡献，用于和游戏角色面板对账；
- `result.combat` 含全部贡献，作为毕业率的 `base_attrs`。

差集就是吃食一类只在战斗内生效、不进角色面板的加成。

值和明细绑在一起，是为了让「显示 A 的值、却按 B 的明细拆分」在结构上
就写不出来——它们分开放的时候，界面显示面板值而 breakdown 汇总了含
吃食的战斗贡献，两栏加不到一起。

## 未建模条目与反解

未填数值的条目标 `modeled: false`，贡献 0 并登记进 `unmodeled`；
`solve_residual()` 反解出一份「手填补足」，使 `panel_attrs` 等于实测
面板。已建模来源走推导、未建模部分由补足兜底，总量始终正确——
所以来源只建了一半也是可用状态，不必等全部填完。

补足是逐轮修正而非一次相减：公式来源会让改一个字段影响另一个字段。

## 校验

面板对不上时靠 breakdown 定位，而不是只知道总数不对：

- `ScopeResult.modifiers_for(field)` —— 该字段收到的每一次贡献
  （来源、前值、后值）；
- `ScopeResult.contribution_by_kind(field)` —— 按来源类别汇总；
- `extra_attrs` 的动态属性（指定武学增效、技能增伤等）同样记明细、
  同样进 breakdown 与差异比对，`AppliedModifier.is_extra` 标出来；
- `diff_against_panel(result, panel)` —— 与实测面板逐字段比对，
  只列出不一致的。

实测面板由 `core/role_attr_parser` 从角色详情页 OCR 产出，字段与
`COMBAT_ATTR_FIELDS` 对齐，配合 `scan_role_base_attr.wf` 可全自动。

离线靶子是 11 份毕业率方案 JSON 的 `baseline_attrs`——11 个流派即
11 组独立配置，不进游戏即可回归。

## 五维转换是内建的

转换系数不进 YAML：装备词条上的五维已由 `combat_attrs.convert_five_dims`
换算，系数写在那里。`builtin.dimension_effects()` 引用同一组常数，避免
两处各存一份导致改一处漏一处——这正是那组系数曾错了一年没被发现的
原因。YAML 只负责声明角色**有多少**劲/势/敏。

五维转换不受用户选择影响：选的是上哪几门心法，不是要不要让敏转成
外功攻击，所以恒参与求值。

## 界面

「游戏配置 → 属性来源」。补数据是这块的真正瓶颈（心法 37 门 × 6 重
就是 222 行），所以界面按「一组一屏」组织，左侧选心法/武学，右侧只
显示这一组的几行，每行两次点击填完：

| 取值方式 | 用途 |
|---|---|
| 一整条词条 | 选词条类别即可，数值由 affix_caps 生成 |
| 无贡献 | 心法六重里大量是触发类效果，确认后计入进度 |
| 自定义数值 | 单个字段 + 数值 |
| 高级 | 一条给多个属性或需要公式的，直接编辑该条目 YAML |

条目 id 形如「易水歌·二重」，按「·」前半分组。`scripts/gen_attr_model_entries.py`
只增不改地补齐骨架，游戏出新心法时重跑即可，已填的数值不会被覆盖。

## 推导与落地

面板左下「推导基础属性…」打开推导对话框：选流派与等级、勾选参与
推导的来源，得到逐字段的推导值、与所选基础属性的差异，以及**按来源
拆分的贡献**——面板对不上时靠这一列定位到具体来源，而不是只知道
总数不对。

「存为基础属性」把结果写进现有的基础属性存储，毕业率链路照旧读它，
不需要为此改动任何既有计算。存的是 `combat_attrs`（全集），因为
毕业率算的是战斗内表现而不是角色面板。

## 现状

引擎、界面、推导链路已就位，**只差填数值**。当前骨架：

- 心法 222 条（37 门 × 6 重），来自社区心法表；国际服尚未放出的
  五个流派（裂石·钧 / 牵丝·翊 / 破竹·尘 / 破竹·鸢 / 破竹·樽）需要
  在界面里自行新增；
- 武学 22 条，取自 `game_config.yaml` 的 `martial_arts`；
- 其余来源（等级底子 / 突破 / 套装 / 武备 / 神工 / 奇物 / 秘籍 /
  吃食）为空，按需新增。

填数值的顺序建议：突破 → 心法 → 武学 → 套装 / 武备 / 神工 / 奇物。
