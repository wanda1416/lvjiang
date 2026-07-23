# 开发日志（过程层）

按时间记录开发过程中的重要决策、问题排查、实验记录。

## 目录结构

- `YYYY-MM/` — 按月分组的开发日志
- `milestones/` — 里程碑总结
- `experiments/` — 实验记录（含失败尝试）

## 已有日志

### 2026-07

| 日期 | 文件 | 主题 |
|------|------|------|
| 2026-07-21 | [2026-07-21-project-evolution-summary.md](2026-07/2026-07-21-project-evolution-summary.md) | 项目演进总结（Phase 0~8 + 用户指令索引） |
| 2026-07-16 | [2026-07-16-workflow-stability-and-evaluation.md](2026-07/2026-07-16-workflow-stability-and-evaluation.md) | 工作流稳定性与评估体系闭环 |
| 2026-07-16 | [2026-07-16-scene-grouping.md](2026-07/2026-07-16-scene-grouping.md) | 场景分组管理功能 |
| 2026-07-16 | [2026-07-16-dsl-refactor-and-crash-protection.md](2026-07/2026-07-16-dsl-refactor-and-crash-protection.md) | DSL 去隐式化重构与崩溃防护体系 |
| 2026-07-16 | [2026-07-16-dsl-points-arrows-next-step.md](2026-07/2026-07-16-dsl-points-arrows-next-step.md) | DSL 消费 points/arrows 改造方案 |
| 2026-07-15 | [2026-07-15-scene-yaml-externalization.md](2026-07/2026-07-15-scene-yaml-externalization.md) | 场景定义 YAML 外部化、SceneRegistry、加载顺序控制 |
| 2026-07-15 | [2026-07-15-scene-class-refactor.md](2026-07/2026-07-15-scene-class-refactor.md) | 装备场景类化重构、功能按钮标记、区域复制修复 |
| 2026-07-15 | [2026-07-15-architecture-refactor.md](2026-07/2026-07-15-architecture-refactor.md) | 架构文档重构、场景标识符规范化、布局顺序管理 |
| 2026-07-15 | [2026-07-15-canvas-middle-layer.md](2026-07/2026-07-15-canvas-middle-layer.md) | 画布中间层坐标解耦 |
| 2026-07-14 | [2026-07-14-equip-model-dedup-slot.md](2026-07/2026-07-14-equip-model-dedup-slot.md) | 装备模型去 slot 依赖全链路重构 |
| 2026-07-14 | [2026-07-14-overlay-dpi.md](2026-07/2026-07-14-overlay-dpi.md) | BorderOverlay 多 DPI 跨屏定位问题 |
| 2026-07-14 | [2026-07-14-layout-hierarchy-refactor.md](2026-07/2026-07-14-layout-hierarchy-refactor.md) | 区域配置布局层级重构 |

## 日志格式

```markdown
# Dev Log: <标题>
> 日期：YYYY-MM-DD
> 涉及模块：<路径>
> 关键词：<关键词>
## 问题描述
## 排查过程
## 根因分析
## 解决方案
## 教训
```
