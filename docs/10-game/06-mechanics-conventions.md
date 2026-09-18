# 游戏机制约定（代码内写死项登记）

本文登记**写死在代码里、不来自 `config/`、且属于游戏规则事实**的约定。它们今天不做配置化
（改动成本大于收益），但游戏版本更新时随时可能失效，所以必须有一处能查到"哪条规则在哪
个文件、为什么是这个值"。

登记原则：

- 只收"游戏规则事实"和"补两种口径差异的经验值"。纯工程常量（目录名、错误码、DSL 关键字）不登记。
- 每条写清 **值 / 位置 / 出处或依据 / 变化时要动哪里**。
- 新增一条写死的游戏规则时，先在这里登记，再写代码；改动已登记条目时同步更新本表。
- 已经进配置的规则（等级抗性 `judge_resistance` / `buff_resistance`、词条等级上限与承音上限、
  转律词条库、赛季时间线、等级能力开关）**不在**本表，以 `config/system/yysls/` 为准。

档位含义：**A** 游戏数值规则（随版本变的概率最高）；**B** 部位 / 词条身份约定（游戏结构性事实）；
**C** 容差与经验阈值（补两种口径差，不是游戏规则本身）。

---

## A. 游戏数值规则

### A1. 五维词条 → 战斗属性转换系数

| 项 | 值 |
|---|---|
| 位置 | `core/combat/combat_attrs.py` 顶部 `JIN_TO_MIN_OUTER` 等六个常量；`convert_five_dims()` 直接使用；`core/attr_model/builtin.py` 把同一组常量包装成默认公式 |
| 值 | 1 劲 → 0.225 最小外功 + 1.36 最大外功；1 势 → 0.9 最大外功 + 0.038% 会意率；1 敏 → 0.9 最小外功 + 0.076% 会心率；体/御不换算 |
| 依据 | 自行拟合。校验不变量：一条满值五维词条产出的各项按各自词条满值归一后相加应为 1（110 级：劲 1.003、势 0.986、敏 0.986）。见 `tests/yysls/test_combat_attrs.py` 归一测试 |
| 历史 | 早期拟合值（劲 0.246/1.315、敏 1.0/0.075%）归一后敏达 1.044，经反推路径进入基础属性，使五维词条边际收益被高估约 11%，已修正 |
| 注意 | `attr_model` 子系统另有配置化的面板公式（`base.yaml` 里 `formula: {source: dim_min, multiplier: 0.2639, max: 73.9}`），那是**角色面板五维 → 面板属性**的口径，与本条**装备词条五维 → 战斗属性**不是一套；两边系数不同是有意的 |
| 变化时 | 改六个常量并重跑归一测试；`builtin.py` 自动跟随 |

### A2. 弓玦套装属性

| 项 | 值 |
|---|---|
| 位置 | `core/combat/combat_attrs.py` `compute_gongjue_attrs()`；调用入口统一在 `core/graduation/context.py` `gongjue_attrs()` |
| 值 | 弓玦属性 = 对应三率词条在**当前赛季装备等级**的普通上限 ÷ 2。类型映射写死：会意 → 会意率 / `intent_rate`，会心 → 会心率 / `crit_rate`，精准 → 精准率 / `precision` |
| 依据 | 游戏内弓玦面板数值实测 |
| 变化时 | 新弓玦类型改两个映射 dict；比例改 `half_cap` 一行。赛季等级为 0（未配置赛季）时弓玦属性为零，见 0.12.2 发布说明 |

### A3. 三率抗性的基准与上限

| 项 | 值 |
|---|---|
| 位置 | `core/combat/combat_attrs.py` `PRECISION_BASE = 65.0`、`CRIT_RATE_CAP = 0.80`、`INTENT_RATE_CAP = 0.40`；`core/graduation/optimal_combo.py` 向量路径再引用一次 |
| 值 | 精准率：生效 = 65% + (面板 − 65%) ÷ 除数；会心率：生效 = 面板 ÷ 除数，上限 80%；会意率：生效 = 面板 ÷ 除数，上限 40% |
| 依据 | 游戏判定属性面板说明 |
| 注意 | 除数本身 `1 + 抗性/100` 的抗性值已进等级配置（`judge_resistance`），但基准 65 和两个上限**没有跟着进配置**，仍按 110 级写死 |
| 变化时 | 改三个常量；`optimal_combo.py` 的向量内环通过 import 跟随 |

