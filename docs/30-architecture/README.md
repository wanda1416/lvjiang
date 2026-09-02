# 架构说明文档（技术层）

律匠系统的技术架构、分层设计、数据流与接口契约。

## 文件索引

| 文件 | 内容 |
|------|------|
| [01-main-window-state-flow.md](01-main-window-state-flow.md) | 主窗口状态机：扫描、定位、执行流程 |
| [02-plugin-system.md](02-plugin-system.md) | 插件系统架构与开发指南 |
| [03-graduation-formula-model.md](03-graduation-formula-model.md) | 毕业率公式模型快速参考 |
| [04-device-agent-protocol.md](04-device-agent-protocol.md) | 设备端代理协议：PC 经 adb forward 连律匠 app，用无障碍/Shizuku 截图与手势（L7 契约） |
| [05-config-layering.md](05-config-layering.md) | 配置分层：system/local/session、合并语义、删除白名单 |
| [06-telemetry.md](06-telemetry.md) | 匿名统计：D1 表结构、写入粒度设计、校验边界、分析查询 |
| [07-tuning-history.md](07-tuning-history.md) | 调律历史：统一结果模型、版本化 SQLite、历史 UI 与七天补传 |

## 子系统索引

| 目录 | 内容 |
|------|------|
| [31-models/](31-models/README.md) | 核心领域模型与数据契约 |
| [32-grammar/](32-grammar/README.md) | 工作流 DSL 语法与语义 |
| [33-engine/](33-engine/README.md) | DSL 引擎内部机制与静态检查 |
| [34-scene/](34-scene/README.md) | 场景、布局语义与编辑器 |
| [35-workflows/](35-workflows/README.md) | 跨场景业务流程编排 |
| [36-graduation/](36-graduation/README.md) | 毕业率计算引擎 |

## 待补充

- `layers/` — 各层详细说明（capture、ocr、input、detector、rules-engine、inventory、workflow）
- `data-flow/` — 各工作流的数据流图
- `interfaces/` — 接口契约
- `extensibility/` — 扩展性设计（添加新流派、新部位、新工作流）
