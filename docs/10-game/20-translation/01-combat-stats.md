# 01 战斗属性与伤害体系

来源前缀：`stat.*`、`system.damageType.*`、`system.resource.*`、
`system.path.*`、`data.path.*`、`system.gearRarity`。

## 五维基础属性

| 中文 | English | key |
|---|---|---|
| 体 | Body | `stat.body` |
| 敏 | Agility | `stat.agility` |
| 劲 | Power | `stat.power` |
| 势 | Momentum | `stat.momentum` |
| 御 | Defense | `stat.defense` |

## 暴击与命中

| 中文 | English | key |
|---|---|---|
| 会心率 | Critical Rate | `stat.crit` |
| 会心伤害加成 | Crit DMG Bonus | `stat.critDmgBonus` |
| 直接会心率 | Direct Critical Rate | `stat.directCrit` |
| 会意率 | Affinity Rate | `stat.affinity` |
| 会意伤害加成 | Affinity DMG Bonus | `stat.affinityDmgBonus` |
| 直接会意率 | Direct Affinity Rate | `stat.directAffinity` |
| 精准率 | Precision Rate | `stat.precision` |
| 外功防御 | Physical Defense | `stat.physicalDefense` |

## 属性攻击（最小/最大）

| 中文 | English | key |
|---|---|---|
| 最小外功攻击 | Min Physical Attack | `stat.minPhys` |
| 最大外功攻击 | Max Physical Attack | `stat.maxPhys` |
| 最小鸣金攻击 | Min Bellstrike Attack | `stat.minBellstrike` |
| 最大鸣金攻击 | Max Bellstrike Attack | `stat.maxBellstrike` |
| 最小裂石攻击 | Min Stonesplit Attack | `stat.minStonesplit` |
| 最大裂石攻击 | Max Stonesplit Attack | `stat.maxStonesplit` |
| 最小牵丝攻击 | Min Silkbind Attack | `stat.minSilkbind` |
| 最大牵丝攻击 | Max Silkbind Attack | `stat.maxSilkbind` |
| 最小破竹攻击 | Min Bamboocut Attack | `stat.minBamboocut` |
| 最大破竹攻击 | Max Bamboocut Attack | `stat.maxBamboocut` |
| 最小无相攻击 | Min Void Attack | `stat.minVoidAttack` |
| 最大无相攻击 | Max Void Attack | `stat.maxVoidAttack` |
| 气血最大值 | Max HP | `stat.maxHp` |
| 最大精力 | Max Endurance | `stat.maxEndurance` |
| 最大耐力 | Max Vitality | `stat.maxVitality` |

## 伤害加成与穿透

| 中文 | English | key |
|---|---|---|
| 外功伤害加成 | Phys DMG Bonus | `stat.physDmgBonus` |
| 鸣金伤害加成 | Bellstrike DMG Bonus | `stat.bellstrikeDmgBonus` |
| 裂石伤害加成 | Stonesplit DMG Bonus | `stat.stonesplitDmgBonus` |
| 牵丝伤害加成 | Silkbind DMG Bonus | `stat.silkbindDmgBonus` |
| 牵丝治疗加成 | Silkbind Healing Bonus | `stat.silkbindHealingBonus` |
| 破竹伤害加成 | Bamboocut DMG Bonus | `stat.bamboocutDmgBonus` |
| 鸣金穿透 | Bellstrike Penetration | `stat.bellstrikePenetration` |
| 裂石穿透 | Stonesplit Penetration | `stat.stonesplitPenetration` |
| 牵丝穿透 | Silkbind Penetration | `stat.silkbindPenetration` |
| 破竹穿透 | Bamboocut Penetration | `stat.bamboocutPenetration` |
| 会对首领会心/伤害 | vs Boss DMG | `stat.vsBossDmg` |
| 会心治疗加成 | Critical Healing Bonus | `stat.criticalHealingBonus` |
| 群体类奇术增伤 | Area Mystic Skill DMG Boost | `stat.areaMysticDmgBoost` |
| 单体类奇术增伤 | Single-Target Mystic Skill DMG Boost | `stat.singleTargetMysticDmgBoost` |

## 武学增伤（装备词条 `data.gear.affixes.*` 同义）

| 中文 | English | key |
|---|---|---|
| 全武学增效 | All Martial Arts | `stat.allMartialArts` |
| 剑武学增伤 | Art of Sword | `stat.swordDmgBoost` |
| 枪武学增伤 | Art of Spear | `stat.spearDmgBoost` |
| 双刀武学增伤 | Art of Dual Blades | `stat.dualBladesDmgBoost` |
| 陌刀武学增伤 | Art of Mo Blade | `stat.moBladeDmgBoost` |
| 横刀武学增伤 | Art of Heng Blade | `stat.hengBladeDmgBoost` |
| 绳镖武学增伤 | Art of Rope Dart | `stat.ropeDartDmgBoost` |
| 扇武学增伤 | Art of Fan | `stat.fanDmgBoost` |
| 伞武学增效 | Art of Umbrella | `stat.umbrellaDmgBoost` |
| 手甲武学增伤 | Art of Gauntlet | `stat.gauntletDmgBoost` |

## 伤害类型（`system.damageType.*`）

| 中文 | English |
|---|---|
| 外功 | Physical |
| 鸣金 | Bellstrike |
| 裂石 | Stonesplit |
| 牵丝 | Silkbind |
| 破竹 | Bamboocut |
| 奇术 | Mystic |
| 武学技能 | Martial Arts |
| 其他 | Other |

## 战斗资源（`system.resource.*`）

| 中文 | English |
|---|---|
| 天志 | Heaven's Will |
| 精元 | Vitality |

## 流派第二字（`system.path.*` + `data.path.*`）

流派命名规律为「伤害类型·单字」，如 鸣金·虹 = `splendor`、裂石·威 =
`stonesplitMight`。两个前缀按流派系统各占一半：

| 单字 | English | key | 对应流派 |
|---|---|---|---|
| 虹 | Splendor | `system.path.splendor` | 鸣金·虹 |
| 影 | Umbra | `system.path.umbra` | 鸣金·影 |
| 玉 | Jade | `system.path.jade` | 牵丝·玉 |
| 霖 | Deluge | `system.path.deluge` | 牵丝·霖 |
| 风 | Wind | `system.path.wind` | 破竹·风 |
| 威 | Might | `data.path.stonesplitMight` | 裂石·威 |
| 钧 | Strength | `data.path.stonesplitStrength` | 裂石·钧 |
| 尘 | Dust | `data.path.bamboocutDust` | 破竹·尘 |
| 鸢 | Kite | `data.path.bamboocutKite` | 破竹·鸢 |
| 混搭 | Mixed | `data.path.mixed` | （工具概念，非官方流派） |

> 牵丝·翊、破竹·樽等 110 级新增流派未见于该表（国际服数据版本滞后），
> 见 `docs/10-game/02-school-system.md`。