### A4. 抗性缺省值 145 / 15

| 项 | 值 |
|---|---|
| 位置 | `core/combat/combat_attrs.py` `THREE_RATE_RESISTANCE = 145`、`BONUS_RESISTANCE = 15`；作为 `apply_three_rate_resistance` / `apply_bonus_resistance` / `apply_penetration_resistance` 的默认参数 |
| 值 | 三率抗性 145（除数 2.45）、增效抗性 15（除数 1.15） |
| 依据 | 110 级数值 |
| 注意 | 正式链路（`build_graduation_attrs`、面板展示）都从等级配置传入抗性，默认参数只在等级配置缺失或直接裸调时生效；`calculate_judgment_outcomes` 由调用方传入 |
| 变化时 | 优先改 `levels_and_seasons.yaml`；常量只是兜底 |

### A5. 词条 min / max 拆分比 1:2

| 项 | 值 |
|---|---|
| 位置 | `core/attr_model/models.py` `DEFAULT_AFFIX_SPLIT = (1, 2)` |
| 值 | 心法给出的一整条外功/属攻词条按 1:2 拆成最小/最大，两者之和等于该等级该词条满值 |
| 依据 | 96 级（25.9 / 51.9，和 77.8）与 110 级（40.5 / 80.9，和 121.4）两个独立数据点 |
| 变化时 | 单条不符合的在 `attr_model/*.yaml` 里显式写 `split` 覆盖；默认值改常量 |

### A6. 调律词条组合铁律

| 项 | 值 |
|---|---|
| 位置 | `core/equip_validator.py` `_validate_slots()`；类别名在 `_categories()` |
| 值 | ① 词条 2–5 互不重复，但允许与首词条同名（首词条是装备自带，不由调律产出）；② 归属为 **属攻类** 的词条最多 2 条；③ 神力词条最多 1 条，且转律不产神力（`transferred_divine`）；④ 神力 = 归属为 **增效类** 或 **武器类** 的词条 |
| 依据 | 游戏调律产出规律，长期实测未见反例 |
| 注意 | "属攻类 / 增效类 / 武器类"是**词组配置里的归属名字符串**，代码按字面匹配。改配置里的归属名会让规则静默失效 |
| 变化时 | 数量上限改 `_validate_slots` 里的两个比较；类别集合改 `_categories()` |

### A7. 可转律槽位

| 项 | 值 |
|---|---|
| 位置 | `core/loadout/transmute.py` `TRANSMUTABLE_INDICES = (2, 3, 4, 5)`；`core/loadout/development_rules.py` `FIRST_AFFIX_INDEX = 1` / `AFFIX_INDEXES` |
| 值 | 转律只能改商角徵羽（第 2–5 条），宫（第 1 条）永远不动；已转律的装备再次转律只能改同一槽 |
| 依据 | 游戏转律界面 |
| 变化时 | 改两个常量；`rule_judge._eval_partial` 的转出候选按 `affixes[1:]` 切片，需同步 |

### A8. 装备固定 5 条词条

| 项 | 值 |
|---|---|
| 位置 | `range(1, 6)` / `affix_1 ~ affix_5` 散布在 `equip_validator.py`、`affix_cap.py`、`loadout/chengyin_merge.py`、`loadout/repository.py`、`equip_parser/models.py`、`equip_parser/parser.py` |
| 值 | 每件装备最多 5 条普通词条 + 1 条定音 |
| 变化时 | 至少 6 处；建议届时先收成一个常量再改 |

### A9. 赛季切换时刻

