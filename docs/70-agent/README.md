# 律匠外部 Agent 使用入口

> 最后更新：2026-10-09（基线 v0.13.14）。本机 MCP 接口已实现；WorkBuddy 界面接入与真实游戏闭环待实机验收。

智能调律属于 Lv1 功能。未开通 Lv1 时，所有 MCP 访问（包括初始化、发现和文档
读取）均返回激活提示。请让用户在律匠设置的“功能激活”中激活后重新接入。

用户在律匠“调律管理”旁的“智能调律”页选择执行用户和目标，授予需要的权限，
开启 MCP，复制或导出 WorkBuddy 接入配置。配置只连接当前本机实例，包含私人
连接令牌；关闭服务或退出律匠后失效，重新开启后重新导出。

Agent 首先调用 get_capabilities 和 list_docs，阅读操作契约，按需要查询用户数据、
扫描备战方案、与用户交流目标、调用计算，然后生成全新的基础规则组和调律规则。
用户可以回到智能调律页一键启动生成结果，也可授权 Agent 启动。内置 AI 设置与
此接入无关，不要求在律匠填写模型 API Key。

## 操作契约

- [对象、权限与执行边界](10-contracts/01-context-and-permissions.md)
- [装备培养与组合分析](20-operations/01-analysis.md)
- [生成新的调律配置](20-operations/02-generation.md)
- [WorkBuddy 接入与对话示例](30-examples/01-workbuddy.md)

## 共享知识

装备、流派与评价规格的唯一正文在 [公开领域知识](../10-game/10-public/README.md)。
具体可用玩法、词条上限、默认值及用户覆盖通过 get_game_config 和 get_tuning_config
读取，静态文档不是实时配置。公开工具 schema 随 Agent 文档包生成；维护文档和
源码不属于运行时上下文。
