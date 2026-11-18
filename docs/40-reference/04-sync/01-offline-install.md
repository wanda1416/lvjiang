# 离线安装契约（PC → Android 配置同步）

本文描述 PC 打包与 Android 端安装离线同步包的当前事实。权威实现在
`src/lvjiang/core/offline_bundle.py`（打包与安装）、`src/lvjiang/core/ondevice/offline.py`
（设备端入口）、`src/lvjiang/core/config/workflow_document.py`（任务参数提交）、
`src/lvjiang/core/fs_util.py`（锁与原子写）。

## 打包：进入离线包的内容

`build_offline_bundle`（`src/lvjiang/core/offline_bundle.py`）按消费契约白名单收集文件，
不递归复制整个配置层：

- `system` / `local` 两层按 `_LAYER_PATTERNS` 白名单收集（app.yaml、ocr.yaml、场景、布局、
  工作流 `.wf`、参照图、模板、地图），布局仅携带所选布局及其继承依赖；
- `session` 只带 `session.json`、`interface.json`、`batch.json`、`workflow.json` 与
  `users/*.json`，以及插件钩子声明的会话文件与 SQLite 快照（当前 `_DATABASE_FILES`
  为空，实际 DB 由 app 钩子声明）；
- **Profile 不进入离线包**：`profile.db`/`profile.yaml` 等总览数据不打包，manifest 显式
  声明 `"profile": false`；总览由账号登录后云端同步，与 PC 在线行为一致。设备端安装时
  检测到包携带 profile 数据会直接拒绝（`install_offline_bundle` 的 `carries_profile` 检查）。
- **本机身份不进入离线包**：`LOCAL_ONLY_CONFIG_FILES`
  （`session/instance.id`、`session/account_token`、`session/account.json`）是本机登录态，
  打包侧不得收进清单（打包后自检会拒绝泄漏），安装侧一个字节都不碰。`install_id`
  等设备身份由此保护，一次 PC→手机同步不会把登录态连根拔掉。

## 两个保留开关

设备端同步 UI（`src/lvjiang/ui/mobile/offline.py`）与协议 `offline_sync_begin` 都要求
分别显式选择，默认均勾选：

1. **保留手机任务参数**（`preserve_task_params`）：保留各用户的任务设置（含自动调律
   等插件任务）与手机独有用户；脚本、布局和 Profile DB 仍更新。取消时按 PC 完整
   覆盖任务参数并删除包内没有的手机用户文件。旧 APK 缺少该标志时拒绝同步，提示先
   更新 APK。
2. **保留手机批量配置**（`preserve_batch_config`）：保留手机整份批量配置组定义；取消
   时落地包里的 `batch.json`。手机已有的 `batch.json` 在开启该开关时整份不动；手机
   还没有该文件时仍会落地包内配置组定义，否则第一次同步拿不到批量配置。本次运行
   草稿始终保留。

## staging 校验：内容与时机

`install_offline_bundle` 先把整包解压到配置目录旁的临时 staging 目录
（`.offline-stage-*`），在 staging 内完成全部校验，校验点包括：

- `manifest.json` 的 `format` 与 `app_version` 必须与本机运行版本一致（不一致提示更新 APK）；
- 压缩包成员集合必须与清单完全一致、无重复；展开总量不超过 256 MiB；
- 逐文件 SHA-256 校验；路径必须为 `config/{system,local,session}/...` 的合法相对路径；
- 清单不得包含本机独占文件与 profile 数据；
- 包内 `interface.json` 的活动选择必须与清单 username/layout 一致；
- 所有 `.db` 快照做 SQLite `PRAGMA integrity_check`；任务参数文档经
  `read_workflow_source` / `validate_workflow_document` 校验（必须为按 wf_id 登记的对象）。

**校验失败发生在 staging 阶段，此时手机配置一个字节都没有被改动**，错误信息为
「原配置未覆盖」。

**读取手机状态发生在 staging 校验全部通过之后**：`src/lvjiang/core/offline_bundle.py`
在 staging 校验完成处有显式分界注释（「从这里才读取/计算手机最终状态」）。此后才在
`configuration_lock`、`config_write_lock` 与 `session.json`/`workflow.json`/`batch.json`
的 `locked_paths` 保护下读取手机当前 `WorkflowSource` 并开始写入。