| 项 | 值 |
|---|---|
| 位置 | `config/manager.py` `SEASON_SWITCH_HOUR = 5` |
| 值 | 赛季在早上 05:00 切换；`season_at()` 的生效区间为 `[start_date 05:00, end_date 05:00)`，上下半赛季同理 |
| 依据 | 游戏公告 |
| 文档 | 0.12.2 发布说明"不兼容变动 2" |

---

## B. 部位 / 词条身份约定

### B1. 调律规则的部位归并

| 项 | 值 |
|---|---|
| 位置 | `core/tuning_rules/models.py` `PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}`（15 处引用）、`PART_KEYS`、`QUALITY_PARTS`；`core/evaluator/rule_judge.py` `_build_attempts()` 另写死 `part in ("环", "冠胄", "胫甲")` 才展开玩法 |
| 值 | 调律规则按 5 个模式部位定义：主武器 / 副武器 / 环 / 冠胄 / 胫甲；佩用环的规则、胸甲用冠胄的、腕甲用胫甲的 |
| 依据 | 游戏里这三对部位的词条池与判定完全相同 |
| 注意 | `rule_judge.py` 那处没走 `PART_ALIAS`，是第二份副本 |
| 变化时 | 改 `PART_ALIAS` 与 `PART_KEYS`，并同步 `rule_judge._build_attempts` |

### B2. 装备部位 / 槽位集合

| 项 | 值 |
|---|---|
| 位置 | `core/equip_parser/constants.py` `WEAPON_SLOTS`、`ARMOR_SLOTS`、`JEWELRY_TYPES_SET = {"环", "佩"}`、`ARMOR_TYPES_SET = {"冠胄", "胸甲", "胫甲", "腕甲"}`；`core/loadout/models.py` `EQUIPMENT_SLOTS` |
| 值 | 8 个槽位：主武器 / 副武器 / 环 / 佩 / 冠胄 / 胸甲 / 胫甲 / 腕甲 |
| 注意 | 与 UI 层的槽位表是同一事实的多份副本（见重构清单 B1） |

### B3. 规则 DSL 的动态词条与保留名

| 项 | 值 |
|---|---|
| 位置 | `core/tuning_rules/models.py` `DYNAMIC_AFFIXES = ("最大本属攻击", "最小本属攻击", "最大外属攻击", "最小外属攻击")`、`GENERIC_ATTR = "通用"`、`DYNAMIC_CATEGORY = "动态类"`、`ATTR_ATTACK_CATEGORY = "属性攻击"` |
| 值 | 动态词条是规则层词汇，不是配置里的真实词条；判定时按玩法属性把具体属攻归类为本属/外属。"通用"属性的玩法不做动态归类 |
| 注意 | `"通用"` 必须与 `affixes.yaml` 里属性攻击词组 `_aliases` 的分组名一致；`"属性攻击"` 必须与词组名一致。`rule_judge` 对动态词条特判"仅非武器部位可作填充/转入候选"，因为它们不在配置部位表里 |

### B4. 按类别名决定的行为

| 类别名 | 位置 | 行为 |
|---|---|---|
| `"指定技能增效"` | `core/combat/combat_attrs.py` `map_affix_to_attr()`、`is_bonus_percent_field()` | 该词组的词条进 `extra_attrs` 并受增效抗性 |
| `"外功增益"`、`"属攻增益"`、`"指定技能增效"` | `core/equip_parser/dingyin_parser.py` `_DINGYIN_CATEGORIES` | 定音只从这三个词组里解析；解析不出的按止戈定音处理 |
| `"全部武学增效"`、`"对单位增效"`、`"奇术类增伤"` | `core/graduation/graduation_converter.py` | 毕业率计算器 Excel 导入时的三个固定输入项 |
| `"属攻类"`、`"增效类"`、`"武器类"` | `core/equip_validator.py` `_categories()` | 见 A6 |

改配置里任一词组名而不改这里，对应行为静默消失。

### B5. `"武器"` 作为部位显示名

