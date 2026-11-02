# config/session 数据划分与读写方

> 最后更新：2026-11-02（基线 v0.15.8）。本层描述如与代码不符，以代码为准。

`config/session/` 是客户端唯一的纯运行态目录：不进 git、不参与 system/local 配置分层、
不经 `ConfigResolver`。目录本身由 `constants.SESSION_CONFIG_DIR` 声明，其余路径统一经
`core/paths.session_directory(config)` 取得——隔离实例、打包和手机同步都靠传不同的配置根
换目录，不允许从 system 的父目录推导。

本文只回答「这个目录里有什么、谁写、谁读、丢了会怎样」。节点语义、领域模型和锁协议分别
在 [04-config-layering.md](04-config-layering.md)、[31-models/04-profile.md](../31-models/04-profile.md)、
[31-models/03-session-and-context.md](../31-models/03-session-and-context.md) 与
[38-platform/02-instance-access.md](../38-platform/02-instance-access.md) 里写全，这里只做归属与
存续判断，不重复它们的结论。

## 一、五个分区

同样是「运行态」，能不能丢、要不要跨设备、能不能给作者看，三件事完全不同。按归属分五区：

| 分区 | 内容 | 可否重建 | 是否跨设备有意义 |
|------|------|----------|------------------|
| A 用户域 | 按 username 隔离的资料、配装、笔记、Profile 数值与定义 | 否 | 是 |
| B 历史与产出 | 任务/批量/调律历史、工作流产出、逐任务日志 | 否 | 是（终态归集） |
| C 实例状态 | `session.json`、`interface.json`、`batch.json`、`content.json` | `content.json` 可重建，其余不可 | 否 |
| D 平台与接入 | 预置基底与标记、离线同步状态、MCP 接入、Agent 结果、截图、二维码 | 截图与二维码可重建 | 否 |
| E 锁 | 主实例所有权、跨进程读写锁、用户执行租约 | 是（进程退出即失效） | 否 |

## 二、A 用户域（按 username 隔离）

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `users/{名}.json` | 用户资料：`created_at`、`avatar`（存 basename）、`attributes`、`workflow_params`、`ui_state`，余下字段按插件扩展保留 | `core/user_config.py` | `document_type=lvjiang.user`、schema v2；每次修改走「跨进程读改写 + 只提交实际变化」 |
| `users/{名}.loadouts.json` | 配装方案文档：`revision` + 方案列表，单文档实测可达约 170 KB | `apps/yysls/core/loadout/repository.py` | 整文档读写，靠文档内 `revision` 做乐观并发，不是逐条同步 |
| `users/{名}.notes.json` | 用户笔记 | `core/user_notes.py` | |
| `users/{名}.session.json` | DSL `session` 关键字的落点 | `core/config/users.py`（`SessionManager`） | 机制可用但当前无系统脚本写入，见 03-session-and-context |
| `avatars/avatar_<16hex>.png` | 内容寻址的头像 PNG 库 | `core/user_avatars.py` | 资料里只存 basename，可直接删（改回默认头像） |
| `profile.db` | 四模型数值（`profile_entries`）、变更历史（`profile_history`）、key 重命名审计 | `core/profile/repository.py`（`ProfileDB`） | schema v8，`entries.locked` 是「用户 × key」的失效标记 |
| `profile.yaml` | 按模型归档的 key 定义 | `core/profile/schema.py` | **是用户数据，不是系统配置**：保存时必须保留未识别定义，不得因升级覆盖 |

用户域全部不可重建。`users/.lock/` 下的锁与它们一一对应，见第五节。

## 三、B 历史与产出

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `daily_history.db` | `batch_runs` / `task_runs` | `core/daily_history.py`（`TaskHistoryRepository`） | schema v2；同时被 `core/history_transfer.py` 作为回传通道登记 |
| `tuning_history.db` | `tuning_runs` / `tuning_equipment` / `telemetry_deliveries` | `apps/yysls/core/tuning_history/repository.py` | schema v3；投递状态供遥测使用，见 [39-runtime/01-tuning-history.md](../39-runtime/01-tuning-history.md) |
| `output/{用户}/{年-月}/DD/` | 工作流产出与批量报告 | `core/fs_util.dated_output_dir` | 历史记录里以相对路径保存（`result_path` / `report_path`），改目录会让旧记录找不到文件 |
| 目录外的 `logs/daily/` | 逐任务日志 | `core/daily_history.default_log_root` | 不在 session 下，但同属运行态，历史记录以 `log_path` 指向 |
| `history-transfer/` | 手机历史回传的收发 zip 与 `preferences.json` | `core/android/history_transfer.py`、`core/ondevice/history_transfer.py`、`ui/mobile/offline.py` | zip 是传输中产物，`preferences.json` 按设备序列号记上次选择，复用 `SessionStore` 原语 |

