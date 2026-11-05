# config/session 数据划分与读写方

> 最后更新：2026-11-02（基线 v0.15.9）。本层描述如与代码不符，以代码为准。

`config/session/` 是客户端唯一的纯运行态目录：不进 git、不参与 system/local 配置分层、
不经 `ConfigResolver`。目录本身由 `constants.SESSION_CONFIG_DIR` 声明，其余路径统一经
`core/paths.session_directory(config)` 取得——隔离实例、打包和手机同步都靠传不同的配置根
换目录，不允许从 system 的父目录推导。

同级的 `logs/` 放产出与日志（`logs/output`、`logs/daily`、`logs/batch`、`logs/tuning`），
不属于本目录，见第三节；其余各节都只讲 `config/session/` 里面的东西。

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
| B 历史 | 任务/批量/调律历史库、手机历史回传目录 | 否 | 是（终态归集） |
| C 实例状态 | `session.json`、`interface.json`、`batch.json`、`content.json` | `content.json` 可重建，其余不可 | 否 |
| D 平台与接入 | 预置基底与标记、离线同步状态、MCP 接入、Agent 结果、截图、二维码 | 截图与二维码可重建 | 否 |
| E 锁 | 主实例所有权、跨进程读写锁、用户执行租约 | 是（进程退出即失效） | 否 |

产出 JSON 与逐任务日志不在本目录，在 `PROJECT_ROOT/logs/` 下（见第三节末表）。

## 二、A 用户域（按 username 隔离）

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `users/{名}.json` | 用户资料：`created_at`、`avatar`（存 basename）、`attributes`、`workflow_params`、`ui_state`，余下字段按插件扩展保留 | `core/user_config.py` | `document_type=lvjiang.user`、schema v2；`ui_state` 是**按用户隔离**的界面偏好（出装筛选、战斗属性页开关），与 `interface.json` 的页面级 `ui_state` 同名不同义：前者随角色切换，后者随机器 |
| `users/{名}.loadouts.json` | 配装方案文档：`revision` + 方案列表，单文档实测可达约 170 KB | `apps/yysls/core/loadout/repository.py` | 整文档读写，靠文档内 `revision` 做乐观并发，不是逐条同步 |
| `users/{名}.notes.json` | 用户笔记 | `core/user_notes.py` | |
| `users/{名}.session.json` | DSL `session` 关键字的落点 | `core/config/users.py`（`SessionManager`） | 机制可用但当前无系统脚本写入，见 03-session-and-context |
| `avatars/avatar_<16hex>.png` | 内容寻址的头像 PNG 库 | `core/user_avatars.py` | 资料里只存 basename，可直接删（改回默认头像） |
| `profile.db` | 四模型数值（`profile_entries`）、变更历史（`profile_history`）、key 重命名审计 | `core/profile/repository.py`（`ProfileDB`） | schema v8，`entries.locked` 是「用户 × key」的失效标记 |
| `profile.yaml` | 按模型归档的 key 定义 | `core/profile/schema.py` | **是用户数据，不是系统配置**：保存时必须保留未识别定义，不得因升级覆盖 |

用户域全部不可重建。用户目录自身不落任何锁文件，跨进程由执行租约负责，见第六节。

## 三、B 历史与产出

本目录里只有两个历史库和手机回传的收发目录：

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `daily_history.db` | `batch_runs` / `task_runs` | `core/daily_history.py`（`TaskHistoryRepository`） | schema v2；同时被 `core/history_transfer.py` 作为回传通道登记 |
| `tuning_history.db` | `tuning_runs` / `tuning_equipment` / `telemetry_deliveries` | `apps/yysls/core/tuning_history/repository.py` | schema v3；投递状态供遥测使用，见 [39-runtime/01-tuning-history.md](../39-runtime/01-tuning-history.md) |
| `history-transfer/` | 手机历史回传的收发 zip 与 `preferences.json` | `core/android/history_transfer.py`、`core/ondevice/history_transfer.py`、`ui/mobile/offline.py` | zip 是传输中产物，`preferences.json` 按设备序列号记上次选择，复用 `SessionStore` 原语 |

产出与日志在同级 `logs/` 下，与配置、会话互不干扰；同一次运行的结果与日志落在同一棵目录树：

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `logs/output/{用户}/{年-月}/DD/` | 工作流结果 JSON | `core/fs_util.task_result_path` | 历史库以相对 `PROJECT_ROOT` 的路径记录（`result_path`）；旧版本写在 `config/session/output`，旧记录直接指向旧目录，照旧打得开 |
| `logs/daily/{用户}/{年-月}/DD/` | 逐任务日志 | `core/daily_history.default_log_root` | 历史库以 `log_path` 指向 |
| `logs/batch/{年-月}/DD/` | 批量报告 | `core/batch_report.py` | 历史库以 `report_path` 指向 |
| `logs/tuning/{用户}/{年-月}/DD/` | 调律说明 Markdown | `apps/yysls/workflows/tuning_doc.py` | 历史库以 `markdown_path` 指向 |

这四类路径都存在历史库里，移动目录要让旧记录继续可用：历史库记录的是相对 `PROJECT_ROOT`
的路径，换目录就得同时改写记录（参见 `scripts/one_off/migrate_output_to_logs.py` 的做法）。

## 四、C 实例状态（机器自己的，不跨设备）

