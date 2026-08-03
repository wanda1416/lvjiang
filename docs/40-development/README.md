# 开发日志（过程层）

按时间记录开发过程中的重要决策、问题排查、实验记录。

## 目录结构

- `YYYY-MM/` — 按月分组的开发日志
- `milestones/` — 里程碑总结
- `experiments/` — 实验记录（含失败尝试）

## 已有日志

### 2026-08

| 日期 | 文件 | 主题 |
|------|------|------|
| 2026-08-03 | [2026-08-03-screenshot-ocr-canvas-v011.md](2026-08/2026-08-03-screenshot-ocr-canvas-v011.md) | screenshot 指令 + OCR 画布可视化 + v0.1.1 发布 |
| 2026-08-02 | [2026-08-02-scene-editor-batch-dsl-ocr.md](2026-08/2026-08-02-scene-editor-batch-dsl-ocr.md) | 场景编辑器增强 + 批处理完善 + DSL 语法扩展 + OCR 清洗架构 |
| 2026-08-01 | [2026-08-01-layout-split-batch-mvp-v010.md](2026-08/2026-08-01-layout-split-batch-mvp-v010.md) | 布局拆分 + 批处理 MVP + 调律冷却期 + v0.1.0 发布 |

### 2026-07

| 日期 | 文件 | 主题 |
|------|------|------|
| 2026-07-31 | [2026-07-31-dsl-docs-reorg-tuning-rules-platform.md](2026-07/2026-07-31-dsl-docs-reorg-tuning-rules-platform.md) | DSL 文档重组 + 调律规则模型大重构 + 平台适配层 + macOS 支持 |
| 2026-07-30 | [2026-07-30-tuning-materials-and-crash-fix.md](2026-07/2026-07-30-tuning-materials-and-crash-fix.md) | 属攻词条双重身份 + 调律材料策略 + 狗粮规则引擎 + 退出崩溃根治 |
| 2026-07-26 | [2026-07-26-config-engine-and-attrs.md](2026-07/2026-07-26-config-engine-and-attrs.md) | 配置引擎 + 属性管理 |
| 2026-07-26 | [2026-07-26-tuning-switch-refactor.md](2026-07/2026-07-26-tuning-switch-refactor.md) | 调律切换重构 |
| 2026-07-25 | [2026-07-25-tuning-school-exhaustive-match.md](2026-07/2026-07-25-tuning-school-exhaustive-match.md) | 调律流派判定体系重构（穷举匹配制） |
| 2026-07-24 | [2026-07-24-plugin-arch-references-docs.md](2026-07/2026-07-24-plugin-arch-references-docs.md) | 单包插件化架构重构 + 参考图管理 + 文档结构重构 |
| 2026-07-23 | [2026-07-23-equip-parser-ui-layout.md](2026-07/2026-07-23-equip-parser-ui-layout.md) | 装备解析与 UI 布局优化 |
| 2026-07-22 | [2026-07-22-tuning-rules-standardization.md](2026-07/2026-07-22-tuning-rules-standardization.md) | 调律规则标准化 |
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
