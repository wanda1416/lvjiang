# 律匠文档中心

> 最后更新：2026-10-09（基线 v0.13.14）。本层描述如与代码或配置不符，以代码与配置为准。

按用途分为八层：使用者看 60，需求与规格看 10/20，实现细节看 30，过程记录看 40/50，
文档规范与待办看 00。

| 目录 | 内容 | 面向 |
|------|------|------|
| [00-meta/](00-meta/README.md) | 文档规范（[00-doc-standard.md](00-meta/00-doc-standard.md)）、路线图、待办、平台推进计划 | 维护者 |
| [10-game/](10-game/README.md) | 游戏客观机制，以及律匠的调律评价规格 | 用户 / 开发者 |
| [20-requirements/](20-requirements/README.md) | 各功能的需求定义与实施状态 | 开发者 |
| [30-architecture/](30-architecture/README.md) | 数据模型、DSL 语法、引擎、场景、工作流、毕业率 | 开发者 |
| [40-development/](40-development/README.md) | 按日期归档的开发日志与技术参考 | 开发者 |
| [50-releases/](50-releases/README.md) | 各版本发布说明 | 所有人 |
| [guide/](guide/README.md) | 安装、配置、运行的完整操作指引（用户指南） | 用户 |
| [60-userguide/](60-userguide/README.md) | 旧用户指南路径的兼容页（正文已迁至 guide/） | 用户 |
| [70-agent/](70-agent/README.md) | 外部 Agent 接入、发现与业务操作契约 | 用户 / Agent |

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
| 搞懂装备怎么被评级 | [调律评价规则](10-game/10-tuning-rules/README.md) |
| 配置并运行自动调律 | [调律配置总览](guide/automation/04.01-tuning-config-overview.md) |
| 了解游戏调律/转律/承音机制 | [装备系统](10-game/01-equipment-system.md) |
| 写工作流脚本 | [脚本工作台与作者须知](guide/desktop/08-script-authoring.md)、[DSL 语法](30-architecture/32-grammar/README.md) |
| 反馈问题 | [问题反馈](guide/desktop/09-faq-and-feedback.md) |

- [在线配置、会员内容与功能激活](guide/accounts/appendix-a-online-config.md)：更新生效、本地覆盖、本地激活与在线会员授权的区别、关闭撤销与人工恢复。
- 旧路径 `docs/60-userguide/` 下的链接已迁移，旧地址由 [60-userguide 兼容页](60-userguide/README.md)承接。
