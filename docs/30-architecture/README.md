# 架构说明文档（已拆分迁移）

> 最后更新：2026-11-18（基线 v0.15.17）。本页是旧路径兼容页，只做拆分去向总表。

原 `docs/design/architecture/` 已按内容性质拆分：架构与设计去向 [design/architecture/](../design/architecture/README.md)，
DSL 接口参考去向 [reference/dsl/](../reference/dsl/README.md)，平台接入操作事实去向
[agent/](../agent/README.md)。对照：

| 旧子层 | 新位置 |
|--------|--------|
| `30-overview/` | `docs/design/architecture/overview/` |
| `31-models/` | `docs/design/architecture/models/` |
| `32-grammar/` | `docs/reference/dsl/`（本目录下的 [32-grammar/README.md](32-grammar/README.md) 为其兼容页） |
| `33-engine/` | `docs/design/architecture/engine/` |
| `34-scene/` | `docs/design/architecture/scene/` |
| `35-workflows/` | `docs/design/architecture/workflows/` |
| `36-graduation/` | `docs/design/architecture/graduation/` |
| `37-vision/` | `docs/design/architecture/vision/` |
| `38-platform/` | `docs/design/architecture/platform/`（其中 `05-ai-connection.md`、`07-agent-mcp.md` 迁至 `docs/agent/`） |
| `39-runtime/` | `docs/design/architecture/runtime/` |

入口：[架构区](../design/architecture/README.md)、[DSL 语法参考](../reference/dsl/README.md)。
