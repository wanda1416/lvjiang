# 受保护配置同步

共享协议位于 `core.config.protected_bundle`，负责 ZIP、AES-GCM、允许路径和包大小；`content_service` 封装不跟随重定向的 HTTPS POST，避免激活码或管理 Token 被转发到其它站点。

`protected_remote.snapshot` 在主线程冻结本机有效激活码、有效期和明确配置的服务地址。`SyncJob.protected` 不在 repr 中展示激活码。Worker 线程只使用该快照，不重新读取 UI 配置。

Worker/D1 存已验签登记激活码的摘要、等级、有效期和启用状态。密钥表存 AES-GCM 包装的包密钥、密文摘要、最低客户端版本及包版本；包装主密钥留在 Worker Secret。签发私钥不上传。`/v1/key` 只返回授权允许的密钥，响应禁止缓存。

`run_sync` 分别检查受保护清单和公开清单，公开返回 304 不妨碍受保护包更新。受保护来源通过 remote 中的 `.protected.json` 记录归属、文件摘要、包版本及激活码摘要；公开同步保留这些路径。撤回或拒绝授权后强制重新检查公开清单，使相同路径恢复公开来源。

包先完整解密和验证，再在临时目录构造下一份暂存快照，目录替换并保留可恢复备份。保持既有 `remote.staging` 在下次启动提升的行为。解析器、工作流执行器和布局加载器不参与解密。

Resolver 在现有 remote 仲裁入口检查受保护文件授权。新实体、模板和登记表可由有效受保护包补充；系统已存在的普通配置继续比 content_version。受保护 WF 按包版本允许更新，公开 WF 不覆盖已有脚本。场景登记表先各层规范化，再合并受保护增量和 local 增量。失效内容被隐藏；启动时清理其归属文件。

本地服务地址位于 `config/local/remote_service.json`，不从远程清单读取并自动发送激活码。未确定生产域名前没有内置默认服务地址。真实云端部署及接口、密文下载解密、停用撤回和恢复已通过隔离验证；正式 Windows/Android 应用和实际业务包验收尚未完成。

需求见 [加密配置下发](../../20-requirements/50-platform/11-protected-config.md)，使用见 [用户配置](../../60-userguide/11-protected-config.md)。

PC 向手机生成离线快照时，已授权且实际生效的受保护基底冻结到快照的 system 层，原 local 覆盖保持不变。失效文件不导出，也不传输 PC 激活码与 `.protected.json`。Android 原生入口不新增联网同步，沿用已有离线执行契约。

内容接口明确发送应用 User-Agent。Worker 的 JSON `forbidden` 与边缘层普通 HTTP 403 分开识别；只有前者作为授权拒绝撤回文件，后者按联网失败处理并保留已有内容。
