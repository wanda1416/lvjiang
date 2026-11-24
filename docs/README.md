# 律匠文档中心

> 本层描述如与代码或配置不符，以代码与配置为准。

按用途分层：使用者看 60-guide，游戏事实看 10-domain，设计与决策看 30-design，接口参考看
40-reference，接入与契约看 70-agent，计划与待办看 20-plans，规范与平台计划看 00-meta，
历史与发布记录看 80-history/50-releases。
目录本身就是生命周期：只有纯计划进 20-plans（未写代码），有概念与实现进 30-design，
定型后把精确契约沉淀到 40-reference；时间与版本以 Git 历史为准，正文不写日期基线。

| 目录 | 内容 | 面向 |
|------|------|------|
| [10-domain/01-yysls/](10-domain/01-yysls/README.md) | 游戏客观机制，以及律匠的调律评价规格 | 用户 / 开发者 |
| [30-design/](30-design/01-architecture/README.md) | 架构与设计：[architecture/](30-design/01-architecture/README.md) 各子区、[decisions/](30-design/02-decisions/README.md) 已实现需求的决策与规格 | 开发者 |
| [40-reference/](40-reference/README.md) | 接口参考：DSL 语法、配置契约、同步与 Profile 数据模型 | 开发者 |
| [20-plans/](20-plans/README.md) | 部分实现功能的计划与边界（原 20-requirements 分流）与[全局待办](20-plans/01-backlog.md) | 开发者 |
| [00-meta/](00-meta/README.md) | 文档规范（[doc-standard.md](00-meta/01-doc-standard.md)）、平台推进计划 | 维护者 |
| [80-history/](80-history/02-dev/README.md) | 开发日志与过程档案（[dev/](80-history/02-dev/README.md) 按月归档）、[路线图快照](80-history/01-roadmap.md)、[meta 归档](80-history/01-meta-archive/README.md) | 开发者 |
| [50-releases/](50-releases/README.md) | 各版本发布说明 | 所有人 |
| [60-guide/](60-guide/README.md) | 安装、配置、运行的完整操作指引（用户指南） | 用户 |
| [70-agent/](70-agent/README.md) | 外部 Agent 接入、发现与业务操作契约 | 用户 / Agent |

## 核心契约

实现行为的权威说明，与代码同步维护；与旧架构文档冲突时以这里为准。

| 契约 | 入口 |
|------|------|
| 插件装配 | [契约 A：插件装配](40-reference/05-plugins/01-contract.md) |
| 配置与迁移 | [契约 B：配置与迁移](40-reference/01-config/01-contract.md)；配套[配置迁移恢复指南](60-guide/05-recovery/01-config-migration.md) |
| Profile 与账户同步 | [契约 C：Profile 与账户同步](40-reference/04-sync/02-profile-and-account.md)；配套[Profile 数据字段模型](40-reference/03-profile/01-data-model.md) |
| 离线安装（PC → Android） | [契约 D：离线安装](40-reference/04-sync/01-offline-install.md) |

## 常用入口

| 我想…… | 去这里 |
|--------|--------|
| 装好就开始用 | [快速开始](60-guide/04-desktop/01-quick-start.md) |
| 搞懂装备怎么被评级 | [调律评价规则](10-domain/01-yysls/10-tuning-rules/README.md) |
| 配置并运行自动调律 | [调律配置总览](60-guide/03-automation/04.01-tuning-config-overview.md) |
| 了解游戏调律/转律/承音机制 | [装备系统](10-domain/01-yysls/01-equipment-system.md) |
| 写工作流脚本 | [脚本工作台与作者须知](60-guide/04-desktop/08-script-authoring.md)、[DSL 语法](40-reference/02-dsl/README.md) |
| 接入外部 Agent | [Agent 入口](70-agent/README.md) |
| 反馈问题 | [问题反馈](60-guide/04-desktop/09-faq-and-feedback.md) |

- [在线配置、会员内容与功能激活](60-guide/01-accounts/01-online-config-and-activation.md)：更新生效、本地覆盖、本地激活与在线会员授权的区别、关闭撤销与人工恢复。
- 构建与打包：[打包与发布手册](../packaging/readme.md)、[Android 构建与部署](../android/DEPLOYMENT.md)、[Android 签名说明](../android/README-signing.md)。
- 旧编号目录（`docs/10-game/`、`docs/20-requirements/`、`docs/30-architecture/`、
  `docs/40-development/`、`docs/50-releases/`、`docs/60-userguide/`、`docs/70-agent/`）与
  中间态无编号目录已一次性切换到上表现行结构，不做旧路径兼容；Agent 文档 ID 同步退役，
  用 list_docs 重新获取现行 ID。
