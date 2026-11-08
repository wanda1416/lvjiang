# 06 装备与定音

来源前缀：`system.gearSlot.*`、`system.gearRarity.*`、
`system.gearSet.*`、`data.gearSet.*`、`data.armorSet.*`、
`data.arsenal.*`、`data.bowRingSet.*`、`system.setPieces.*`、
`system.attunement.*`。

## 装备部位（`system.gearSlot.*`）

| 中文（繁中原文） | English |
|---|---|
| 冠冑 | Helmet |
| 胸甲 | Chestpiece |
| 腕甲 | Bracer |
| 脛甲 | Greaves |
| 環 | Disc |
| 佩 | Pendant |
| 左武 | Left Weapon |
| 右武 | Right Weapon |

## 品阶与件数

| 中文 | English | key |
|---|---|---|
| 金 | Gold | `system.gearRarity.gold` |
| 紫 | Purple | `system.gearRarity.purple` |
| 0/2/4 件 | 0/2/4 pieces | `system.setPieces.0/2/4` |

## 套装（gearSet / armorSet）

| 中文（繁中原文） | English | key |
|---|---|---|
| 斷嶽 | Cleftpeak | `data.gearSet.Cleftpeak` |
| 浣花 | Ivorybloom | `data.gearSet.Ivorybloom` |
| 時雨 | Rain Whisper | `data.gearSet.RainWhisper` |
| 江凝 | Calmwaters | `data.armorSet.Calmwaters` |
| 易相 | Formbend | `data.armorSet.Formbend` |
| 撼天（gearSet） | Etherwrath | `system.gearSet.etherwrath` |
| 飛隼（gearSet） | Hawkwing | `system.gearSet.hawkwing` |
| 通用 | General | `data.arsenal.General` |

> 国服另有 玉斗套、连星套、抱玉套 等（见 `docs/10-game/02-school-system.md`），
> 国际服数据未收录，英文名待补。

## 弓玦（`data.bowRingSet.*`）

| 中文（繁中原文） | English |
|---|---|
| 無 | None |
| 精準 | Precision |

## 定音词条（`system.attunement.*`）

命名规律：`<武学>・<技能类型>增傷/增療`。通用词条在外，流派词条按流派
分组（与 `docs/10-game/02-school-system.md` 「指定技能增效」一致）。

### 通用

| 中文（繁中原文） | English | key |
|---|---|---|
| 外功穿透 | Physical Penetration | `physicalPenetration` |
| 外功抗性 | Physical Resistance | `physicalResistance` |
| 無相穿透 | Formless Penetration | `formlessPenetration` |

### 按武学

| 中文（繁中原文） | English | key 后缀 |
|---|---|---|
| 无名剑法（未收录，见说明） | — | — |
| 醉夢遊春・武學技增傷 | Everspring Umbrella - Martial Art Skill DMG Boost | `everspringMartialBoost` |
| 醉夢遊春・特殊技增傷 | Everspring Umbrella - Special Skill DMG Boost | `everspringSpecialBoost` |
| 天志垂象・蓄力技增傷 | Heavenwill Gauntlets - Charged Skill DMG Boost | `heavenwillChargedBoost` |
| 天志垂象・輕擊及派生技增傷 | Heavenwill Gauntlets - Light Attack and Varied Combo DMG Boost | `heavenwillLightVariedComboBoost` |
| 天志垂象・武學技增傷 | Heavenwill Gauntlets - Martial Art Skill DMG Boost | `heavenwillMartialBoost` |
| 千機索天・重擊增傷 | Skygrasp Rope Dart - Heavy Attack DMG Boost | `skygraspHeavyBoost` |
| 千機索天・特殊技增傷 | Skygrasp Rope Dart - Special Skill DMG Boost | `skygraspSpecialBoost` |
| 明川藥典・治療技增療 | Panacea Fan - Healing Skill Boost | `panaceaHealingSkillBoost` |
| 明川藥典・武學技增療 | Panacea Fan - Martial Art Skill Healing Boost | `panaceaMartialHealingBoost` |
| 明川藥典・特殊技增療 | Panacea Fan - Special Skill Healing Boost | `panaceaSpecialHealingBoost` |
| 十方破陣・蓄力技增傷 | Phalanxbane Blade - Charged Skill DMG Boost | `phalanxbaneChargedBoost` |
| 十方破陣・武學技增傷 | Phalanxbane Blade - Martial Art Skill DMG Boost | `phalanxbaneMartialBoost` |
| 斬雪刀法・蓄力技增傷 | Snowparting Blade - Charged Skill DMG Boost | `snowpartingChargedBoost` |
| 斬雪刀法・武學技增傷 | Snowparting Blade - Martial Art Skill DMG Boost | `snowpartingMartialBoost` |
| 斬雪刀法・輕重擊派生技增傷 | Snowparting Blade - Light/Heavy Attack Varied Combo DMG Boost | `snowpartingVariedComboBoost` |
| 千香引魂蠱・武學技增療 | Soulshade Umbrella - Martial Art Skill Healing Boost | `soulshadeMartialHealingBoost` |
| 千香引魂蠱・特殊技增療 | Soulshade Umbrella - Special Skill Healing Boost | `soulshadeSpecialHealingBoost` |
| 八方風雷槍・蓄力技增傷 | Stormbreaker Spear - Charged Skill DMG Boost | `stormbreakerChargedBoost` |
| 八方風雷槍・特殊技增傷 | Stormbreaker Spear - Special Skill DMG Boost | `stormbreakerSpecialBoost` |
| 嗟夫刀法・蓄力技增傷 | Thundercry Blade - Charged Skill DMG Boost | `thundercryChargedBoost` |
| 嗟夫刀法・護盾增效 | Thundercry Blade - Shield Boost | `thundercryShieldBoost` |
| 嗟夫刀法・特殊技增傷 | Thundercry Blade - Special Skill DMG Boost | `thundercrySpecialBoost` |
| 粟子行雲・蓄力技增傷 | Unfettered Rope Dart - Charged Skill DMG Boost | `unfetteredChargedBoost` |
| 粟子行雲・武學技增傷 | Unfettered Rope Dart - Martial Art Skill DMG Boost | `unfetteredMartialBoost` |
| 粟子行雲・特殊技增傷 | Unfettered Rope Dart - Special Skill DMG Boost | `unfetteredSpecialBoost` |

说明：

- 国际服数据不含 积矩九剑、青山执笔、九重春色、无名剑/枪法、泥犁三垢、
  粟子游尘、舞绫鼓 的定音词条；国服侧清单见
  `docs/10-game/02-school-system.md`「指定技能增效」表。
- 技能类型的官方口径：武學技 = Martial Art Skill、蓄力技 = Charged Skill、
  特殊技 = Special Skill、派生技 = Varied Combo、治療 = Healing（增疗）。
