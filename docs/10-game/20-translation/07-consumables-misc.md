# 07 神工、帖、食物与突破

来源前缀：`data.divinecraft.*`、`data.script.*`、`data.food.*`、
`data.breakthrough.*`。

## 神工（`data.divinecraft.*`）

组合命名规律：`<属性>-<属性>`，效果为对应伤害类型加成与耐力返还。

| 中文（繁中原文） | English | key |
|---|---|---|
| 無 | None | `None` |
| 火 | Fire | `Fire` |
| 火·毒 | Fire-Poison | `FirePoison` |
| 火·水 | Fire-Water | `FireWater` |
| 毒·火 | Poison-Fire | `PoisonFire` |
| 毒·水 | Poison-Water | `PoisonWater` |
| 水·火 | Water-Fire | `WaterFire` |
| 水·毒 | Water-Poison | `WaterPoison` |

效果示例（`Fire.description`）：HP DMG +1.5% ＝ 氣血傷害 +1.5%；
`FireWater.description`：HP DMG +1.5%, Qi DMG +4% ＝ 氣血傷害 +1.5%，
真氣傷害 +4%。真气在英文语境作 **Qi**。

## 帖（`data.script.*`）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 無 | None | `None` | 无帖效果 |
| 存煞帖 | Convergence Script | `Convergence` | 真气 60% 以上的目标受到的属性攻击伤害 +15% |
| 懸解帖 | Detachment Script | `Detachment` | 气血 40% 以下的目标受到的会意伤害 +10% |
| 明鑑帖 | Insight Script | `Insight` | 气血 60% 以上的目标受到的奇术伤害 +15% |
| 行藏帖 | Opportunity Script | `Opportunity` | 气血 40% 以下的目标受到的会心伤害 +10% |
| 承歡帖 | Revelry Script | `Revelry` | 自身气血 ≤30% 时受伤获得承欢（伤害 +20%，见 05） |
| 蝕淵帖 | Voidrot Script | `Voidrot` | 真气 40% 以下的目标受到的会意伤害 +10% |
| 鬼掣帖 | Wraithstrike Script | `Wraithstrike` | 真气 40% 以下的目标受到的会心伤害 +10% |

## 食物（`data.food.*`）

| 中文（繁中原文） | English |
|---|---|
| 無 | None |
| 涮魚 | Simmering Fish Slices |

## 突破（`data.breakthrough.*`）

国际服仅收录 `16`、`17` 两档（名与值相同，无独立译名）。国服语境的
突破/等级档位见 `docs/10-game/01-equipment-system.md`。
