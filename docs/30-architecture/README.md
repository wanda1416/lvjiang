# 架构说明文档（技术层）

> 最后更新：2026-10-07（基线 v0.13.13）。本层描述如与代码或配置不符，以代码与配置为准。

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

## 子层内文件

### 30-overview（架构总览）

| 文件 | 内容 |
|------|------|
| [01-main-window-state-flow.md](30-overview/01-main-window-state-flow.md) | 主窗口状态机：扫描、定位、执行流程 |
| [02-plugin-system.md](30-overview/02-plugin-system.md) | 插件系统架构与开发指南 |
| [03-graduation-formula-model.md](30-overview/03-graduation-formula-model.md) | 毕业率公式模型（导航至 36-graduation） |
| [04-config-layering.md](30-overview/04-config-layering.md) | 配置分层：system/remote/local/session、合并语义、删除白名单 |

### 38-platform（平台与接入）

| 文件 | 内容 |
|------|------|
| [01-device-agent-protocol.md](38-platform/01-device-agent-protocol.md) | 设备端代理协议：PC 经 adb forward 连律匠 app，用无障碍/Shizuku 截图与手势（L7 契约） |
| [02-instance-access.md](38-platform/02-instance-access.md) | 多实例执行锁与用户数据访问边界 |
| [03-io-backends.md](38-platform/03-io-backends.md) | 截图与输入后端矩阵：PC 前台/后台、Android a11y/Shizuku/ADB、scrcpy 的能力与限制 |
| [04-telemetry.md](38-platform/04-telemetry.md) | 匿名统计：D1 表结构、写入粒度设计、校验边界、分析查询 |
| [05-ai-connection.md](38-platform/05-ai-connection.md) | AI 连接参数、系统凭据库、可取消文本请求与设置页边界 |

### 39-runtime（运行期能力）

| 文件 | 内容 |
|------|------|
| [01-tuning-history.md](39-runtime/01-tuning-history.md) | 调律历史：统一结果模型、版本化 SQLite、历史 UI 与七天补传 |
| [02-maps.md](39-runtime/02-maps.md) | 地图定义与地图管理：世界系 POI、自带 HUD 场景、小地图朝向解析、管理对话框 |
| [03-concurrent-execution.md](39-runtime/03-concurrent-execution.md) | 多目标并发执行：运行实例、目标资源重绑定、日志与调律多页隔离 |
| [04-build-calculator.md](39-runtime/04-build-calculator.md) | 出装搭配独立存储、总词条分配与角色模拟状态边界 |

其余子层（31-models ~ 37-vision）的文件清单见各自 README。

## 相关

- 写死在代码里的游戏规则事实（不在 `config/`）统一登记在
  [../10-game/06-mechanics-conventions.md](../10-game/06-mechanics-conventions.md)；新增此类常量先登记再写代码。
