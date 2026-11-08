# 05 增益与减益

来源前缀：`data.buff.*`、`data.debuff.*`。按流派分组；`name` 为正式名，
`shortName` 是构建器缩写（如 `Cog`、`TP`），只列正式名。描述列摘录官方
效果原文（en/繁中），供核对机制。

## 通用（`data.buff.general.*`）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 春 | Bloom | `Bloom` | 四时无常的春季结果 |
| 夏 | Flare | `Flare` | 全武学 +10%，75% 血以上再 +10% |
| 秋 | Yield | `Yield` | 奇术伤害 +10%，每秒回 2 耐力 |
| 冬 | Frost | `Frost` | 四时无常的冬季结果 |
| 飞隼 | Hawkwing | `Hawkwing` | 每层外功攻击 +2% |
| 易水 | Yi River | `YiRiver` | 每层伤害 +1%、外功穿透 +2 |
| 護盾 | Shield | `Shield` | 通用角色护盾 |
| 醉酒 | Intoxicated | （mystic 组同名） | 允许使用太白醉月/神龙吐火 |
| — | Revelry | `Revelry` | 造成伤害 +20%（承欢帖效果） |
| — | Turnaround | `Turnaround` | 返还奇术 30% 耐力消耗，上限 10 |
| — | Seasonal Edge Cooldown | `SeasonalEdgeCooldown` | 期间四时无常不触发 |

通用减益：

| 中文（繁中原文） | English | key |
|---|---|---|
| 控制 | Controlled | `debuff.general.Controlled` |
| 氣竭 | Exhausted | `debuff.general.Exhausted` |

## 鸣金（bellstrikeUmbra / bellstrikeSplendor）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| — | Concentration | `buff.bellstrikeUmbra.Concentration` | 会意伤害 +10%；凝神章 T3 生效期间再提高直接会意率 |
| 驚魂 | Soul-Shaken | `debuff.bellstrikeUmbra.SoulShaken` | 每层受到的 DoT +5%；来源为影再 +5% |
| 真氣失衡 | Qi Imbalance | `debuff.bellstrikeSplendor.QiImbalance` | 受真气伤害 +10%；气竭期间气血伤害 +8%，鸣金再 +8% |

## 裂石（stonesplitStrength / stonesplitMight）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 絕韻 | Cadence | `buff.stonesplitStrength.Cadence` | 施加时尝试获得防反 |
| 蓄力強化 | Charge Enhancement | `buff.stonesplitStrength.ChargeEnhancement` | 焚心获得热血难凉 |
| 斷嶽 | Cleftpeak | `buff.stonesplitStrength.Cleftpeak` | 每层气血伤害 +1%；满层轻/重派生、安西军再 +8% |
| 忘機 | Forgetfulness | `buff.stonesplitStrength.Forgetfulness` | 跳过斩雪轻击蓄力 |
| 熱血難涼 | Inner Passion | `buff.stonesplitStrength.InnerPassion` | 加速焚心并提高伤害 |
| 鐵衣戍魂 | Iron Guard | `buff.stonesplitStrength.IronGuard` | 气血伤害 +8% |
| 開山 | Mountain Splitter | `buff.stonesplitStrength.MountainSplitter` | 焚心/安西军会心伤害 +10% 且必暴 |
| 防反 | Riposte | `buff.stonesplitStrength.Riposte` | 下一次山崩地裂施放时间 −2s |
| 穿喉 | Throat-Pierced | `buff.stonesplitStrength.ThroatPierced` | 每层外功穿透 +2、会心伤害 +2%；T3/T6 起派生与安西军追加 |
| 破陣 | Breakthrough | `buff.stonesplitMight.Breakthrough` | 蓄力技伤害 +42% |
| 摧鼓 | Drumbeat | `buff.stonesplitMight.Drumbeat` | 蓄力技伤害 +15%；鹰扬虎视后转为破阵 |
| 硬荏 | Hardened Foe | `buff.stonesplitMight.HardenedFoe` | 护盾破裂后伤害 +10% |
| 膽寒 | Dread | `debuff.stonesplitStrength.Dread` | 受气血伤害 +12% |
| 懼刃 | Fearful Blade | `debuff.stonesplitStrength.FearfulBlade` | 属性抗性 −16；对钧受气血伤害 +8% |
| 易傷 | Vulnerable | `debuff.stonesplitMight.Vulnerable` | 受气血/真气伤害 +8%，对威再 +8% |

