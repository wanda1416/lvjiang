# 02 武学

来源前缀：`system.martialArt.*`、`system.weapon.*`、
`data.martialArt.*.talent.*`。

## 武学（19 门，`system.martialArt.*`）

| 中文 | English | key |
|---|---|---|
| 无名剑法 | Nameless Sword | `namelessSword` |
| 无名枪法 | Nameless Spear | `namelessSpear` |
| 积矩九剑 | Strategic Sword | `strategicSword` |
| 九曲惊神枪 | HeavenQuaker Spear | `heavenQuakerSpear` |
| 嗟夫刀法 | Thundercry Blade | `thundercryBlade` |
| 十方破阵 | Phalanxbane Blade | `phalanxbaneBlade` |
| 斩雪刀法 | Snowparting Blade | `snowpartingBlade` |
| 八方风雷枪 | Stormbreaker Spear | `stormbreakerSpear` |
| 泥犁三垢 | Infernal Twinblades | `infernalTwinblades` |
| 粟子游尘 | Mortal Rope Dart | `mortalRopeDart` |
| 粟子行云 | Unfettered Rope Dart | `unfetteredRopeDart` |
| 千机索天 | Skygrasp Rope Dart | `skygraspRopeDart` |
| 青山执笔 | Inkwell Fan | `inkwellFan` |
| 明川药典 | Panacea Fan | `panaceaFan` |
| 九重春色 | Vernal Umbrella | `vernalUmbrella` |
| 醉梦游春 | Everspring Umbrella | `everspringUmbrella` |
| 千香引魂蛊 | Soulshade Umbrella | `soulshadeUmbrella` |
| 天志垂象 | Heavenwill Gauntlets | `heavenwillGauntlets` |

> 表内仅 18 门：国际服数据不含 舞绫鼓（牵丝·翊）等 110 级新增武学。

## 武器类型（`system.weapon.*`）

| 中文 | English |
|---|---|
| 剑 | Sword |
| 枪 | Spear |
| 双刀 | Dual Blades |
| 陌刀 | Mo Blade |
| 横刀 | Heng Blade |
| 绳镖 | Rope Dart |
| 扇 | Fan |
| 伞 | Umbrella |
| 手甲 | Gauntlet |

## 武学天赋（`data.martialArt.*.talent.*`）

有繁中依据的条目：

| 中文（繁中原文） | English | key |
|---|---|---|
| 外功攻擊提升 | Physical Attack Up | `martialArt.*.talent.0`（无名剑/斩雪/千香） |
| 會心率提升 | Critical Rate Up | `panaceaFan.talent.0`、`phalanxbaneBlade.talent.0`、`skygraspRopeDart.talent.0` |
| 會心傷害提升 | Critical DMG Up | `snowpartingBlade.talent.1` |
| 鐵衛穿透提升 | Iron Guard Penetration Up | `phalanxbaneBlade.talent.1` |
| 裂石屬性提升 | Stonesplit Attribute Up | 多门武学共通 |
| 破竹屬性提升 | Bamboocut Attribute Up | `thundercryBlade.talent.2`、`skygraspRopeDart.talent.2` |
| 鳴金屬性提升 | Bellstrike Attribute Up | `namelessSpear.talent.3`、`namelessSword.talent.3` |
| 牽絲屬性提升 | Silkbind Attribute Up | `panaceaFan.talent.3`、`soulshadeUmbrella.talent.3` |
| 勁力外功攻擊提升 | Power Physical Attack Up | `thundercryBlade.talent.1` |
| 蓄力及派生技強化 | Charged and Varied Combo Enhancement | `thundercryBlade.talent.0` |
| 蓄力及派生技會心率提升 | Charged and Varied Combo Critical Rate Up | `thundercryBlade.talent.2` |
| Buff 強化 | Buff Enhancement | `soulshadeUmbrella.talent.0` |
| 會意率提升 | Affinity Rate Up | `namelessSpear.talent.0` |
| 會意傷害提升 | Affinity DMG Up | `namelessSpear.talent.2` |
| 最大精力提升 | Max Endurance Up | `namelessSpear.talent.1` |
| 天志傷害提升 | Heaven's Will DMG Boost | `skygraspRopeDart.talent.1` |
| 重擊治療提升 | Heavy Attack Healing Boost | `panaceaFan.talent.1` |
| 奇術精準強化 | Mystic Precision Enhancement | `panaceaFan.talent.2` |
| 真氣相持強化 | Qi Struggle Enhancement | `namelessSword.talent.0` |
| 劍氣會意強化 | Sword Qi Affinity Enhancement | `namelessSword.talent.2` |
| 會心治療強化 | Critical Heal Enhancement | `soulshadeUmbrella.talent.2` |

> 天赋名繁中列多数为空，上表英文来自 `en` 列；仅列出了能确认中文叫法的
> 条目，如需完整天赋清单直接查 CSV 的 `data.martialArt.*` 段。
