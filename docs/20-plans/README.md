# 功能规划与部分实现边界（plans）

> 本层描述如与代码或配置不符，以代码与配置为准。

本目录只收**还没开始写代码**的纯计划：调研、提案、方案对比与全局待办。开始落地后把文档
`git mv` 进 [design/](../30-design/01-architecture/README.md) 继续演进，定型后再沉淀契约到
[reference/](../40-reference/README.md)。收录边界见[文档规范](../00-meta/01-doc-standard.md)。

一篇计划一个文件，不再按领域分子目录；领域只在「领域」列里标注，避免同一篇文档在多层
索引里被重复登记。某篇的进度看它正文首行，不在索引里重复；已实现的方案见
[设计决策索引](../30-design/02-decisions/README.md)。

| 文档 | 领域 | 内容 |
|------|------|------|
| [01-backlog.md](01-backlog.md) | 全局待办 | 唯一待办来源：仍然有效的功能待办与 P1/P2 详情 |
| [02-base-attribute-baseline.md](02-base-attribute-baseline.md) | 调律 | 115 阶满养成基础属性：按流派与来源选择推导无 40 词条基线 |
| [03-profile-overview.md](03-profile-overview.md) | 档案 | 用户 Profile 系统需求总览：四模型数据层、范围与实施路线 |
| [04-currency-tracking.md](04-currency-tracking.md) | 档案 | 货币追踪：钱袋 OCR → Profile stock 模型 |
| [05-stamina-management.md](05-stamina-management.md) | 档案 | 心力体力管理：资源监控 → 恢复预测 → 超标预警 |
| [06-linger-checklist.md](06-linger-checklist.md) | 档案 | 袅袅之音清单：条目记录、周期读取与可选入账 |
| [07-i18n.md](07-i18n.md) | 平台 | 国际化支持框架：tr() 函数、翻译文件、UI 改造 |
| [08-in-match-navigation.md](08-in-match-navigation.md) | 平台 | 局内地图目标闭环导航与撤离：渡尘墟、觉障林等玩法通用 |
| [09-agent-mcp.md](09-agent-mcp.md) | 平台 | 外部 Agent MCP 接入：发行版能力包、调律配置启动、授权与验收 |
| [10-agent-docs-access.md](10-agent-docs-access.md) | 平台 | Agent 文档逐篇收录、目录拆分与配置文件访问清单 |
| [11-world-map-catalog.md](11-world-map-catalog.md) | 平台 | 大世界地图与地域目录、探查底图保存与预览、重叠截图拼接 |
| [12-ai-exception-recovery.md](12-ai-exception-recovery.md) | 平台 | AI 异常导航恢复：有限动作、检查点续跑与独立验收 |
| [13-debug-frame-history.md](13-debug-frame-history.md) | 平台 | 识别帧历史诊断 FIFO、异常冻结包与反馈附件上传 |
| [14-wf-simulation-hardening.md](14-wf-simulation-hardening.md) | 平台 | 离线 WF 模拟的回归门禁增强：无人值守弹窗门禁、状态覆盖断言、失败 Diff、OCR 缓存 |
| [15-desktop-ui-polish.md](15-desktop-ui-polish.md) | 平台 | 桌面端高密度 UI 收口：真机巡检、文字减压、高 DPI 弹性与横幅/弹窗收敛 |
