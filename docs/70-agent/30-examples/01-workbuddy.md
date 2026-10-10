# WorkBuddy 本机接入与对话示例

> 最后更新：2026-10-09（基线 v0.13.14）。按官方格式导出 Streamable HTTP 配置，实际 WorkBuddy 界面接入待本机验收。

1. 启动律匠，在设置中激活 Lv1，打开“调律管理”旁的“智能调律”。
2. 点击“启动 MCP 服务”，再点击“导出 MCP 接入配置”。
3. 在 WorkBuddy 自定义 MCP 配置入口导入 JSON，连接本机律匠。
4. 让 Agent 先调用 get_capabilities、list_users、read_doc("entry")。
5. 可以说：“先看看当前用户和我所有用户，再扫描我指定用户的备战方案，和我讨论
   想玩的玩法。分析哪些装备值得培养，生成一套新调律配置，保留我原有设置。”
6. Agent 在对话中展示建议和生成结果；用户要求执行时调用 start_auto_tuning。
   进度和结果仍在律匠原有调律管理中查看。

切换律匠主界面用户，或让 Agent 处理另一个用户，不需要重导接入配置。设备通过
list_targets 查询，启动时可指定 target_id；设备未连接或忙碌时由接口返回原因。

导出格式为 mcpServers、type=streamableHttp、url 和 Authorization 请求头，符合
[WorkBuddy 官方说明](https://open.workbuddy.cn/docs/connector)。客户端须运行在
能访问本机 127.0.0.1 的同一台电脑。GUI 对本机 HTTP 的实际接受情况待实机验收。
多个实例各用独立端口和令牌；同时配置时将默认 lvjiang 连接名称改成不同名称。

接入配置不包含用户数据或模型 API Key。令牌属于私人连接信息，请勿公开分享。
关闭/重开服务或重启软件后重新导出；关闭 MCP 不停止已启动的游戏任务。
开发环境直接运行源码即可读取同结构文档，不需要先打包。
