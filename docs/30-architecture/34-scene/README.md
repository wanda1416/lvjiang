# 场景系统

游戏场景的语义模型定义与编辑器操作手册。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-scene-layout-definition.md](01-scene-layout-definition.md) | 场景与布局语义模型：Scene / Area / Layout 三层数据结构定义 |
| [02-scene-layout-editing.md](02-scene-layout-editing.md) | 场景编辑器操作手册：可视化编辑、网格校准、脚本调试 |
| [03-cross-scene-references.md](03-cross-scene-references.md) | 跨场景 area 引用：一块区域只留一处坐标真源，与子场景嵌套的区别 |
| [04-page-transition-contract.md](04-page-transition-contract.md) | 页面切换契约：多视图归属与 to: 转移声明，死视图检测 |

## 关联文档

- **场景实现**：[31-models/02-scene-implementations.md](../31-models/02-scene-implementations.md) — 各场景具体字段定义
- **DSL 交互指令**：[32-grammar/02-concepts.md](../32-grammar/02-concepts.md)（概念）/ [03.3-mouse.md](../32-grammar/03.3-mouse.md)（指令） — click/drag/wait 等指令的场景寻址
- **架构总览**：[30-architecture/README.md](../README.md)
