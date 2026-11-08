# 游戏术语中英对照（20-translation）

本目录整理《燕云十六声》国际服（Where Winds Meet）官方翻译中与律匠相关的
游戏独有术语，供识别映射、文档写作和与海外用户交流时统一口径。

## 数据来源

- 官方翻译表：国际服构建工具 [where-builds-meet](https://github.com/) 的
  `locales/translations.csv`（本地路径
  `C:\Users\wanghui\Githubs\game\where-builds-meet\locales\translations.csv`，
  1081 条，12 种语言列）。
- 该表即律匠 i18n 体系（`config/i18n/apps/yysls/`）此前采用的官方词表来源。
- 表中 `en` 与 `zh-Hant` 列最完整；`zh-Hans`、`ja` 基本为空。本目录的简中列
  以繁中为基准转换，并已与 `docs/10-game` 各机制文档中的国服叫法核对。

## 键结构

| 前缀 | 含义 | 收录位置 |
|---|---|---|
| `stat.*` | 战斗属性/词条 | [01-combat-stats.md](01-combat-stats.md) |
| `system.damageType.*` | 伤害类型 | 01 |
| `system.resource.*` | 战斗资源 | 01 |
| `system.martialArt.*` / `data.martialArt.*.talent.*` | 武学与武学天赋 | [02-martial-arts.md](02-martial-arts.md) |
| `system.weapon.*` | 武器类型 | 02 |
| `system.innerWay.*` / `data.innerway.*` | 心法 | [03-innerway.md](03-innerway.md) |
| `data.skill.*` | 技能（通用/奇术/各武学） | [04-skills.md](04-skills.md) |
| `data.dot.*` | 持续伤害 | 04 |
| `data.buff.*` / `data.debuff.*` | 增益/减益 | [05-buffs-debuffs.md](05-buffs-debuffs.md) |
| `system.gearSlot / gearRarity / gearSet`、`data.gearSet / armorSet / arsenal / bowRingSet` | 装备体系 | [06-equipment.md](06-equipment.md) |
| `system.attunement.*` | 定音词条 | 06 |
| `data.divinecraft.*` | 神工 | [07-consumables-misc.md](07-consumables-misc.md) |
| `data.script.*` | 帖 | 07 |
| `data.food.*` / `data.breakthrough.*` | 食物、突破 | 07 |
| `system.path.*` / `data.path.*` | 流派第二字 | 01、02 均有索引 |
| `ui.*`、`data.rotation.*`、`data.build.*` | where-builds-meet 自身界面/玩法配置 | **不收录**（非游戏术语） |

## 使用原则

1. **官方译名优先**。表内 `en` 列来自游戏内文本，写英文文档、回复海外用户时
   以它为准；不要自造直译。
2. **国服叫法以本仓库机制文档为准**。流派（裂石·威）、调律、定音等机制词的
   权威定义在 `docs/10-game` 各文档；本目录只做中英对照，不重新定义机制。
3. 表内个别 `en` 列存在拼写瑕疵（如 `penetraion`）或社区工具自造的缩写
   （`shortName`，如 `WTS`、`EDP`），引用时注意区分正式名与缩写。
4. 少数条目繁中列为空（多为 buff/debuff 描述与天赋名），对照表只收有中文
   依据的条目；纯英文无对照的技能变体（如 `1-Hit`/`[Cancel]` 分段）按规则
   标注而非逐条翻译。

## 文件索引

| 文件 | 内容 |
|---|---|
| [01-combat-stats.md](01-combat-stats.md) | 战斗属性、伤害类型、流派第二字、资源 |
| [02-martial-arts.md](02-martial-arts.md) | 武学、武器类型、武学天赋 |
| [03-innerway.md](03-innerway.md) | 心法 |
| [04-skills.md](04-skills.md) | 技能与持续伤害 |
| [05-buffs-debuffs.md](05-buffs-debuffs.md) | 增益/减益 |
| [06-equipment.md](06-equipment.md) | 装备部位/品阶/套装/弓玦、定音词条 |
| [07-consumables-misc.md](07-consumables-misc.md) | 神工、帖、食物、突破 |
