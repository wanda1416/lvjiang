# 律匠需求文档

> 最后更新：2026-10-06（基线 v0.13.12）。本层描述如与代码或配置不符，以代码与配置为准。

> 需求文档索引。游戏机制介绍见 [10-game](../10-game/README.md)，架构设计见 [30-architecture](../30-architecture/README.md)。

---

## 需求列表

每份文档只写需求、边界与验收；实现细节在[30-architecture](../30-architecture/README.md)，操作步骤在[60-userguide](../60-userguide/README.md)。

状态取值只有三种：`已实现`、`部分实现`、`规划`；权威写法是各文档首行的状态行，
本表的取值为便于扫读的摘要，两者不一致时 `scripts/docs_check.py` 会报错。

### 10-tuning（调律域：自动调律、规则管理、智能调律、转律模拟、赛季与属性基线）

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-auto-tuning.md](10-tuning/01-auto-tuning.md) | 自动调律：背包遍历、调律决策编排、行为处置 | 已实现 |
| [02-tuning-management.md](10-tuning/02-tuning-management.md) | 调律管理：实时总览、版本化历史、七天匿名补传 | 已实现 |
| [03-smart-tuning.md](10-tuning/03-smart-tuning.md) | 智能调律：结合备战方案毕业率的二次调律处理与剩余词条推演 | 已实现 |
| [04-transmute-simulation.md](10-tuning/04-transmute-simulation.md) | 模拟转律：八件装备联合转律建议、公共装备目标保存、备战方案第四假设 | 已实现 |
| [05-nanlu-season.md](10-tuning/05-nanlu-season.md) | 南吕相和赛季：115 阶词条范围、赛季承音门槛、评级与毕业率适配 | 部分实现 |
| [06-native-attack-affixes.md](10-tuning/06-native-attack-affixes.md) | 本属攻击统一规则表达：用最大/最小本属攻击替代无相与本流派属攻 | 已实现 |
| [07-base-attribute-baseline.md](10-tuning/07-base-attribute-baseline.md) | 115 阶满养成基础属性：按流派与来源选择推导无 40 词条基线 | 部分实现（基础静态模型已实现，细分来源待补） |

### 20-equipment（装备与数据域：备战方案、出装计算、会话数据）

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-loadout-management.md](20-equipment/01-loadout-management.md) | 跨用户备战方案管理、显式排序与主页面只读边界 | 已实现 |
| [02-build-calculator.md](20-equipment/02-build-calculator.md) | 出装搭配、角色模拟计算器及总词条合法分配 | 已实现（不含自动调律联动） |
| [03-combat-attribute-sources.md](20-equipment/03-combat-attribute-sources.md) | 当前方案战斗属性来源、逐词条明细与白字黄字对照 | 已实现 |

### 30-batch（批量域：可执行性判定、调度单元、无人值守、状态分层）

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-batch-task-eligibility.md](30-batch/01-batch-task-eligibility.md) | 批量任务可执行性：业务 WF 前置判定、整用户跳过与生命周期边界 | 已实现 |
| [02-batch-execution-units.md](30-batch/02-batch-execution-units.md) | 批量调度单元：可见用户、属性聚合、成员选择与运行快照 | 已实现 |
| [03-unattended-batch.md](30-batch/03-unattended-batch.md) | 无人值守批量：弹窗改道与异常恢复工作流 | 已实现 |
| [04-batch-state-layers.md](30-batch/04-batch-state-layers.md) | 批量状态分层：定义 / 运行草稿 / 执行快照 | 已实现 |

### 40-profile（用户档案域：Profile 数据模型、毕业率、货币、心力体力）

| 文档 | 内容 | 状态 |
|------|------|------|
| [README.md](40-profile/README.md) | 玩家档案系统：四模型数据层、心力体力管理、货币追踪与毕业率分析 | 部分实现（四模型已实现，角色基础数据与材料库存待补） |

### 50-platform（平台与执行域：多语言、局内导航、输入时间线、执行目标与并发）

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-i18n.md](50-platform/01-i18n.md) | 国际化支持框架：tr() 函数、翻译文件、UI 改造 | 已实现 |
| [02-in-match-navigation.md](50-platform/02-in-match-navigation.md) | 局内地图目标闭环导航与撤离：渡尘墟、觉障林等玩法通用 | 部分实现（基础设施已落地，导航运行时未开始） |
| [03-input-timeline.md](50-platform/03-input-timeline.md) | 输入时间线：描述严格并发的操作序列，多路手势与跨端落地 | 已实现（待真机验收并发手势） |
| [04-execution-targets.md](50-platform/04-execution-targets.md) | 单窗口、多设备连接与显式执行目标 | 已实现 |
| [05-concurrent-target-execution.md](50-platform/05-concurrent-target-execution.md) | 多执行目标并发任务、断线恢复与 Lv1 授权边界 | 已实现（待多设备实机验收） |
| [06-workflow-capture-reuse.md](50-platform/06-workflow-capture-reuse.md) | WF 显式截图复用、同帧识别与诊断留图 | 已实现（待偶发菜单失败实机取证） |
| [07-android-offline.md](50-platform/07-android-offline.md) | PC 单向同步与 Android 悬浮控制离线任务 | 部分实现（第一阶段已实现，游戏验收待完成） |

### 60-editor（编辑器与查看工具：DSL 插件、任务历史、元数据表单、画布组选区）

| 文档 | 内容 | 状态 |
|------|------|------|
| [01-wf-editor-plugin.md](60-editor/01-wf-editor-plugin.md) | 工作流 DSL 编辑器插件：语法高亮、实时诊断、语义智能 | 已实现 |
| [02-task-history.md](60-editor/02-task-history.md) | 任务历史：单任务/批量两级 ID、参数与产出查询、独立日志 | 已实现 |
| [03-workflow-metadata-editor.md](60-editor/03-workflow-metadata-editor.md) | 工作流元数据表单与 local / system 保存位置 | 已实现 |
| [04-canvas-group-selection.md](60-editor/04-canvas-group-selection.md) | 画布组选区：Ctrl 多选与整组平移、修饰键语义 | 已实现 |

待办事项与平台推进计划已移至 [00-meta](../00-meta/README.md)：
[02-backlog.md](../00-meta/02-backlog.md)、
[archive/android-platform-progress.md](../00-meta/archive/android-platform-progress.md)、
[platforms/macos.md](../00-meta/platforms/macos.md)。
