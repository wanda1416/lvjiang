# 游戏领域知识

记录《燕云十六声》中与律匠相关的客观机制，以及律匠采用的调律评价规格。

客观机制与评价规格分开维护：机制描述游戏允许什么，评价规格描述律匠如何判断和处置装备。

## 文件索引

| 文件 | 内容 |
|------|------|
| [01-equipment-system.md](01-equipment-system.md) | 装备系统：部位、品质、等级、调律/转律/承音机制 |
| [02-school-system.md](02-school-system.md) | 门派系统：流派定位、武学特色 |
| [03-damage-mechanics.md](03-damage-mechanics.md) | 伤害机制：计算公式、属性转换 |
| [04-tuning-mechanics.md](04-tuning-mechanics.md) | 调律相关文档的边界与索引 |
| [05-ui-pages-and-relations.md](05-ui-pages-and-relations.md) | 游戏 UI 页面、叠层、视图状态及自动化流程关联图 |
| [10-tuning-rules/](10-tuning-rules/README.md) | 律匠评价规格：装备分级、流派规则、转律模拟与熔断 |
| [20-affix-analysis/](20-affix-analysis/README.md) | 词条分布规律分析：数据来源、分析脚本、已知偏差清单 |

## 待补充

- `affix-compendium.md` — 词条大全：名称、类型、数值范围、出现规则
- `rarity-rules.md` — 稀有词条规则

这两篇要填的是**结论**（游戏实际怎么出词条），而结论得先从观测数据里推出来——
方法与脚本见 [20-affix-analysis/](20-affix-analysis/README.md)。

各流派毕业标准与调律评级不是同一概念，毕业率模型见
[毕业率计算](../30-architecture/36-graduation/README.md)。
