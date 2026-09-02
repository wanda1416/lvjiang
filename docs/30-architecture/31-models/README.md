# 数据模型

核心领域对象的结构化定义，作为各模块间的共同数据契约。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-equipment-models.md](01-equipment-models.md) | 装备领域模型：OCR 识别数据结构、词条清洗、品阶推断 |
| [02-scene-implementations.md](02-scene-implementations.md) | 场景实现定义：九个场景的具体字段（Area / Region）配置 |
| [03-session-and-context.md](03-session-and-context.md) | Session 与 Context 数据模型：持久状态与运行时上下文 |
| [04-profile.md](04-profile.md) | Profile 共享模块：存储契约、周期扩展与 UI 边界 |
| [05-martial-arts-and-playstyles.md](05-martial-arts-and-playstyles.md) | 武学/流派/玩法三层：玩法决定调律方向，流派决定毕业率 |

## 关联文档

- **语义模型**：[34-scene/01-scene-layout-definition.md](../34-scene/01-scene-layout-definition.md) — Scene / Area / Layout 三层数据结构
- **游戏规则**：[10-game/01-equipment-system.md](../../10-game/01-equipment-system.md) — 装备系统玩法机制
- **DSL 语法**：[32-grammar/README.md](../32-grammar/README.md) — 工作流 DSL 如何引用这些模型