| 路径 | 内容 | 咽喉模块 | 备注 |
|------|------|----------|------|
| `session.json` | `users`（用户名册与顺序）、`settings`、`daily.scripts`（脚本顺序/启停/显示名）、`wf_configs`（按 `wf_id` 的工作流参数）、`plans`（不分发的机器级方案） | `SessionStore`（`core/config/session.py`） | 节点语义见 04-config-layering；写入口是 `get_node`/`set_node`/`update_node`/`mutate_node`，禁止外部直接读写文件 |
| `interface.json` | `actives`（当前用户/布局/空间/环境/方案）、`daily`（当前脚本与分组）、`ui_state`（窗口、分栏、页签、列宽）、`batch`（活动组 ID 与草稿）、`profile`（总览分组与顺序）、`alert_info`、`alert_history`、`server_config`（公告、遥测、在线配置状态） | `InterfaceStore`（`core/config/interface.py`） | 页面级状态唯一写入口是 `update_ui_page_state`，不得整节点替换 |
| `batch.json` | 批量活动组的**定义**（`groups`） | `core/batch_run.py` | 与 `interface.json.batch` 分工：定义在 batch.json，当前选中与草稿在 interface.json |
| `content.json` | 内容修订索引：按实体记 revision | `core/config/content.py` | 可重建；任务读取不依赖它，缺失或损坏只影响这份记录本身 |

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

全部跨进程锁集中在一个锁根 `config/session/.locks/`，文件名统一为
`<类型>_<摘要8位>.lock`（摘要取 sha256 前 8 位；`core/fs_util.lock_file`）：

| 文件名 | 用途 | 归属 |
|--------|------|------|
| `instance_<摘要8位>.lock` | 主实例所有权，持有至进程结束 | `core/access.py` |
| `state_<摘要8位>.lock` | `session.json` / `interface.json` 等文档的跨进程读改写 | `core/fs_util.interprocess_lock` |
| `install_<摘要8位>.lock` | 配置安装与配置写临界区 | `core/config/content.py` |
| `profile_<摘要8位>.lock` | `profile.yaml` 写入 | `core/profile/schema.py` |
| `document_<摘要8位>.lock` | 文档目录事务恢复（`yysls/`、`agent/`） | `core/config/document_store.py` |
| `execution_<摘要8位>.lock` | 用户执行租约，表达「这个用户正被任务独占」 | `core/access.py` |
| `agent_<摘要8位>.lock` | 外部 Agent 配置合并写入 | `core/agent_connection.py` |

集中存放带来两个直接好处：不往 `config/local` 等数据目录写运行时文件；退出所有节点后
整个 `.locks` 目录删掉即为干净重置，下次按需重建。用户资料的读改写只在进程内串行，
跨进程由执行租约负责——不再为每份用户资料单独建文件锁。

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
| 跨进程文件锁（`.locks/` 下 `<类型>_<摘要8位>.lock`） | 所有 JSON 读改写与用户执行互斥 |
| 加载基线 + 字段级合并（`SessionSnapshot`） | `users/*.session.json` |
| 文档内 `revision` 乐观并发 | `users/*.loadouts.json` |
| SQLite 事务（WAL + busy_timeout） | 三个 `.db` |
| 事务日志 + 下次访问恢复（`_transaction.json`） | `yysls/`、`agent/` 多文档更新 |

## 八、生命周期与存续判断

- **可删可重建**：`content.json`、`screenshots/`、`feedback-qrcode.jpg`、全部锁文件。
- **可重置但会丢偏好**：`session.json`、`interface.json`、`batch.json`（重新配置即可，但批量组与脚本顺序会丢）。
- **不可重建（用户资产）**：`users/*`、`profile.db`、`profile.yaml`、三个历史库、`logs/output/`、`agent/results.json`。
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
- 头像文件是用户上传的个人图像，与 `logs/output/` 里的产出一样只在该用户目录语义下使用。

## 十、已知缺口

1. `31-models/03-session-and-context.md` 的目录树只列了 `session.json`、`yysls/`、`profile.db`、`users/`，
   本文补齐其余条目；两处描述冲突时以本文与代码为准。
2. `session.json`、`interface.json` 的 `version` 字段已无代码读写，磁盘上残留 2 与 1；
   任何「按版本号决定是否迁移」的新逻辑都必须先重建这个机制，不能假定它有效。
3. 旧版本在数据目录旁留下过侧车锁（`session.json.lock` 等）、`session/.lock/`、
   `users/.lock/`、`yysls/.lock` 等落点；当前代码只写 `config/session/.locks/`。
   这些残留用 `scripts/one_off/cleanup_obsolete_locks.py` 一次清掉（退出所有节点后运行）。
4. `logs/` 下的四类产出（结果 JSON、逐任务日志、批量报告、调律说明）都只增不减，
   没有保留期也没有统一清理入口；历史库里分别用 `result_path`、`log_path`、`report_path`、
   `markdown_path` 指向它们，清理时要一并考虑记录。

## 相关文档

| 文档 | 内容 |
|------|------|
| [04-config-layering.md](04-config-layering.md) | system/local 分层、`session.json` 节点划分与用户偏好归属 |
| [05-content-cache.md](05-content-cache.md) | 修订缓存、运行快照与基底状态 |
| [../31-models/03-session-and-context.md](../31-models/03-session-and-context.md) | DSL `session`/`context` 与用户目录文件职责 |
| [../31-models/04-profile.md](../31-models/04-profile.md) | Profile 四模型与数值语义 |
| [../38-platform/02-instance-access.md](../38-platform/02-instance-access.md) | 主实例/只读实例边界与用户执行锁 |
| [../39-runtime/01-tuning-history.md](../39-runtime/01-tuning-history.md) | 调律历史模型与投递状态 |
