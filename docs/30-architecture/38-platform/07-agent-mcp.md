# 外部 Agent 本机 MCP 与智能调律

> 最后更新：2026-10-09（基线 v0.13.14）。桌面服务、文档构建和配置生成已实现，WorkBuddy GUI 与真实游戏闭环待实机验收。

## 接入与线程

燕云插件在右侧调律管理旁注册 AgentTuningPage。页面选择执行用户、授权目标和
操作权限，显式启动 LocalMCPServer。服务使用官方 MCP Python SDK 的 FastMCP、
Streamable HTTP 和 uvicorn，仅绑定 127.0.0.1 的动态端口；每次开启生成新的
Bearer 令牌，关闭后不再接受请求。导出 WorkBuddy mcpServers 配置中的
streamableHttp、URL 和请求头；导出是本机私人文件，不是公开文档包。

core/agent_mcp 只承载传输，不导入游戏或 Qt。tool_catalog 是工具登记的唯一来源，
服务器与公开 schema 构建共用。普通工具经 asyncio.to_thread 调用 AgentService；
较长的领域计算另返回作业 ID，可查询及取消。游戏启动和控制通过 AgentTaskBridge
的 Qt 信号在主线程调用已有宿主，超时或关闭时撤销未执行请求，不从网络线程改控件。
关闭 MCP 不停止游戏任务；应用退出撤销权限及未执行桥接请求。

## 数据与权限

智能调律为 Lv1 功能。传输入口在验证 Bearer 令牌后逐次调用 has_feature("lv1")，
拦截整个 MCP 协议，包括初始化、发现和文档资源，不仅是写入工具。未激活时，
JSON-RPC 请求返回含激活提示和 lv1_required 标识的协议错误；其他 HTTP 请求返回
403 和相同提示。客户端能直接显示提示，且已建立的连接也复查当前授权。
页面同时禁用未激活时的开启接入与一键启动，启动处理器再次校验，仍允许关闭服务。

AgentGrant 是当前连接的冻结授权，限定一个用户和目标，独立授予读取、采集写入、
新配置生成、用户选择保存与任务执行，另限制回收/重置/狗粮/锁定。生成期间的权限
复核使用独立短锁；磁盘发布锁不会阻止 UI 撤销授权。启动在主线程再次核对权限、
目标、目标占用与现有连接方案。当前仅支持已连接且主界面选中的授权目标，不能
通过参数偷偷切换观察目标或停止其他任务。

公共配置由发行语义的 ConfigResolver 读取，local/remote/system 的优先级不变；
没有通用文件/SQL/Shell 工具。用户数据经 LoadoutRepository 等权威接口读取。
get_plan_context 只读取授权用户方案所引用的基础属性，不列举共享属性文件中的
其他用户条目。分析覆盖只在派生方案上生效，不写回活动方案或真实装备。

## 新配置发布与运行快照

create_generated_tuning 校验基础组、规则及运行字段，按用户与幂等请求 ID 分配
新的稳定 key，委托规则管理层 create_generated_entities 排他创建 local 文件。
任何层已有同 key 时拒绝覆盖。配置创建失败只清理本次拥有的文件；全部落盘后
才发布私人生成结果。重试相同请求返回原结果，不同内容必须用新请求 ID。

生成结果保存在 config/session/agent/results.json，含目标、培养建议、方案引用、
plan_targets 派生选择、新配置引用、运行参数和定义修订；规则 YAML 不存私人关联。
DocumentDirectoryStore 管理该目录，tasks.json 保存本服务启动任务的引用、状态和
扫描结果。重连可查询，重启不自动重放旧任务；旧运行缺少终态时明确返回 unknown。

启动复核定义修订及所有可能执行的动作，调用共用 prepare_tuning。UI 普通调律也
使用该准备函数。Agent 页调用 TuningTab 的同一启动入口，传入完整运行选择和派生
LoadoutState；TuningRunContext.smart_state 只供本次 SmartTuningEvaluator 使用。
目标方案范围与对话选择的玩法不影响持久化方案、本次之外的默认值或原数据。

扫描复用 scan_all_loadouts，只处理可见网格。scan_results 是逐方案结构化产出，
分别记录装备和基础属性结果；顶层 -1 在桌面任务历史中记失败。采集仍走既有装备
与基础属性写入边界，不向 Agent 暴露任意数据编辑。任务进度继续进入调律管理。

## 文档与发行

机制和评价正文在 docs/10-game/10-public，源码登记在 90-internal；旧路径只保留
导航，不再维护第二份规格。Agent 契约在 docs/70-agent，UI 指引仍在用户指南。
scripts/build_agent_bundle.py 按明确文件清单输出 agent 目录、manifest 与实际工具
schema，并重写包内引用。维护引用不扩张打包边界。AgentDocuments 只按文档 ID
读取清单内文件，验证摘要和路径归属，不在文档缺失时回退读取开发仓库。

Windows 打包将同一构建结果放入安装器/便携版，并输出独立 Agent ZIP；安装升级
清理旧官方 agent 集合，保留个人配置与数据。PyInstaller 收集 uvicorn 动态后端、
MCP 元信息及 JSON Schema 资源。开发运行可先构建文档包：

```bash
python scripts/build_agent_bundle.py --output agent
```

需求与访问边界见 [MCP 需求](../../20-requirements/50-platform/12-agent-mcp.md)和
[文档访问清单](../../20-requirements/50-platform/13-agent-docs-access.md)。
