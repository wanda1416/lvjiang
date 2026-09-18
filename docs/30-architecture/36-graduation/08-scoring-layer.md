# 评分层：从备战方案到毕业率的公共骨架

毕业率引擎（L1～L4）回答“一组战斗属性 → DPS / 毕业率”。它上面还有一层业务骨架，回答
“一个备战方案、一套装备、一份假设 → 毕业率”。最优组合、转律建议、培养建议、备战方案
面板、智能调律五个入口都建立在这层之上，区别只在**怎样生成候选装备**。

```
PlanScoringContext.from_plan(plan)         # 方案 → 流派、方案、计算器、基础属性(含弓玦)、玩法
        │
        ▼
Assumptions(...).project(equipped)         # 满等级 / 赛季承音 / 满承音 / 满定音 / 模拟转律
        │
        ▼
LoadoutScorer.rate(projected)              # effective_equipped → 聚合 → build_graduation_attrs → 计算器
```

## 模块

| 模块 | 职责 |
|---|---|
| `core/graduation/context.py` | `PlanScoringContext.from_plan`：主副武学 → 流派；流派 + 方案名 → 计算器；玩法基础属性 + 弓玦 → 基础属性。弓玦固定为方案已选套装——智能调律不为一件装备假设换弓玦。缺信息抛 `PlanContextError(reason)`。`gongjue_attrs()` 是弓玦属性的唯一实现 |
| `core/graduation/assumptions.py` | `Assumptions` 数据结构与 `project()`；`labels()` 供界面标注“基于：…”。`season_chengyin` 只由最优组合页（搜索空间选项）和智能调律（固定为真）设置，不进共享假设栏、不进备战方案面板——它派生平行候选分支，其余假设是原地提升 |
| `core/graduation/scoring.py` | `LoadoutScorer`：四段链路 + 属性签名缓存 + 求值计数 + 预算/取消（`BudgetExceeded`） |
| `core/combat/combat_attrs.py` | `effective_equipped`：游戏规则归一化（部位合法性、词组 `_stack: max` 同名只取最高）；`apply_hypothetical_caps`：假设投影实现 |

## 各入口如何接入

| 入口 | 候选生成 | 接入方式 |
|---|---|---|
| 备战方案面板 | 当前穿戴 | `attrs_tab._refresh_display` 用 `LoadoutScorer(None, base+弓玦, school).attrs()` 生成毕业率输入，后台任务只调计算器 |
| 最优组合 | 背包候选笛卡尔积 | 向量化内环（`optimal_combo._apply_resistance_to_vec` + `_apply_max_stack_fix`）为性能单独实现；`tests/yysls/test_scoring_consistency.py` 与标准路径对拍 |
| 转律建议 | 八件各至多一次转律 | `transmute_optimizer` 直接持有 `LoadoutScorer`（带预算与取消） |
| 培养建议 / 词条收益率 | 等品质替换、单条增删 | `affix_impact` 内部构造 `LoadoutScorer`；`_graduation_rate` 仅作旧接口包装 |
| 智能调律 | 规则池补全 + 一次转律分支 | `_PlanContext.scorer`；其余七件的三满投影按 (方案, 槽) 缓存，候选合成整套后交内核 |

## 转律的三套口径与共用部分

| 环节 | 转入词条来源 | 为什么不同 |
|---|---|---|
| 规则评级潜力（`evaluator/rule_judge.py`） | 规则的 `transmute_priority`，按优先级取 | 没有毕业率数据，只能按规则作者的价值序 |
| 智能调律（`smart_tuning.py`） | 各流派转律库并集 ∩ 本规则词条库 | 已选调律规则，收益由毕业率决定 |
| 备战方案转律建议（`loadout/transmute.py`） | 各流派转律库并集 | 不承诺用户是什么流派 |

三者共用 `loadout/transmute.py` 的两个函数：`retransfer_capability()`（再次转律能力：承音看
原始等级与承音后开关，未承音看当前等级）与 `transmute_targets(equip, index, pool)`（部位
合法性、去掉第 2～5 条已有、整件校验）。`judge_transmute_eligibility(require_retransfer=…)`
区分“只支持一次转律的装备是否参与”：备战方案不参与，自动调律参与。

评分链路里不来自配置的规则常量（三率上限与精准基准、弓玦比例、五维系数、可转律槽位）
登记在 [../../10-game/06-mechanics-conventions.md](../../10-game/06-mechanics-conventions.md)。
