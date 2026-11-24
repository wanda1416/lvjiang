# 功能规划与部分实现边界（plans）

> 本层描述如与代码或配置不符，以代码与配置为准。

本目录只收**还没开始写代码**的纯计划：调研、提案、方案对比与全局待办。开始落地后把文档
`git mv` 进 [design/](../30-design/01-architecture/README.md) 继续演进，定型后再沉淀契约到
[reference/](../40-reference/README.md)。收录边界见[文档规范](../00-meta/01-doc-standard.md)；
下表只登记「有哪些篇」，某篇的进度看它正文首行，不在索引里重复。

## 全局待办（原 00-meta/02-backlog.md 迁入）

| 文档 | 内容 |
|------|------|
| [backlog.md](01-backlog.md) | 唯一待办来源：仍然有效的功能待办与 P1/P2 详情 |

## tuning（调律域：自动调律、规则管理、智能调律、转律模拟、赛季与属性基线）

| 文档 | 内容 |
|------|------|
| [01-auto-tuning.md](../30-design/02-decisions/06-tuning/01-auto-tuning.md) | 自动调律：背包遍历、调律决策编排、行为处置 |
| [02-tuning-management.md](../30-design/02-decisions/06-tuning/02-tuning-management.md) | 调律管理：实时总览、版本化历史、七天匿名补传 |
| [03-smart-tuning.md](../30-design/02-decisions/06-tuning/03-smart-tuning.md) | 智能调律：结合备战方案毕业率的二次调律处理与剩余词条推演 |
| [04-transmute-simulation.md](../30-design/02-decisions/06-tuning/04-transmute-simulation.md) | 模拟转律：八件装备联合转律建议、公共装备目标保存、备战方案第四假设 |
| [05-nanlu-season.md](../30-design/02-decisions/06-tuning/05-nanlu-season.md) | 南吕相和赛季：115 阶词条范围、赛季承音门槛、评级与毕业率适配 |
| [06-native-attack-affixes.md](../30-design/02-decisions/06-tuning/06-native-attack-affixes.md) | 本属攻击统一规则表达：用最大/最小本属攻击替代无相与本流派属攻 |
| [07-base-attribute-baseline.md](01-tuning/07-base-attribute-baseline.md) | 115 阶满养成基础属性：按流派与来源选择推导无 40 词条基线 |

## profile（Profile 子系统：跨域共享的玩家档案，供调律 / 装备 / 总览共用）

| 文档 | 内容 |
|------|------|
| [README.md](02-profile/README.md) | 玩家档案子系统总览：四模型数据层、范围与实施路线 |
| [01-graduation-rate.md](../30-design/02-decisions/05-profile/01-graduation-rate.md) | 毕业率分析：角色装备 → 毕业率模型 → 展示 |
| [02-currency-tracking.md](02-profile/02-currency-tracking.md) | 货币追踪：钱袋 OCR → Profile stock 模型 |
| [03-stamina-management.md](02-profile/03-stamina-management.md) | 心力体力管理：资源监控 → 恢复预测 → 超标预警 |
| [04-overview-multi-columns.md](../30-design/02-decisions/05-profile/04-overview-multi-columns.md) | 数据总览字段多选、批量新增列与列宽保持 |
| [05-history-dialog.md](../30-design/02-decisions/05-profile/05-history-dialog.md) | 变更记录对话框：三入口统一、筛选与聚合 |
| [06-overview-layout-sync.md](../30-design/02-decisions/05-profile/06-overview-layout-sync.md) | 页面结构（`profile.json`）独立成文件、手动上传下载与网页只读展示 |

## platform（平台与执行域：多语言、局内导航、输入时间线、执行目标与并发）

