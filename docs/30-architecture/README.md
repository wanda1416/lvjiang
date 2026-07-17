# 架构说明文档（技术层）

律匠系统的技术架构、分层设计、数据流与接口契约。

## 文件索引

| 文件 | 内容 |
|------|------|
| [main-window-state-flow.md](main-window-state-flow.md) | 主窗口状态机：扫描、定位、执行流程 |
| [scene-layout-definition.md](scene-layout-definition.md) | 场景与布局：语义模型定义（Scene / Area / Layout） |
| [scene-layout-editing.md](scene-layout-editing.md) | 场景与布局：编辑器操作手册 |

DSL 语法文档已拆分至 [`../32-grammar/`](../32-grammar/README.md)。

## 待补充

- `layers/` — 各层详细说明（capture、ocr、input、detector、rules-engine、inventory、workflow）
- `data-flow/` — 各工作流的数据流图
- `interfaces/` — 接口契约
- `extensibility/` — 扩展性设计（添加新流派、新部位、新工作流）