## 四、C 实例状态（机器自己的，不跨设备）

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `session.json` | `users`（用户名册与顺序）、`settings`、`daily.scripts`（脚本顺序/启停/显示名）、`wf_configs`（按 `wf_id` 的工作流参数）、`plans`（不分发的机器级方案） | `SessionStore`（`core/config/session.py`） | 节点语义见 04-config-layering；写入口是 `get_node`/`set_node`/`update_node`/`mutate_node`，禁止外部直接读写文件 |
| `interface.json` | `actives`（当前用户/布局/空间/环境/方案）、`daily`（当前脚本与分组）、`ui_state`（窗口、分栏、页签、列宽）、`batch`（活动组 ID 与草稿）、`profile`（总览分组与顺序）、`alert_info`、`alert_history`、`server_config`（公告、遥测、在线配置状态） | `InterfaceStore`（`core/config/interface.py`） | 页面级状态唯一写入口是 `update_ui_page_state`，不得整节点替换 |
| `batch.json` | 批量活动组的**定义**（`groups`） | `core/batch_run.py` | 与 `interface.json.batch` 分工：定义在 batch.json，当前选中与草稿在 interface.json |
| `content.json` | 内容修订索引：按实体记 revision，供缓存失效 | `core/config/content.py` | 可重建；缺失只影响缓存命中，不影响配置正确性 |

`session.json` 与 `users/{名}.session.json` 只是名字像，归属完全不同：前者是 App/UI 状态，
后者是 DSL `session` 关键字的落点。按用途区分，不要按文件名猜。

## 五、D 平台与接入

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `baseline.json` | 官方预置基底身份 | `core/system_preset.py`、`core/config/remote.py` | 只用于生成和确认预置，不参与远端比对 |
| `preset.json` | 预置已初始化的标记 | `core/system_preset.py`、`core/ondevice/offline.py` | 安卓端据此判断「用预置配置」而不是「已从 PC 同步」 |
| `offline.json` | 手机离线同步的来源与时间 | `core/ondevice/offline.py` | 只读状态，无同步记录表示从未同步 |
| `mcp.json` | 外部 Agent 接入：端口、启用开关、`token`、`agent_key`、导出路径 | `core/agent_connection.py` | **含凭据**，见第七节 |
| `agent/results.json`、`agent/tasks.json` | 外部 Agent 的生成结果与任务表 | `apps/yysls/core/agent/service.py` | 经 `DocumentDirectoryStore`（`_meta.json` + 可恢复事务） |
| `screenshots/{布局}/{场景}(__{视图}).(png|webp)` | 布局参考截图 | `core/layout_screenshots.py` | 目录体积第一大项，删掉重截即可 |
| `feedback-qrcode.jpg` | 交流群二维码缓存 | `core/group_qrcode.py` | 可重建，缺失时从站点拉取 |
| `yysls/{play_styles,graduations,attr_loadout,attr_derivations}.json` + `_meta.json` | 燕云插件的会话文档 | `apps/yysls/config/session_node.py` | `DocumentDirectoryStore`，storage v1；跨文档更新走可恢复事务 |

## 六、E 锁与句柄

| 路径 | 用途 | 归属 |
|------|------|------|
| `.locks/configuration.lock` | 主实例所有权，持有至进程结束 | `core/access.py` |
| `.locks/<目标路径摘要>.<suffix>.lock` | 同一目标的跨进程读改写串行化 | `core/fs_util.interprocess_lock` |
| `users/.lock/<摘要>.execution.lock` | 用户执行租约，表达「这个用户正被任务独占」 | `core/access.py` |
| `users/.lock/<摘要>.metadata.lock`、`users/.lock/<文件名>.lock` | 用户资料与用户 Session 的短时写锁，与执行租约互相独立 | `core/user_config.py`、`core/config/users.py` |
| `yysls/.locks/`、`agent/.lock`、`agent/generation.lock` | 文档目录事务锁、Agent 生成互斥 | `core/config/document_store.py`、agent service |

锁文件保留不代表仍被占用，异常退出由操作系统释放。获取顺序全仓库统一为
「进程内线程锁 → 跨进程文件锁」，且跨进程锁永远在 `configuration_lock` 之后。

## 七、读写收口与一致性

一条规则：**每个持久化物只有一处咽喉模块**，调用方不得自己拼路径做文件 IO。

当前收口情况（按文件路径字面量检索 `src/`）：

| 数据面 | 引用它的模块数 | 说明 |
|--------|----------------|------|
| `users/*.loadouts.json`、`*.notes.json`、`*.session.json` | 4 | `user_config` / `user_notes` / `config.users` / loadout repository |
| `session.json`、`interface.json` | 2 | 只有 `SessionStore`/`InterfaceStore` 做文件 IO；离线包与手机回传各按显式路径构造一个实例，节点仍归原 Store 语义 |
| `profile.db`、`daily_history.db`、`tuning_history.db` | 各 1 个 IO 方 | 各有一个 Repository 承担读写；其余出现是按文件名的打包清单（`offline_bundle`）或历史回传通道（`history_transfer`、tuning `transfer`） |

