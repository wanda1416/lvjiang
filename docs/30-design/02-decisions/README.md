# 决策与规格记录（已实现需求）

> 本层描述如与代码或配置不符，以代码与配置为准。

本表为原 `docs/20-requirements/` 的分流索引：目标已实现的行落在本目录（design/decisions/），
部分实现的行落在 [plans/](../../20-plans/README.md)（仅源码检出）。下表只登记「有哪些篇」，
某一篇的进度与验收状态看它正文首行，不在索引里重复。

## tuning（调律域：自动调律、规则管理、智能调律、转律模拟、赛季与属性基线）

| 文档 | 内容 |
|------|------|
| [01-auto-tuning.md](06-tuning/01-auto-tuning.md) | 自动调律：背包遍历、调律决策编排、行为处置 |
| [02-tuning-management.md](06-tuning/02-tuning-management.md) | 调律管理：实时总览、版本化历史、七天匿名补传 |
| [03-smart-tuning.md](06-tuning/03-smart-tuning.md) | 智能调律：结合备战方案毕业率的二次调律处理与剩余词条推演 |
| [04-transmute-simulation.md](06-tuning/04-transmute-simulation.md) | 模拟转律：八件装备联合转律建议、公共装备目标保存、备战方案第四假设 |
| [05-nanlu-season.md](../../20-plans/01-tuning/05-nanlu-season.md)（仅源码检出） | 南吕相和赛季：115 阶词条范围、赛季承音门槛、评级与毕业率适配 |
| [06-native-attack-affixes.md](06-tuning/06-native-attack-affixes.md) | 本属攻击统一规则表达：用最大/最小本属攻击替代无相与本流派属攻 |
| [07-base-attribute-baseline.md](../../20-plans/01-tuning/07-base-attribute-baseline.md)（仅源码检出） | 115 阶满养成基础属性：按流派与来源选择推导无 40 词条基线 |

## equipment（装备与数据域：备战方案、出装计算、会话数据）

| 文档 | 内容 |
|------|------|
| [01-loadout-management.md](03-equipment/01-loadout-management.md) | 跨用户备战方案管理、显式排序与主页面只读边界 |
| [02-build-calculator.md](03-equipment/02-build-calculator.md) | 出装搭配、角色模拟计算器及总词条合法分配 |
| [03-combat-attribute-sources.md](03-equipment/03-combat-attribute-sources.md) | 当前方案战斗属性来源、逐词条明细与白字黄字对照 |

## batch（批量域：可执行性判定、调度单元、无人值守、状态分层）

| 文档 | 内容 |
|------|------|
| [01-batch-task-eligibility.md](01-batch/01-batch-task-eligibility.md) | 批量任务可执行性：业务 WF 前置判定、整用户跳过与生命周期边界 |
| [02-batch-execution-units.md](01-batch/02-batch-execution-units.md) | 批量调度单元：可见用户、属性聚合、成员选择与运行快照 |
| [03-unattended-batch.md](01-batch/03-unattended-batch.md) | 无人值守批量：弹窗改道与异常恢复工作流 |
| [04-batch-state-layers.md](01-batch/04-batch-state-layers.md) | 批量状态分层：定义 / 运行草稿 / 执行快照 |
| [05-user-task-skip.md](01-batch/05-user-task-skip.md) | 用户 × 任务跳过声明与「工具 → 任务参数」双视图编辑器 |

## profile（Profile 子系统：跨域共享的玩家档案，供调律 / 装备 / 总览共用）

| 文档 | 内容 |
|------|------|
| [README.md](../../20-plans/02-profile/README.md)（仅源码检出） | 玩家档案子系统总览：四模型数据层、范围与实施路线 |
| [01-graduation-rate.md](05-profile/01-graduation-rate.md) | 毕业率分析：角色装备 → 毕业率模型 → 展示 |
| [02-currency-tracking.md](../../20-plans/02-profile/02-currency-tracking.md)（仅源码检出） | 货币追踪：钱袋 OCR → Profile stock 模型 |
| [03-stamina-management.md](../../20-plans/02-profile/03-stamina-management.md)（仅源码检出） | 心力体力管理：资源监控 → 恢复预测 → 超标预警 |
| [04-overview-multi-columns.md](05-profile/04-overview-multi-columns.md) | 数据总览字段多选、批量新增列与列宽保持 |
| [05-history-dialog.md](05-profile/05-history-dialog.md) | 变更记录对话框：三入口统一、筛选与聚合 |
| [06-overview-layout-sync.md](05-profile/06-overview-layout-sync.md) | 页面结构（`profile.json`）独立成文件、手动上传下载与网页只读展示 |

## platform（平台与执行域：多语言、局内导航、输入时间线、执行目标与并发）

