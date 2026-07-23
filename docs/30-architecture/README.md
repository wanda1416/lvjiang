# 架构说明文档（技术层）

律匠系统的技术架构、分层设计、数据流与接口契约。

## 文件索引

| 文件 | 内容 |
|------|------|
| [01-main-window-state-flow.md](01-main-window-state-flow.md) | 主窗口状态机：扫描、定位、执行流程 |
| [02-plugin-system.md](02-plugin-system.md) | 插件系统架构与开发指南 |

> 场景系统文档已独立至 [34-scene/](../34-scene/) 目录。

DSL 语法文档已拆分至 [`../32-grammar/`](../32-grammar/README.md)。

## 待补充

- `layers/` — 各层详细说明（capture、ocr、input、detector、rules-engine、inventory、workflow）
- `data-flow/` — 各工作流的数据流图
- `interfaces/` — 接口契约
- `extensibility/` — 扩展性设计（添加新流派、新部位、新工作流）
