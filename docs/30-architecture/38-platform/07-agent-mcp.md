# 外部 Agent 本机 MCP 与智能调律

> 最后更新：2026-10-09（基线 v0.13.14）。桌面服务与开发/发行共用文档已实现，WorkBuddy GUI、Windows 打包和真实游戏闭环待实机验收。

## 服务与动态上下文

AgentTuningPage 位于“调律管理”旁，仅管理启动、导出和关闭服务。页面不保存
固定用户、目标白名单、动作授予或独立生成结果编辑器。开启服务就开放已实现
能力，服务关闭后 AgentService.set_enabled(False) 阻止排队中操作继续执行。

get_capabilities 和 list_users 经 Qt 桥读取现有 UserManager 的当前用户与全部
用户，list_targets 读取完整目标注册表及 ready/selected/busy 状态。查询不写默认
或编辑状态。每次工具明确指定 user；任务可指定 target_id，缺省解析当前目标。
不存在的用户拒绝，目标选择沿用宿主草稿保存及运行视图投影，再调用现有执行器。

LocalMCPServer 使用官方 FastMCP、Streamable HTTP 和 uvicorn，绑定本机动态
端口与独立令牌。连接配置不含用户或目标，切换上下文无需重导；不同安装实例
端口与令牌独立。关闭不停止已有游戏任务，退出关闭桥接并撤销未执行请求。

## 调用时限制与线程

Bearer 校验后，每次请求校验 has_feature("lv1")，包括初始化、发现和所有文档
资源。未激活时 JSON-RPC 错误携带激活提示和 lv1_required；其他 HTTP 请求返回
403 和相同提示。现有连接无法绕过，页面提示 Lv1 且禁用未激活时的启动服务。

不再维护 AgentGrant。已有能力默认开放，服务关闭、设备离线、用户不存在、
运行占用、平台/图库不支持时由接口按调用返回具体原因。动作是否发生由最终
规则配置决定，不增加前置勾选授权。任意文件、凭据和脚本执行仍未开放。

普通工具经 asyncio.to_thread 调用 AgentService，计算作业另返 ID。桥接操作由
Future 和 Qt 信号进入主线程；若调用本来就在主线程则直接执行，避免自等待。
超时或关闭取消未执行请求。执行用户与参数在启动时冻结，不随界面切换改变。

## 数据与新配置

所有用户通过明确 user 参数定位现有仓储，各用户的方案、任务和生成结果仍有
各自归属。读取所有用户不等于混合数据；任务 ID 与 user 必须匹配。
生成 key 由 user/request_id 派生，结果私有内容独立存储，不写入规则 YAML。

新基础组和规则使用公共解析器，独占创建 local 文件，任何层已有 key 都不覆盖。
记录发布失败只清理本次创建内容。启动核对定义修订、派生方案与目标状态；
回收/重置/锁定/材料行为由配置决定。用户默认保存按字段合并并校验基准修订。

## 共享文档与构建

文档继续由原层维护：10-game 为机制，30-architecture 为架构与 DSL，
60-userguide 为操作，70-agent 为接入契约。DOCUMENT_DIRECTORIES 定义发布四层，
document_catalog 发现其中全部 Markdown；AGENT_DOCUMENTS 只定义常用 ID 别名。
list_docs/search_docs 可按 category 筛选，清单带标题、分类、路径和哈希。

开发环境 AgentDocuments(root=PROJECT_ROOT/docs, source_mode=True) 实时读正文，
运行时生成工具 schema，无需打包。发行构建按完全相同路径复制到 dist/lvjiang/docs，
在 70-agent 中输出 manifest 和 schemas/tools.json。独立 ZIP 使用 docs 前缀，
保持四层结构；发行读取核对文档清单、目录和哈希，不开放其他路径。

所有正文原样复制，机制不搬至 Agent 层，用户指南可用于操作教学，架构目录
完整保留 DSL 文档。章节 ID 的目录分隔符使用冒号，以便同样用于 MCP Resource URI。
源码、需求/开发文档、私人配置不会因正文引用而扩大读取范围。安装升级只清理
四个官方文档子目录与旧 agent 目录，不清用户 local/session。

需求见 [MCP 需求](../../20-requirements/50-platform/12-agent-mcp.md)，
文件边界见 [文档访问清单](../../20-requirements/50-platform/13-agent-docs-access.md)。