| 文档 | 内容 |
|------|------|
| [01-i18n.md](../../20-plans/03-platform/01-i18n.md)（仅源码检出） | 国际化支持框架：tr() 函数、翻译文件、UI 改造 |
| [02-in-match-navigation.md](../../20-plans/03-platform/02-in-match-navigation.md)（仅源码检出） | 局内地图目标闭环导航与撤离：渡尘墟、觉障林等玩法通用 |
| [03-input-timeline.md](04-platform/03-input-timeline.md) | 输入时间线：描述严格并发的操作序列，多路手势与跨端落地 |
| [04-execution-targets.md](04-platform/04-execution-targets.md) | 单窗口、多设备连接与显式执行目标 |
| [05-concurrent-target-execution.md](04-platform/05-concurrent-target-execution.md) | 多执行目标并发任务、断线恢复与 Lv1 授权边界 |
| [06-workflow-capture-reuse.md](04-platform/06-workflow-capture-reuse.md) | WF 显式截图复用、同帧识别与诊断留图 |
| [07-android-offline.md](04-platform/07-android-offline.md) | PC 单向同步与 Android 悬浮控制离线任务 |
| [08-android-task-settings.md](04-platform/08-android-task-settings.md) | 手机按用户编辑任务参数与自动调律设置 |
| [10-ai-connection.md](04-platform/10-ai-connection.md) | AI 连接配置、系统凭据存储与后台连接测试 |
| [09-android-preset.md](04-platform/09-android-preset.md) | 官方预置、手机用户管理与同步参数保留 |
| [12-agent-mcp.md](../../20-plans/03-platform/12-agent-mcp.md)（仅源码检出） | 外部 Agent MCP 接入：发行版能力包、调律配置启动、授权与验收 |
| [13-agent-docs-access.md](../../20-plans/03-platform/13-agent-docs-access.md)（仅源码检出） | Agent 文档逐篇收录、目录拆分与配置文件访问清单 |
| [15-android-workbench.md](04-platform/15-android-workbench.md) | Android 四页工作台、确认交互、执行历史和只读用户数据 |
| [16-android-batch.md](04-platform/16-android-batch.md) | 手机批量配置、运行草稿、悬浮启动与方向过渡 |
| [17-prerelease.md](04-platform/17-prerelease.md) | PEP 440 测试/候选版本与正式升级比较 |
| [18-android-connection-recovery.md](04-platform/18-android-connection-recovery.md) | 代理与 ADB 重连、输入降级及人工恢复边界 |
| [19-write-ownership.md](04-platform/19-write-ownership.md) | 用户执行占用、热键与 MCP 的主实例专属门禁 |
| [20-world-map-catalog.md](../../20-plans/03-platform/20-world-map-catalog.md)（仅源码检出） | 大世界地图与地域目录、探查底图保存与预览、重叠截图拼接 |
| [21-plugin-data.md](04-platform/21-plugin-data.md) | 插件领域数据与同步数据库声明 |
| [22-plugin-runtime.md](04-platform/22-plugin-runtime.md) | 插件宿主运行接口与公共事件契约 |
| [23-service-boundaries.md](04-platform/23-service-boundaries.md) | 公共服务与桌面适配边界 |
| [24-config-policy-bootstrap.md](04-platform/24-config-policy-bootstrap.md) | 插件配置策略显式装配 |
| [25-configuration-modules.md](04-platform/25-configuration-modules.md) | 配置、布局与组合展示职责拆分 |
| [26-online-config-background-download.md](../../20-plans/03-platform/26-online-config-background-download.md)（仅源码检出） | 在线配置后台下载、暂存与下次启动安装 |
| [27-hotkeys-and-menu-shortcuts.md](04-platform/27-hotkeys-and-menu-shortcuts.md) | 键位分区：菜单快捷键、全局热键与插件键位归属 |
| [28-account-document-sync.md](04-platform/28-account-document-sync.md) | 全局任务参数与批量配置上云：账户级文档通道、冲突不留副本与迁移 |
| [29-feedback-channel.md](04-platform/29-feedback-channel.md) | 反馈与建议：客户端内置提交、分类与附件协议、网页管理控制台与管理员名册 |
| [30-account-device-credential.md](04-platform/30-account-device-credential.md) | 设备 ID 作为重置密码凭据：服务端校验、预检接口与两端展示位置 |
| [31-ui-theme-system.md](04-platform/31-ui-theme-system.md) | 桌面端三主题系统（纯白/宣纸/玄墨）、两层单一来源收口与原生标题栏同步 |

## editor（编辑器域：WF 编辑器插件、任务历史、运行态与保存边界）

| 文档 | 内容 |
|------|------|
| [01-wf-editor-plugin.md](02-editor/01-wf-editor-plugin.md) | 工作流 DSL 编辑器插件：语法高亮、实时诊断、语义智能 |
| [02-task-history.md](02-editor/02-task-history.md) | 任务历史：单任务/批量两级 ID、参数与产出查询、独立日志 |
| [03-workflow-metadata-editor.md](02-editor/03-workflow-metadata-editor.md) | 工作流元数据表单与 local / system 保存位置 |
| [04-canvas-group-selection.md](02-editor/04-canvas-group-selection.md) | 画布组选区：Ctrl 多选与整组平移、修饰键语义 |
| [05-runtime-find-area.md](02-editor/05-runtime-find-area.md) | 动态矩形识别区域、命中坐标与相邻文字读取 |
| [06-runtime-drag-hold.md](02-editor/06-runtime-drag-hold.md) | 拖拽保持时长变量、区间与 timeline 校验 |
| [07-dsl-parser-performance.md](02-editor/07-dsl-parser-performance.md) | DSL 解析性能与 AST、错误定位一致性 |
| [08-config-schema-boundaries.md](02-editor/08-config-schema-boundaries.md) | 配置旧入口清理、当前增量与必要迁移边界 |
| [09-content-revisions.md](02-editor/09-content-revisions.md) | 文件修订索引与保存冲突提示 |
| [10-script-groups.md](02-editor/10-script-groups.md) | 脚本展示分组、刷新加载及名称覆盖标记 |
| [11-execution-feedback.md](02-editor/11-execution-feedback.md) | 四类任务入口的就地状态与启动失败提示 |
| [12-parameter-save-boundaries.md](02-editor/12-parameter-save-boundaries.md) | 日常参数字段合并、连续输入保存及运行快照边界 |