自查方式：改数据层前后各跑一次路径字面量检索，引用模块数变多就说明有人在绕过咽喉。

一致性机制与数据面一一对应，不是「都上锁」：

| 机制 | 用在 |
|------|------|
| 临时文件 + `os.replace` 原子替换 | 所有 JSON 与文档 | 
| 跨进程文件锁（`.locks/` 按路径摘要命名） | 所有 JSON 读改写 |
| 加载基线 + 字段级合并（`SessionSnapshot`） | `users/*.session.json` |
| 文档内 `revision` 乐观并发 | `users/*.loadouts.json` |
| SQLite 事务（WAL + busy_timeout） | 三个 `.db` |
| 事务日志 + 下次访问恢复（`_transaction.json`） | `yysls/`、`agent/` 多文档更新 |
| `READONLY_TRANSIENT_PATHS` 白名单 | 只读实例的 `actives`/`daily`/`ui_state`/`batch` 等选择项 |

## 八、生命周期与存续判断

- **可删可重建**：`content.json`、`screenshots/`、`feedback-qrcode.jpg`、全部锁文件。
- **可重置但会丢偏好**：`session.json`、`interface.json`、`batch.json`（重新配置即可，但批量组与脚本顺序会丢）。
- **不可重建（用户资产）**：`users/*`、`profile.db`、`profile.yaml`、三个历史库、`output/`、`agent/results.json`。
- **迁移前必须备份**：`profile.db` 由 `schema_version` 表驱动有序迁移（当前 v8）；`profile.yaml` 与 `users/*.json` 靠文件内 schema 字段判定，升级失败时**不得**用默认值覆盖。
- 磁盘上可能出现但不属于程序产物的文件（例如手工留存的 `profile.before-v8.db`）：代码既不读也不写，
  不能当作迁移或恢复机制依赖。

## 九、隐私与打包边界

`config/session/` 会包含用户名、装备数据、执行结果与截图，因此：

- 用户被明确要求**不要**整目录打包上传（见 [../../60-userguide/09-faq-and-feedback.md](../../60-userguide/09-faq-and-feedback.md)）；
  需要排查时由开发者点名具体文件。
- 匿名安装标识因此不放这里，落在 `config/local/telemetry/`（见 04-config-layering 的例外条目）。
- `mcp.json` 的 `token`/`agent_key` 是访问凭据，属于「本机私有」而不是「可分享的运行态」；
  新增同类凭据时按 `config/local` 处理，不要塞进 session。对照：AI 接入的连接参数写在
  `session.json` 的 `settings.ai` 节点，而 API Key 只进系统凭据库（`core/ai/store.py`），
  这才是凭据该有的落点。
- 头像文件是用户上传的个人图像，与 `output/` 里的产出一样只在该用户目录语义下使用。

## 十、已知缺口

1. `31-models/03-session-and-context.md` 的目录树只列了 `session.json`、`yysls/`、`profile.db`、`users/`，
   本文补齐其余条目；两处描述冲突时以本文与代码为准。
2. `session.json`、`interface.json` 的 `version` 字段已无代码读写，磁盘上残留 2 与 1；
   任何「按版本号决定是否迁移」的新逻辑都必须先重建这个机制，不能假定它有效。
3. 目录里残留 `session.json.lock`、`interface.json.lock`、`batch.json.lock` 这类侧车锁文件，
   当前代码统一写 `.locks/` 下的摘要命名锁；这类文件可直接删除。
4. `output/` 与 `logs/daily/` 的同一次运行产出分处两个目录，历史记录分别用 `result_path`、
   `report_path` 与 `log_path` 指过去；清理策略需要同时考虑三处，目前没有统一维护入口。

## 相关文档

| 文档 | 内容 |
|------|------|
| [04-config-layering.md](04-config-layering.md) | system/local 分层、`session.json` 节点划分与用户偏好归属 |
| [05-content-cache.md](05-content-cache.md) | 修订缓存、运行快照与基底状态 |
| [../31-models/03-session-and-context.md](../31-models/03-session-and-context.md) | DSL `session`/`context` 与用户目录文件职责 |
| [../31-models/04-profile.md](../31-models/04-profile.md) | Profile 四模型与数值语义 |
| [../38-platform/02-instance-access.md](../38-platform/02-instance-access.md) | 主实例/只读实例边界与用户执行锁 |
| [../39-runtime/01-tuning-history.md](../39-runtime/01-tuning-history.md) | 调律历史模型与投递状态 |
