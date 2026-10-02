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
| [08-smart-tuning.md](08-smart-tuning.md) | 智能调律：结合备战方案毕业率的二次调律处理与剩余词条推演 | ✅ 已开放 |
| [09-transmute-simulation.md](09-transmute-simulation.md) | 模拟转律：八件装备联合转律建议、公共装备目标保存、备战方案第四假设 | ✅ 已实现 |
| [10-batch-task-eligibility.md](10-batch-task-eligibility.md) | 批量任务可执行性：业务 WF 前置判定、整用户跳过与生命周期边界 | ✅ 已实现 |
| [11-batch-execution-units.md](11-batch-execution-units.md) | 批量调度单元：可见用户、属性聚合、成员选择与运行快照 | ✅ 已实现 |
| [11-loadout-management.md](11-loadout-management.md) | 跨用户备战方案管理、显式排序与主页面只读边界 | ✅ 已实现 |
| [12-nanlu-season.md](12-nanlu-season.md) | 南吕相和赛季：115 阶词条范围、赛季承音门槛、评级与毕业率适配 | 部分实现 |
| [13-base-attribute-baseline.md](13-base-attribute-baseline.md) | 115 阶满养成基础属性：按流派与来源选择推导无 40 词条基线 | 基础静态模型已实现 |
| [14-yysls-session-files.md](14-yysls-session-files.md) | 燕云会话数据分文件存储与旧节点一次性迁移 | ✅ 已实现 |
| [15-input-timeline.md](15-input-timeline.md) | 输入时间线：描述严格并发的操作序列，多路手势与跨端落地 | 需求与边界已确认，尚未实现 |
| [16-workflow-metadata-editor.md](16-workflow-metadata-editor.md) | 工作流元数据表单与 local / system 保存位置 | ✅ 已实现 |
| [17-unattended-batch.md](17-unattended-batch.md) | 无人值守批量：弹窗改道与异常恢复工作流 | ✅ 已实现 |
| [18-batch-state-layers.md](18-batch-state-layers.md) | 批量状态分层：定义 / 运行草稿 / 执行快照 | ✅ 已实现 |

## 子需求

| 目录 | 内容 |
|------|------|
| [02-player-profile/](02-player-profile/) | 毕业率计算、货币追踪、体力管理子需求 |

待办事项与平台推进计划已移至 [00-meta](../00-meta/README.md)：
[02-backlog.md](../00-meta/02-backlog.md)、
[platforms/android.md](../00-meta/platforms/android.md)、
[platforms/macos.md](../00-meta/platforms/macos.md)。