## 牵丝（silkbindDeluge）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 千蒔餘響 | Echoes of a Thousand Plants | `buff.silkbindDeluge.EchoesOfAThousandPlants` | 1 秒后每秒治疗一次，治疗 +15% |
| 一念浮生 | Floating Grace | `buff.silkbindDeluge.FloatingGrace` | 伤害 +10%（霖变体 +24%，`FloatingGraceDeluge`） |
| 朝雨 | Morning Drizzle | `buff.silkbindDeluge.MorningDrizzle` | 立即治疗一次，之后每秒一次，持续 5 秒 |

## 破竹（bamboocutKite / bamboocutWind / bamboocutDust）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 格物 | Cognition | `buff.bamboocutKite.Cognition` | 每层武学技各类属性穿透 +2；鹰隼/无义当诛每层再 +2；三穷致知 T6 起外功穿透同额 |
| 撼天 | Etherwrath | `buff.bamboocutKite.Etherwrath` | 每层全攻击 +1.2%；5 层武学技各类穿透 +6 |
| — | Heaven's Unity | `buff.bamboocutKite.HeavensUnity` | 莫如法天天志生成 0.1→0.3 |
| 崩解 | Disintegration | `buff.bamboocutWind.Disintegration` | 每层外功穿透 +5、会心伤害 +5% |
| 幻鳴 | Phantom Chime | `debuff.bamboocutDust.PhantomChime` | 每层外功抗性 −2 |
| 威 | Heaven's Might | `debuff.bamboocutKite.HeavensMight` | 鹰隼/无义当诛受到的气血伤害 +10% |
| — | Vile Condemned End Cooldown | `debuff.bamboocutKite.VileCondemnedEndCooldown` | 无义当诛不可选终结击 |

## 奇术（mystic）

| 中文（繁中原文） | English | key |
|---|---|---|
| 葉龍驤首・疊浪 | Dragon Head - Tide | `buff.mystic.DragonHeadTide` |
| 滄浪勁 | Surging Waves | `buff.mystic.SurgingWaves` |
| 迷蹤 | Mystery | `buff.mystic.Mystery` |
| 迷蹤增傷 | Mystery DMG Boost | `buff.mystic.MysteryDMGBoost` |
| 迷蹤・影 | Mystery - Umbra | `buff.mystic.MysteryUmbra` |
| 醉酒 | Intoxicated | `buff.mystic.Intoxicated` |
| 簫 | Flute | `buff.mystic.Flute` |
| 萬物為鋒 | World to Sword | `buff.mystic.WorldToSword` |
| — | Enhanced Drunken Poet | `buff.mystic.EnhanceDrunkenPoet` |
| 蟾毒 | Toad Venom | `debuff.mystic.ToadVenom` |
| 次級蟾毒 | Lesser Toad Venom | `debuff.mystic.LesserToadVenom` |

## 心法（innerway）

| 中文（繁中原文） | English | key | 效果摘要 |
|---|---|---|---|
| 恨（青衣蛊）各重数 | Qingyi's Charm T0/T1/T6 | `debuff.innerway.QingyisCharmT*` | 每层外功防御 −0.6%/−1.2%；T6 满层再 −10 外功抗性 |

> `shortName` 缩写（Cog/EW/SS/TP/EDP/WTS…）仅用于构建器内部与日志，
> 官方正式名以 `name` 为准。