| 文档 | 内容 |
|------|------|
| [01-i18n.md](03-platform/01-i18n.md) | 国际化支持框架：tr() 函数、翻译文件、UI 改造 |
| [02-in-match-navigation.md](03-platform/02-in-match-navigation.md) | 局内地图目标闭环导航与撤离：渡尘墟、觉障林等玩法通用 |
| [03-input-timeline.md](../30-design/02-decisions/04-platform/03-input-timeline.md) | 输入时间线：描述严格并发的操作序列，多路手势与跨端落地 |
| [04-execution-targets.md](../30-design/02-decisions/04-platform/04-execution-targets.md) | 单窗口、多设备连接与显式执行目标 |
| [05-concurrent-target-execution.md](../30-design/02-decisions/04-platform/05-concurrent-target-execution.md) | 多执行目标并发任务、断线恢复与 Lv1 授权边界 |
| [06-workflow-capture-reuse.md](../30-design/02-decisions/04-platform/06-workflow-capture-reuse.md) | WF 显式截图复用、同帧识别与诊断留图 |
| [07-android-offline.md](../30-design/02-decisions/04-platform/07-android-offline.md) | PC 单向同步与 Android 悬浮控制离线任务 |
| [08-android-task-settings.md](../30-design/02-decisions/04-platform/08-android-task-settings.md) | 手机按用户编辑任务参数与自动调律设置 |
| [10-ai-connection.md](../30-design/02-decisions/04-platform/10-ai-connection.md) | AI 连接配置、系统凭据存储与后台连接测试 |
| [09-android-preset.md](../30-design/02-decisions/04-platform/09-android-preset.md) | 官方预置、手机用户管理与同步参数保留 |
| [12-agent-mcp.md](03-platform/12-agent-mcp.md) | 外部 Agent MCP 接入：发行版能力包、调律配置启动、授权与验收 |
| [13-agent-docs-access.md](03-platform/13-agent-docs-access.md) | Agent 文档逐篇收录、目录拆分与配置文件访问清单 |
| [15-android-workbench.md](../30-design/02-decisions/04-platform/15-android-workbench.md) | Android 四页工作台、确认交互、执行历史和只读用户数据 |
| [16-android-batch.md](../30-design/02-decisions/04-platform/16-android-batch.md) | 手机批量配置、运行草稿、悬浮启动与方向过渡 |
| [17-prerelease.md](../30-design/02-decisions/04-platform/17-prerelease.md) | PEP 440 测试/候选版本与正式升级比较 |
| [18-android-connection-recovery.md](../30-design/02-decisions/04-platform/18-android-connection-recovery.md) | 代理与 ADB 重连、输入降级及人工恢复边界 |
| [19-write-ownership.md](../30-design/02-decisions/04-platform/19-write-ownership.md) | 用户执行占用、热键与 MCP 的主实例专属门禁 |
| [20-world-map-catalog.md](03-platform/20-world-map-catalog.md) | 大世界地图与地域目录、探查底图保存与预览、重叠截图拼接 |
| [21-plugin-data.md](../30-design/02-decisions/04-platform/21-plugin-data.md) | 插件领域数据与同步数据库声明 |
| [22-plugin-runtime.md](../30-design/02-decisions/04-platform/22-plugin-runtime.md) | 插件宿主运行接口与公共事件契约 |
| [23-service-boundaries.md](../30-design/02-decisions/04-platform/23-service-boundaries.md) | 公共服务与桌面适配边界 |
| [24-config-policy-bootstrap.md](../30-design/02-decisions/04-platform/24-config-policy-bootstrap.md) | 插件配置策略显式装配 |
| [25-configuration-modules.md](../30-design/02-decisions/04-platform/25-configuration-modules.md) | 配置、布局与组合展示职责拆分 |
| [26-online-config-background-download.md](03-platform/26-online-config-background-download.md) | 在线配置后台下载、暂存与下次启动安装 |
| [27-hotkeys-and-menu-shortcuts.md](../30-design/02-decisions/04-platform/27-hotkeys-and-menu-shortcuts.md) | 键位分区：菜单快捷键、全局热键与插件键位归属 |
| [28-account-document-sync.md](../30-design/02-decisions/04-platform/28-account-document-sync.md) | 全局任务参数与批量配置上云：账户级文档通道、冲突不留副本与迁移 |
| [29-ai-exception-recovery.md](03-platform/29-ai-exception-recovery.md) | AI 异常导航恢复提案：有限动作、检查点续跑与独立验收；未实施 |
| [30-debug-frame-history.md](03-platform/30-debug-frame-history.md) | 识别帧历史诊断 FIFO、异常冻结包与反馈附件上传 |
| [31-wf-simulation-hardening.md](03-platform/31-wf-simulation-hardening.md) | 离线WF模拟的回归门禁增强：无人值守弹窗门禁、状态覆盖断言、失败Diff、OCR缓存与宽容匹配档 |
| [32-desktop-ui-polish.md](03-platform/32-desktop-ui-polish.md) | 桌面端高密度 UI 微规范收口与可读性改进：真机巡检、文字减压、高 DPI 弹性与横幅/弹窗收敛 |
