# AI 连接与请求边界

> 2026-10-08，基线 v0.13.13。

`core/ai` 不依赖 Qt 或游戏插件。AISettings 是冻结的请求参数；AIService 使用
HTTPX AsyncClient 调用 `/chat/completions`，返回文本、模型、耗时与服务实际提供的
token 用量。未提供用量时不估算。asyncio 总超时和任务取消关闭客户端，不自动重试。
错误仅含固定说明和状态码，不传播响应正文、网络异常字符串或请求凭据。

AIStore 复用 SessionStore 的文件锁与原子写入，独立 ai.json，不迁移旧节点。
连接节点只保存 base_url、model、timeout；系统凭据库账户由配置绝对路径及接口地址
摘要确定。允许系统安全后端，不降级到明文文件后端。连接配置不在移动同步白名单中。

UI 的 AISettingsPage 独立保存自己的草稿；QRunnable 在后台运行独立 asyncio 循环。
取消事件终止请求，关闭配置窗口触发取消；Qt 信号把结果送回页面，不从工作线程改控件。
后续任务应在启动时读取设置和 Key 构造 AIService，再贯穿使用该快照。

Windows 打包显式收集 keyring.backends.Windows，避免凭据后端动态发现时缺少模块。
游戏领域上下文与计算工具应由游戏插件提供，本阶段没有工作流 AI 内置函数。

依赖参考：[HTTPX](https://www.python-httpx.org/async/)、
[keyring](https://github.com/jaraco/keyring)、
[Chat Completions](https://developers.openai.com/api/reference/resources/chat)。
