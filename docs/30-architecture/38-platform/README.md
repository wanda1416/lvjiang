# 平台与接入

> 最后更新：2026-10-09（基线 v0.13.14）。本层描述如与代码或配置不符，以代码与配置为准。

设备端代理协议、实例执行锁、截图与输入后端矩阵、匿名统计。

## 文档索引

| 文件 | 内容 |
|------|------|
| [01-device-agent-protocol.md](01-device-agent-protocol.md) | 设备端代理协议：PC 经 adb forward 连律匠 app，用无障碍/Shizuku 截图与手势（L7 契约） |
| [02-instance-access.md](02-instance-access.md) | 多实例执行锁与用户数据访问边界 |
| [03-io-backends.md](03-io-backends.md) | 截图与输入后端矩阵：PC 前台/后台、Android a11y/Shizuku/ADB、scrcpy 的能力与限制 |
| [04-telemetry.md](04-telemetry.md) | 匿名统计：D1 表结构、写入粒度设计、校验边界、分析查询 |
| [07-agent-mcp.md](07-agent-mcp.md) | 外部 Agent 本机 MCP、授权、独立配置生成与发行文档包 |

- [08-i18n.md](08-i18n.md)：翻译模块、核心/插件合并、回退与内容一致性契约。

- [09-android-workbench.md](09-android-workbench.md)：Android 四页工作台、确认交互、任务历史与只读用户数据。
