# 调律与转律：文档边界

调律相关的内容曾集中在本文，现已按「客观机制 / 评价规格 / 自动化流程 / 用户配置」四层拆分。
本文只保留边界说明与索引，不再重复任何一方的正文，避免多处副本各自漂移。

## 权威归属

| 内容 | 权威文档 | 说明 |
|------|----------|------|
| 游戏调律、转律、无相攻击、承音与传律的客观机制 | [01-equipment-system.md](01-equipment-system.md) | 游戏允许什么，与律匠实现无关 |
| 装备分级、流派词条要求、顶级判定、转律熔断 | [10-tuning-rules/](10-tuning-rules/README.md) | 律匠如何判断和处置装备 |
| 背包遍历、材料检查、自动处置与执行流程 | [../20-requirements/01-auto-tuning.md](../20-requirements/01-auto-tuning.md) | 自动调律的需求与实现结构 |
| 狗粮规则、大律准石检查等材料配置 | [../60-userguide/04.02-default-config.md](../60-userguide/04.02-default-config.md)、[04.03-behavior-config.md](../60-userguide/04.03-behavior-config.md) | 默认值与在 UI 中如何修改 |
| 用户如何配置并运行自动调律 | [../60-userguide/README.md](../60-userguide/README.md) | 面向使用者的操作指引 |

## 程序侧对应

| 文档层 | 配置/代码 |
|--------|-----------|
| 评价规格 | `config/system/yysls/tuning_rules/*.yaml`（规则本体）、`config/system/yysls/tune_config.yaml`（启用与顺序） |
| 词条标准名 | `config/system/yysls/game_config.yaml` 的 `affix_caps` |
| 材料与扫描处置 | `config/system/yysls/base_groups/*.yaml` |

修改规格时必须同步：[10-tuning-rules/](10-tuning-rules/README.md) 下的文档、对应 YAML、以及相关测试。
