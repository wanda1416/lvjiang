# 流程编排（Workflows）

流程编排描述如何组合多个场景来完成一个完整的业务目标。

## 与场景的关系

- **场景**（`docs/30-architecture/scene-region-editing.md`）：定义单个游戏界面的截图区域和字段
- **流程**：编排多个场景的执行顺序，完成复杂任务

## 已有流程

| 编号 | 文件 | 主题 | 依赖场景 |
|------|------|------|----------|
| 01 | [01-current-equip-analysis.md](01-current-equip-analysis.md) | 用户当前装备分析 | bag_equip_detail, equip_weapon_detail, equip_armor_detail |

## 流程文档模板

```markdown
# 流程：<名称>

## 目标
<流程要完成什么>

## 依赖场景
<需要哪些场景的区域定义>

## 执行步骤
<具体步骤，包括点击、截图、识别、判断>

## 输出
<流程产出什么数据或结果>
```
