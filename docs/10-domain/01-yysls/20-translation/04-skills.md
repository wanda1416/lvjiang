# 04 技能与持续伤害

来源前缀：`data.skill.*`、`data.dot.*`。技能变体（第 N 击、打斷/取消、
蓄力分段）在英文里用固定后缀表达，不再逐条列表：

- `Nth Hit` / `N-Hit` = 第 N 击/一下
- `[Cancel]`、中文「(打斷)」= 打断（取消）
- `Fast Charge` = 快速蓄力；`Slam` = 下砸

## 通用技能（`data.skill.general.*`）

| 中文（繁中原文） | English | key |
|---|---|---|
| 卸势 | Deflect | `Deflect` |
| 成功卸势 | Deflect (Successful) | `DeflectSuccessful` |
| 完美闪避 | Perfect Dodge | `PerfectDodge` |
| 完美闪避（打断） | Perfect Dodge [Cancel] | `PerfectDodgeCancel` |
| 防反触发 | Riposte Trigger | `RiposteTrigger` |
| 防御 | Defense | `Defense` |
| 易水歌 | Morale Chant | `MoraleChant` |
| 抗造 4 护盾 | AoR T4 Shield | `AoRT4Shield` |

## 奇术（`data.skill.mystic.*`）

| 中文（繁中原文） | English | key |
|---|---|---|
| 太白醉月 | Drunken Poet | `DrunkenPoet1..5`（第一~第五击；第5击可打断） |
| 喝酒 | Drunken Poet Drink | `DrunkenPoetDrink`（及 Cancel 变体） |
| 神龙吐火 | Dragon's Breath | `DragonsBreath1/2`（1/2 下，可打断） |
| 神龙吐火·暗灼 | Dragon's Breath - Smolder | `DragonsBreathSmolder1/2` |
| 叶龙骧首·叠浪 | Dragon Head - Tide | `DragonHeadTide` |
| 箫吟千浪 | Flute Chanting a Thousand Waves | `FluteRipple`；完整版 `FluteOfTheTides`；打断版 `FluteOfTheTidesCancel` |
| 金蟾腾跃（打断） | Leaping Toad [Cancel] | `LeapingToad` |
| 万物为锋 | World to Sword | `WorldToSword` |
| 阴阳迷踪步 | Ghostly Step | `GhostlySteps` |
| 阴阳迷踪步·影 | Ghostly Step - Umbra | `GhostlyStepsUmbra` |
| 阴阳迷踪步·影闪避（各武器） | Ghostly Step - Umbra Dodge (… weapon) | `GhostlyStepsUmbraDodge*`（7 种武器变体） |
| 清风霁月 | Serene Breeze | `SereneBreeze` |
| 气刃 | Qi Blade | `QiBlade` |
| 骑龙回马 | Soaring Spin | `SoaringSpin2`；1 下 = `SoaringSpin1` |
| 衍九矢 | Bursting Nine | `BurstingNine`；二连 = `BurstingNine2Shots` |
| 爆燃爆炸 | Combustion Explosion | `CombustionExplosion` |
| 暗灼爆炸 | Smolder Explosion | `SmolderExplosion` |

## 各武学技能（`data.skill.<martial>.*`）

按武学分节，只列有繁中对照的正式名（`shortName` 是构建器内部缩写，
如 `Heng Q`、`Mo Charged`，不作为官方译名）。

### 天志垂象（heavenwillGauntlets，手甲）

| 中文（繁中原文） | English | key |
|---|---|---|
| 天下归义 | All Under Justice | `AllUnderJustice` |
| 莫如法天 | Celestial Mandate | `CelestialMandate` |
| 我有天志 | Heavenwill Declared | `HeavenwillDeclared` |
| 轻击鹰隼 | Light Attack Falcon | `LightAttackFalcon` |
| 天欲义第 1–6 击 | Righteous Reign 1st–6th Hit | `RighteousReign1stHit..6thHit`（第6击另有 Cancel） |
| 有义则治第 1–3 击 | Virtuous Enthroned 1st–3rd Hit | `VirtuousEnthroned1stHit..3rdHit` |
| 无义当诛 | Vile Condemned | `VileCondemned`（蓄力/终结/命中变体 `VileCondemnedCharge/EndHit/Hit`） |
| 恶不义 | Wicked Defiance | `WickedDefiance` |
| 天威伤害 | Heaven's Might Damage | `SkyGrippedReplay` |
| 扶摇直上 T6 返还 | Soaring High T6 Refund | `SoaringHighT6Refund` |

### 十方破阵（phalanxbaneBlade，陌刀）