| 项 | 值 |
|---|---|
| 位置 | `core/equip_validator.py`、`core/evaluator/base.py`、`core/evaluator/rule_judge.py`（两处） |
| 值 | `"武器"` 是 `infer_part()` 输出的部位显示名，不是装备类型；装备类型是剑/枪/扇…。四处按 `== "武器"` 判断 |

### B6. 游戏文本关键字

| 关键字 | 位置 | 用途 |
|---|---|---|
| `"承音"`、`"N阶"` | `core/equip_parser/parser.py` `_parse_level()` | 从装备等级文本识别等级与承音 |
| 等阶名称（装备名前缀） | `config/manager.py` `infer_original_equipment_level()`，名称表在 `levels_and_seasons.yaml` | 承音后原始等级只按名称前缀识别；名称表在配置，**匹配规则**（最长前缀）在代码 |
| `"属性攻击"`、`"外功穿透"`、`"属攻穿透"`、`"属攻伤害加成"`、`"无相"` | `core/role_attr_parser/parser.py` | 角色面板 OCR 标签 |
| 属攻穿透详情页的分项（鸣金/裂石/破竹/牵丝穿透）**不含**装备定音的无相穿透；无相穿透在该页单独一行，且当前版本没有流派穿透装备词条 | `core/role_attr_parser/parser.py` `parse_detail2_attr_pen`（跳过无相行）、`ui/loadout/combat/play_style_dialog.py`（穿透按基础值直接保存） | 反推基础属性时穿透不扣装备的前提。若游戏把无相并入分项显示，这里必须改成减法 |
| `"战斗时间"` | `core/graduation/rotation.py` | 技能轴 Excel 的行标签 |

游戏 UI 文案一改即断，且没有配置层可以热修。

---

## C. 容差与经验阈值

这些不是游戏规则，是补"两种口径落库精度不同"的差；配置里的承音上限改为原值之后，前两条只剩 OCR 半步量化一个用途。

| # | 值 | 位置 | 补的是什么差 |
|---|---|---|---|
| C1 | `_CAP_OVERFLOW_TOL = 0.05 + 1e-6` | `core/equip_validator.py` | 游戏显示 1 位小数、OCR 天然带半步量化误差；正好顶满上限的词条不应报超上限 |
| C2 | `_VALUE_TOL = 0.1 / 2 + 1e-6` | `core/loadout/chengyin_merge.py` | 同一真值以 OCR（114.1）与历史手填派生值（114.12）两种口径落库；喂养的真实提升至少 0.1 才在游戏里可见 |
| C3 | 前景 ≥ 50、红 ≥ 50、红边距 10、前景占比 ≥ 0.10、锁定红占比 ≥ 0.10、未锁 ≤ 0.02 | `core/equip_parser/lock_state.py` | 锁定角标的颜色识别阈值 |
| C4 | 单方案时间预算 5 s | `core/graduation/smart_tuning.py` `_MAX_PLAN_SECONDS` | 智能调律的交互响应上限 |
| C5 | 毕业率精度只允许 `0.01 / 0.001 / 0.0001` | `core/tuning_rules/parsing.py` | `smart_tuning.evaluation.precision` 的取值白名单 |

---

## 维护

- 新增写死的游戏规则：先登记再写代码，PR 描述里引用条目编号。
- 决定把某条配置化时：从本表删除，并在对应配置文档（`60-userguide/04.05-game-config.md`）里补字段说明。
- 本表由 2026-09-18 的代码审计整理，对应提交 `9e5f9d1f` 之后的 `dev`。
- 引用本表的文档：[01-equipment-system.md](01-equipment-system.md)、[03-damage-mechanics.md](03-damage-mechanics.md)、[04-tuning-mechanics.md](04-tuning-mechanics.md)、[../30-architecture/08-base-attr-model.md](../30-architecture/08-base-attr-model.md)、[../30-architecture/36-graduation/08-scoring-layer.md](../30-architecture/36-graduation/08-scoring-layer.md)、[../60-userguide/04.05-game-config.md](../60-userguide/04.05-game-config.md)。
