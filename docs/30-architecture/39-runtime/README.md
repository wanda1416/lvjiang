# 运行期能力

> 最后更新：2026-10-07（基线 v0.13.13）。本层描述如与代码或配置不符，以代码与配置为准。

调律历史、地图与朝向解析、多目标并发执行、出装计算、无人值守恢复与批量状态分层。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-tuning-history.md](01-tuning-history.md) | 调律历史：统一结果模型、版本化 SQLite、历史 UI 与七天补传 |
| [02-maps.md](02-maps.md) | 地图定义与地图管理：世界系 POI、自带 HUD 场景、小地图朝向解析、管理对话框 |
| [03-concurrent-execution.md](03-concurrent-execution.md) | 多目标并发执行：运行实例、目标资源重绑定、日志与调律多页隔离 |
| [04-build-calculator.md](04-build-calculator.md) | 出装搭配独立存储、总词条分配与角色模拟状态边界 |
| [05-unattended-recovery.md](05-unattended-recovery.md) | 无人值守的拦截点、调度器注入信号与配置兜底 |
| [06-batch-state-layers.md](06-batch-state-layers.md) | 批量四层状态的数据归属、协调算法与 `batch.json` v2 迁移 |