| 中文（繁中原文） | English | key |
|---|---|---|
| 安西军·焚心第二/三段 | Anxi Soldier Burning Heart 2nd/3rd Stage | `AnxiSoldierBurningHeart2/3` |
| 安西军·荡八荒 | Anxi Soldier Total Annihilation | `AnxiSoldierTotalAnnihilation` |
| 焚此心一段/二段/三段 | Burning Heart 1st/2nd/3rd Stage | `PhalanxbaneHeavyCharged1/2/3` |
| 荡八荒 | Total Annihilation | `PhalanxbaneQ` |
| 唤千军 | Legion Summon | `PhalanxbaneSpecial` |
| 蓄力中间段 | Burning Heart Charge 2/3、Fast Charge 2/3、Pre Charge | `PhalanxbaneHeavyCharge*/FastCharge*/PreCharge` |
| 蓄力下砸 | Burning Heart (Fast) Slam 1–3 | `PhalanxbaneHeavy(Fast)Slam1..3` |

### 斩雪刀法（snowpartingBlade，横刀）

| 中文（繁中原文） | English | key |
|---|---|---|
| 白刃杀将 | General's Bane | `SnowpartingQ` |
| 白刃杀将·斩 | General's Bane: Slash | `SnowpartingQSlash` |
| 白刃杀将·刺 | General's Bane: Stab | `SnowpartingQStab` |
| 白刃杀将滑行 | General's Bane - Slide | `SnowpartingQSlide` |
| 安西军·白刃杀将·刺 | Anxi Soldier General's Bane: Stab | `AnxiSoldierGeneralsBaneStab` |
| 安西军·拨雪寻春 | Anxi Soldier Snowbreak Spring | `AnxiSoldierSnowbreakSpring` |
| 拨雪寻春 | Snowbreak Spring | `SnowpartingHeavyVC` |
| 塚上霜 | Grave Frost | `SnowpartingLightCharged` |
| 横刀易武 | Snowparting Conversion | `SnowpartingConversion` |
| 飞鸿踏雪 | Fleeting Trace | `SnowpartingSpecial` |

### 八方风雷枪（stormbreakerSpear，枪）

| 中文（繁中原文） | English | key |
|---|---|---|
| 风雷啸 | Storm Roar | `StormRoar` |
| 惊雷震 | Thunder Shock | `ThunderShock`（1 下打断版 `ThunderShock1`） |

### 嗟夫刀法（thundercryBlade，裂石·威主武学）

| 中文（繁中原文） | English | key |
|---|---|---|
| 山崩地裂 | Avalanche | `Avalanche`（1 下打断版 `Avalanche1`） |
| 鹰扬虎视 | Predator's Shield | `PredatorsShield` |
| 破石穿岩劈 | Stonebreaker Cleave | `StonebreakerCleave`（1 下打断版 `StonebreakerCleave1`） |
| 破石穿岩震 | Stonebreaker Quake | `StonebreakerQuake` |

### 明川药典（panaceaFan，扇）

| 中文（繁中原文） | English | key |
|---|---|---|
| 坐看云起 | Cloudburst Healing | `CloudburstHealing`（及打断版） |
| 点翠沾衣第一~三击 | Emerald Dewtouch 1st–3rd Hit | `EmeraldDewtouch1/2/3`（含 Cancel 变体） |
| 白云无尽 | Endless Cloud | `EndlessCloud`（及打断版） |
| 临川四问第 1–4 击 | Fourfold Inquiry 1st–4th Hit | `FourfoldInquiry1..4` |
| 朝雨轻尘 | Morning Drizzle | `MorningDrizzle` |
| 跳重 | Jump Heavy | `JumpHeavy` |

### 千香引魂蛊（soulshadeUmbrella，伞）

| 中文（繁中原文） | English | key |
|---|---|---|
| 千莳余响 | Echoes of a Thousand Plants | `EchoesOfAThousandPlants`（含打断版、扇QQ 变体） |
| 一念浮生 | Floating Grace | `FloatingGrace` |

### 千机索天（skygraspRopeDart，绳镖）

| 中文（繁中原文） | English | key |
|---|---|---|
| 索天势 | Sky Grasped | `SkyGrasped` |
| 飞来势 | Snaring Lash | `SnaringLash`（及打断版） |
| 飞来势鹰隼 | Snaring Lash Falcon | `SnaringLashFalcon` |

## 持续伤害（`data.dot.*`）

| 中文（繁中原文） | English | key |
|---|---|---|
| 爆燃 | Combustion | `dot.mystic.Combustion` |
| 暗灼 | Smolder | `dot.mystic.Smolder` |
| 蟾毒 | Toad Venom | `debuff.mystic.ToadVenom`（见 05） |
| 次级蟾毒 | Lesser Toad Venom | `debuff.mystic.LesserToadVenom`（见 05） |

> 无名剑法/无名枪法/泥犁三垢/青山执笔/九重春色/醉梦游春/粟子行云/粟子游尘
> 的技能分段未进入该表（国际服未收录或仅有英文变体名），需要时直接查 CSV
> `data.skill.*` 段。
