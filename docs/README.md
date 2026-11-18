# 律匠文档中心

> 最后更新：2026-11-18（基线 v0.15.17）。本层描述如与代码或配置不符，以代码与配置为准。

按用途分层：使用者看 guide，游戏事实看 domain，设计与决策看 design，接口参考看
reference，接入与契约看 agent，计划与待办看 plans，规范与平台计划看 meta，历史与
发布记录看 history/releases。

| 目录 | 内容 | 面向 |
|------|------|------|
| [domain/yysls/](domain/yysls/README.md) | 游戏客观机制，以及律匠的调律评价规格 | 用户 / 开发者 |
| [design/](design/architecture/README.md) | 架构与设计：[architecture/](design/architecture/README.md) 各子区、[decisions/](design/decisions/README.md) 已实现需求的决策与规格 | 开发者 |
| [reference/](reference/README.md) | 接口参考：DSL 语法、配置契约、同步与 Profile 数据模型 | 开发者 |
| [plans/](plans/README.md) | 部分实现功能的计划与边界（原 20-requirements 分流）与[全局待办](plans/backlog.md) | 开发者 |
| [meta/](meta/README.md) | 文档规范（[doc-standard.md](meta/doc-standard.md)）、平台推进计划 | 维护者 |
| [history/](history/dev/README.md) | 开发日志与过程档案（[dev/](history/dev/README.md) 按月归档）、[路线图快照](history/roadmap.md)、[meta 归档](history/meta-archive/README.md) | 开发者 |
| [releases/](releases/README.md) | 各版本发布说明 | 所有人 |
| [guide/](guide/README.md) | 安装、配置、运行的完整操作指引（用户指南） | 用户 |
| [agent/](agent/README.md) | 外部 Agent 接入、发现与业务操作契约 | 用户 / Agent |
| [develop/](develop/README.md) | 开发者入口：插件开发、打包、Android 部署 | 开发者 |

## 兼容页（旧路径入口）

| 旧目录 | 现状 |
|--------|------|
| [10-game/](10-game/README.md) | 兼容页，正文已迁至 domain/yysls/ |
| [20-requirements/](20-requirements/README.md) | 兼容页，已按状态分流至 design/decisions/ 与 plans/ |
| [30-architecture/](30-architecture/README.md) | 兼容页，已拆分至 design/architecture/、reference/dsl/、agent/ |
| [40-development/](40-development/README.md) | 兼容页，正文已迁至 history/dev/ |
| [50-releases/](50-releases/README.md) | 兼容页，正文已迁至 releases/ |
| [00-meta/](00-meta/README.md) | 兼容页，已拆分至 meta/、plans/backlog、history/roadmap 与 history/meta-archive/ |
| [60-userguide/](60-userguide/README.md) | 兼容页，正文已迁至 guide/ |
| [70-agent/](70-agent/README.md) | 兼容页，正文已迁至 agent/ |

## 核心契约

实现行为的权威说明，与代码同步维护；与旧架构文档冲突时以这里为准。

| 契约 | 入口 |
|------|------|
| 插件装配 | [契约 A：插件装配](design/architecture/plugins.md) |
| 配置与迁移 | [契约 B：配置与迁移](reference/config/contract.md)；配套[配置迁移恢复指南](guide/recovery/config-migration.md) |
| Profile 与账户同步 | [契约 C：Profile 与账户同步](reference/sync/profile-and-account.md)；配套[Profile 数据字段模型](reference/profile/data-model.md) |
| 离线安装（PC → Android） | [契约 D：离线安装](reference/sync/offline-install.md) |

## 常用入口

| 我想…… | 去这里 |
|--------|--------|
| 装好就开始用 | [快速开始](guide/desktop/01-quick-start.md) |
| 搞懂装备怎么被评级 | [调律评价规则](domain/yysls/10-tuning-rules/README.md) |
| 配置并运行自动调律 | [调律配置总览](guide/automation/04.01-tuning-config-overview.md) |
| 了解游戏调律/转律/承音机制 | [装备系统](domain/yysls/01-equipment-system.md) |
| 写工作流脚本 | [脚本工作台与作者须知](guide/desktop/08-script-authoring.md)、[DSL 语法](reference/dsl/README.md) |
| 接入外部 Agent | [Agent 入口](agent/README.md) |
| 反馈问题 | [问题反馈](guide/desktop/09-faq-and-feedback.md) |

- [在线配置、会员内容与功能激活](guide/accounts/appendix-a-online-config.md)：更新生效、本地覆盖、本地激活与在线会员授权的区别、关闭撤销与人工恢复。
- 旧路径 `docs/60-userguide/` 下的链接已迁移，旧地址由 [60-userguide 兼容页](60-userguide/README.md)承接；
  `docs/domain/yysls/`、`docs/design/architecture/`、`docs/agent/`、`docs/20-requirements/` 同理，见上方兼容页表。
