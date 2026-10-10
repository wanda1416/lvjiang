# 律匠外部 Agent 使用入口

> 最后更新：2026-10-09（基线 v0.13.14）。本机 MCP 已实现；WorkBuddy 界面接入与真实游戏闭环待实机验收。

智能调律属于 Lv1 功能。未开通 Lv1 时，所有 MCP 访问（包括初始化、发现和文档
读取）均返回激活提示。请让用户在律匠设置的“功能激活”中激活后重新接入。

用户在“调律管理”旁的“智能调律”页启动 MCP 服务，导出接入配置到 WorkBuddy。
页面只管理启动、导出和关闭服务。开启服务即可使用本实例已开放的 MCP 能力，
不绑定一个用户或设备，不要求逐项授予权限。配置只含本机地址与私人连接令牌；
切换用户和设备不改变配置，关闭重开服务后重新导出。

先调用 get_capabilities、list_users 和 list_targets，了解当前用户、全部用户和
已连接设备。当前用户随律匠主界面变化，用户与执行目标在每次调用中明确选择。
不要把连接建立时的当前用户当作永久固定用户，换用户处理前重新查询上下文。
接口返回功能未开通、服务关闭、用户不存在、设备离线或任务占用时，向用户解释
具体原因；不用让用户重新导出配置或逐项勾选授权。

阅读契约，查询数据，与用户讨论目标，分析培养和组合，再生成全新的基础组和
调律规则。生成结果和建议在 Agent 中展示，可调用 start_auto_tuning 启动现有
调律流程。用户明确要求保存默认选择时调用 update_tuning_config，之后也能从
原自动调律入口启动。内置 AI 模型设置与 MCP 无关。

## 操作契约

- [对象、状态与执行边界](10-contracts/01-context-and-permissions.md)
- [装备培养与组合分析](20-operations/01-analysis.md)
- [生成新的调律配置](20-operations/02-generation.md)
- [WorkBuddy 接入与对话示例](30-examples/01-workbuddy.md)
- [共享领域知识](../10-game/README.md)

## 机制、操作与 DSL 文档

文档沿用原归属，开发与发行共用以下四层的完整正文和目录：

- [10-game：游戏机制与调律评价](../10-game/README.md)
- [30-architecture：架构与 DSL](../30-architecture/README.md)，[DSL 语法目录](../30-architecture/32-grammar/README.md)
- [60-userguide：软件操作指南](../60-userguide/README.md)
- 70-agent：当前入口、MCP 契约与工作流示例。

先调用 list_docs(category)，或用 search_docs(query, category) 查具体章节；
省略 category 跨四层查询。AI 解释装备与玩法时查 10-game，教用户点击和设置时
查 60-userguide，解释脚本语法和执行语义时查 30-architecture。

开发环境直接读本实例 docs，无需先打包；发行包保留这四个原目录。工具 schema
实时或构建时由实际接口生成。数据与有效规则通过 get_game_config、get_tuning_config
核对，文档描述的软件能力与 MCP 工具当前可调用范围分别判断。
