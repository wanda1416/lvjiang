# 流程：自动调律（Auto Tuning）

> 本文档已迁移至 [20-requirements/01-auto-tuning.md](../../20-requirements/01-auto-tuning.md)。
>
> 该文档包含完整的自动调律设计：Panel 架构、背包遍历策略、指纹模型、调律决策编排、行为处置、模块拆分。

## 快速参考

| 内容 | 位置 |
|------|------|
| Panel 声明式网格架构 | [01 §3](../../20-requirements/01-auto-tuning.md#3-panel-声明式网格架构) |
| 背包遍历策略（Dedup / Positional） | [01 §4](../../20-requirements/01-auto-tuning.md#4-背包遍历策略) |
| 指纹模型 | [01 §5](../../20-requirements/01-auto-tuning.md#5-指纹模型) |
| 调律决策编排 | [01 §6](../../20-requirements/01-auto-tuning.md#6-调律决策编排--状态机三行为点) |
| 模块拆分与职责 | [01 §8](../../20-requirements/01-auto-tuning.md#8-实现架构) |
