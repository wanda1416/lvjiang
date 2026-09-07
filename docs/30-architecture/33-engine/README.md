# 33-engine — 引擎内部机制

本目录记录 DSL 指令在引擎内部的实现机制,面向需要理解"指令背后到底发生了什么"的开发者。

## 工作流程引擎与 DSL 层

两者不是两套并列的执行引擎：

- **WorkflowEngine** 是运行时核心，持有输入、截图、布局、参考图服务、
  session/context 和运行期缓存，并定义 `click`、`scan`、`align`、
  `drag`、`press` 等公共原语。
- **DSL 层**是语法适配层：把 `.wf` 文本解析成 AST，解析变量和参数，
  然后调用 WorkflowEngine 的公共原语。`_exec_*` 方法不应再实现一套
  独立的点击、识别或坐标算法。
- **BaseWorkflow** 是 Python 业务流的便捷门面。它不是另一个引擎；业务方法
  通过该门面调用同一个 WorkflowEngine 实例的原语。

```text
.wf 文本 → DSL parser / AST executor ─┐
                                      ├→ WorkflowEngine 公共原语 → 后端
Python 业务流 → BaseWorkflow 薄门面 ─┘
```

因此“DSL 能力是公共标准”指的是 DSL 暴露的原语语义是标准；原语的
最终所有者仍是 WorkflowEngine，不是 DSL 解析器。

与 `32-grammar` 的区别:
- `32-grammar` 讲**语法与语义**(用户视角,DSL 怎么写、有什么效果)
- `33-engine` 讲**数据流与代价**(实现视角,指令如何调度截屏/识别/坐标解析)

## 目录

| 文档 | 主题 |
|------|------|
| [01-screenshot-and-crop.md](01-screenshot-and-crop.md) | `scan` / `recognize` 的截图-裁剪-识别数据流,以及 DSL 写法对截屏次数的影响 |
| [02-static-check.md](02-static-check.md) | 跑脚本前的静态检查:引用搜集范围、比对规则与报错形式 |

## 阅读建议

- 写工作流 DSL 前先看 `32-grammar`,了解语法
- 写性能敏感的工作流(循环内多次识别)时再看 `33-engine`,避免无谓的重复截图
- 调试坐标解析(`click [scene].$key` 找不到坐标)时,看 `screenshot-and-crop.md` 的 `_coord_meta` 一节
