# 架构说明文档（技术层）

> 最后更新：2026-10-08（基线 v0.13.14）。本层描述如与代码或配置不符，以代码与配置为准。

律匠系统的技术架构、分层设计、数据流与接口契约。

## 子层索引

| 子层 | 内容 |
|------|------|
| [30-overview/](30-overview/README.md) | 架构总览：主窗口状态机、插件系统、毕业率公式模型、配置分层 |
| [31-models/](31-models/README.md) | 核心领域模型与数据契约 |
| [32-grammar/](32-grammar/README.md) | 工作流 DSL 语法与语义 |
| [33-engine/](33-engine/README.md) | DSL 引擎内部机制与静态检查 |
| [34-scene/](34-scene/README.md) | 场景、布局语义与编辑器 |
| [35-workflows/](35-workflows/README.md) | 跨场景业务流程编排 |
| [36-graduation/](36-graduation/README.md) | 毕业率计算引擎 |
| [37-vision/](37-vision/README.md) | 视觉感知子系统：四通道选型、图色与模板匹配、坐标一致性、调优与失败模式 |
| [38-platform/](38-platform/README.md) | 平台与接入：设备端代理协议、实例执行锁、IO 后端矩阵、匿名统计 |
| [39-runtime/](39-runtime/README.md) | 运行期能力：调律历史、地图管理与朝向解析、多目标并发执行、出装计算 |

各子层 README 维护完整章节清单，本页只维护子层导航，避免重复清单失步。

## 相关

- 写死在代码里的游戏规则事实（不在 `config/`）统一登记在
  [../10-game/06-mechanics-conventions.md](../10-game/06-mechanics-conventions.md)；新增此类常量先登记再写代码。
