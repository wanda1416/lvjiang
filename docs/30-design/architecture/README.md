# 架构与设计区

> 最后更新：2026-11-18（基线 v0.15.17）。本区描述如与代码或配置不符，以代码与配置为准。

律匠系统的架构设计、分层契约与实现语义。原 `docs/30-design/architecture/` 的各子层按内容
性质迁入本区，DSL 语法参考单列到 [reference/dsl/](../../40-reference/dsl/README.md)。

## 契约

| 契约 | 入口 |
|------|------|
| 契约 A：插件装配 | [plugins.md](plugins.md) |

## 子区索引

| 子区 | 内容 |
|------|------|
| [overview/](overview/README.md) | 架构总览：主窗口状态机、插件系统、毕业率公式模型、配置分层 |
| [models/](models/README.md) | 核心领域模型与数据契约 |
| [engine/](engine/README.md) | DSL 引擎内部机制与静态检查 |
| [scene/](scene/README.md) | 场景、布局语义与编辑器 |
| [workflows/](workflows/README.md) | 跨场景业务流程编排、系统脚本库分层 |
| [graduation/](graduation/README.md) | 毕业率计算引擎 |
| [vision/](vision/README.md) | 视觉感知子系统：四通道选型、图色与模板匹配、坐标一致性、调优与失败模式 |
| [platform/](platform/README.md) | 平台与接入：设备端代理协议、实例执行锁、IO 后端矩阵、匿名统计（其中 [05-ai-connection.md](../../70-agent/05-ai-connection.md) 与 [07-agent-mcp.md](../../70-agent/07-agent-mcp.md) 迁至 agent 层） |
| [runtime/](runtime/README.md) | 运行期能力：调律历史、地图管理与朝向解析、多目标并发执行、出装计算 |

各子区 README 维护完整章节清单，本页只维护子区导航，避免重复清单失步。

## 相关

- 写死在代码里的游戏规则事实（不在 `config/`）统一登记在
  [游戏机制约定登记表](../../10-domain/yysls/06-mechanics-conventions.md)；新增此类常量先登记再写代码。
- DSL 语法、命令与函数的接口参考：[reference/dsl/](../../40-reference/dsl/README.md)。
- 外部 Agent 接入与操作：[docs/70-agent/](../../70-agent/README.md)。
- 已实现需求的决策与规格记录：[design/decisions/](../decisions/README.md)。
