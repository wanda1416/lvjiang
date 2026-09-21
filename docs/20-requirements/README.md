# 律匠需求文档

> 需求文档索引。游戏机制介绍见 [10-game](../10-game/README.md)，架构设计见 [30-architecture](../30-architecture/README.md)。

---

## 需求列表

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-auto-tuning.md](01-auto-tuning.md) | 自动调律：背包遍历、调律决策编排、行为处置 | ✅ 已实现 |
| [02-player-profile.md](02-player-profile.md) | 玩家档案系统：毕业率分析、货币追踪、心力体力管理 | 部分实现 |
| [03-wf-editor-plugin.md](03-wf-editor-plugin.md) | 工作流 DSL 编辑器插件：语法高亮、实时诊断、语义智能 | ✅ 已实现 |
| [04-i18n.md](04-i18n.md) | 国际化支持框架：tr() 函数、翻译文件、UI 改造 | ✅ 已实现 |
| [05-tuning-management.md](05-tuning-management.md) | 调律管理：实时总览、版本化历史、七天匿名补传 | ✅ 已实现 |
| [06-task-history.md](06-task-history.md) | 任务历史：单任务/批量两级 ID、参数与产出查询、独立日志 | ✅ 已实现 |
| [07-in-match-navigation.md](07-in-match-navigation.md) | 局内地图目标闭环导航与撤离：渡尘墟、觉障林等玩法通用 | 部分实现（控制面、地图管理、朝向识别已落地） |
| [08-smart-tuning.md](08-smart-tuning.md) | 智能调律：结合备战方案毕业率的二次调律处理与剩余词条推演 | 开发模式内测，普通模式未开放 |
| [09-transmute-simulation.md](09-transmute-simulation.md) | 模拟转律：八件装备联合转律建议、公共装备目标保存、备战方案第四假设 | ✅ 已实现 |
| [10-batch-task-eligibility.md](10-batch-task-eligibility.md) | 批量任务可执行性：业务 WF 前置判定、整用户跳过与生命周期边界 | ✅ 已实现 |

## 子需求

| 目录 | 内容 |
|------|------|
| [02-player-profile/](02-player-profile/) | 毕业率计算、货币追踪、体力管理子需求 |

待办事项与平台推进计划已移至 [00-meta](../00-meta/README.md)：
[02-backlog.md](../00-meta/02-backlog.md)、
[platforms/android.md](../00-meta/platforms/android.md)、
[platforms/macos.md](../00-meta/platforms/macos.md)。