## 安装语义：分域逐文件，无整包事务回滚

安装不做整目录交换、不做整包备份与回滚。同步是可重复操作，失败重跑一次即可。
写入按三个域分别处理：

- `system` / `local`：**镜像**。先删除手机上包中没有的文件与空目录（`.migrations`
  目录除外，本地升级原件不能丢），再逐文件写入。镜像删除是上游删除/移动过的
  `.wf`、场景、布局必须在手机上消失的唯一机制，否则会留下幽灵副本。
- `session`：**只覆盖清单内的文件**，同目录其他文件一律不动。`.db` 用 SQLite
  `backup` 写入目标库（因目标库可能正被打开，不是为了回滚）。
- 本机独占文件：一个字节都不碰。

### 任务参数（workflow.json / session.json / users）按 wf_id 覆盖

`commit_workflow_state`（`src/lvjiang/core/config/workflow_document.py`）处理参数提交：

- `workflow_params` 是**按 wf_id 登记的对象**，合并以 PC 文档为底、手机同名 wf_id
  覆盖（保留任务参数时）；新任务采用 PC 参数，已有任务的手机参数整项保留。
- **缺失文档与空文档是两回事**：包内缺失 `workflow.json`（`document is None`）表示
  这次快照没有携带参数文档，不等于显式空文档——不会清空手机参数；包内存在空
  `workflow.json`（`{}` 或仅空 `workflow_params`）是显式空参数，保留开关关闭时会按
  空覆盖。同样，缺少任务参数文档的包不会删除手机用户文件，只有取消保留开关时
  才按包内名册清理。
- 提交前先把手机参数原件存档到 `session/.migrations/workflow/`（按内容哈希命名），
  读取后文件被外部修改过则拒绝写入并提示重试；写入失败只还原仍等于本次输出的
  文件，原件留在本机供核对恢复。

### 文件锁与原子写各自的保护范围

- **文件锁**（`src/lvjiang/core/fs_util.py` 的 `interprocess_lock` / `locked_paths`，
  锁文件在 `config/session/.locks/`）保护的是**并发**：同一目标的多进程/多线程读改写
  被串行化，跨进程锁统一在 `configuration_lock` 之后获取。它不保护崩溃一致性。
- **原子写**（`atomic_write_bytes` / `_write_bytes`：同目录临时文件 + `os.replace`）
  保护的是**单文件**：目标不会出现"写了一半"的中间态。它不保护跨文件的一致性。

两者都不提供跨文件的整包事务。因此进程崩溃或中途失败时，**已完成写入的那些文件
保持新值，未写入的保持旧值**——可能出现半完成的多文件状态（例如部分 `system`
文件已镜像、部分 session 文件已覆盖）。这是设计内的边界，不是缺陷。

### 失败后的核对与重试

安装提交后失败时，异常携带 `offline_configuration_changed = True`
（`src/lvjiang/core/offline_bundle.py` 在 except 中标记；staging 校验失败则为 False）。
设备端 `apply_sync`（`src/lvjiang/core/ondevice/offline.py`）据此给出不同提示：

- 校验失败（未落盘）：「原配置未覆盖」，直接重试即可。
- 提交后失败：「配置可能已部分应用，请重试；参数原件保留在本机」。设备端会先尝试
  `reset_configuration()` 重载/重建配置单例，但不谎称已恢复。

失败时**可能已安装的域**：镜像删除与逐文件写入按序执行，`system`/`local` 可能已部分
镜像更新，`session` 清单内文件可能已部分覆盖，任务参数提交自带「先存档、失败还原
本次已写文件」的小范围保护。用户核对与重试方式：

1. 手机端「离线任务」页查看最近一次同步状态与 `session/offline.json`（本次快照摘要）；
2. 直接重新执行一次完整同步——安装是幂等可重复的，重跑会把剩余文件补齐并使
   system/local 与包一致；
3. 手机任务参数原件在 `session/.migrations/workflow/` 留档，可人工核对；取消参数保留
   导致的手机用户删除不自动恢复，重试时确认开关选择。
