# 33-engine — 引擎内部机制

本目录记录 DSL 指令在引擎内部的实现机制,面向需要理解"指令背后到底发生了什么"的开发者。

与 `32-grammar` 的区别:
- `32-grammar` 讲**语法与语义**(用户视角,DSL 怎么写、有什么效果)
- `33-engine` 讲**数据流与代价**(实现视角,指令如何调度截屏/识别/坐标解析)

## 目录

| 文档 | 主题 |
|------|------|
| [screenshot-and-crop.md](screenshot-and-crop.md) | `scan` / `recognize` 的截图-裁剪-识别数据流,以及 DSL 写法对截屏次数的影响 |

## 阅读建议

- 写工作流 DSL 前先看 `32-grammar`,了解语法
- 写性能敏感的工作流(循环内多次识别)时再看 `33-engine`,避免无谓的重复截图
- 调试坐标解析(`click [scene].$key` 找不到坐标)时,看 `screenshot-and-crop.md` 的 `_coord_meta` 一节
