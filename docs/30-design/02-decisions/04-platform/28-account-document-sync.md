# 账户级文档：全局任务参数与批量配置上云

> 状态：已实现（2026-11-17，基线 v0.15.15）。

## 1. 需求

换机器或重装后，全局任务参数（日常页保存、批量/调律共享的按脚本参数）与批量配置组
定义不随账号恢复，只能手工重配。两者上云：换设备登录账号即随文档通道恢复。

用户级任务参数（`users/{名}.json` 的 `workflow_params`）已随用户资料通道
（`account_users` 表）同步，本需求只覆盖**账户级**两份定义。

## 2. 方案

| 云端 kind | 本地落点 | 结构 | 咽喉模块 |
|-----------|----------|------|----------|
| `workflow`（label `__global__`） | `session/workflow.json` | `{"workflow_params": {wf_id: 参数}}` | `core/config/wf_configs.py` |
| `batch`（label `__global__`） | `session/batch.json`（既有文件，结构不变） | `lvjiang.batch` v2 配置组定义 | `core/batch/config.py` |

- 两者都是 core 固有账户级文档：`core/sync/core_documents.py` 在包导入时登记，
  kind 加入 `RESERVED_KINDS`（插件不得抢占）。同步枚举、增量、水位、落盘全部复用
  `core/sync/engine.py` 现有机制，服务端零改造。
- **冲突不留副本**：`SessionDocument.conflict_copy=False`——任何裁决路径上输掉的
  本地版按云端最新版直接覆盖丢弃（参数与配置可接受丢失，不堆 `.conflict-*` 文件）；
  推送撞 CAS 冲突时也直接认云端版。
- 写路径标脏：`set/update/delete_wf_config` 与 `BatchConfigStore.save` 落盘后
  `mark_dirty("__global__", kind)`，磁盘戳兜底不变。
- 手机下发：离线包 `_SESSION_FILES` 增加 `workflow.json`；保留手机任务参数时以 PC
  为底、手机已有 `workflow_params` 按 `wf_id` 覆盖（与旧 session.json `wf_configs`
  合并语义一致），不保留时整份落地。`batch.json` 沿用既有「保留手机批量配置」开关。

## 3. 迁移

旧存储是 `session.json` 顶层 `wf_configs` 节点。桌面/设备启动状态门禁与参数 API
均执行同一迁移；离线安装仍独立读取手机原状态，不能假设 getter 已运行。

- `workflow.json` 存在即权威，包括空文档/空参数；仅新文件不存在时读旧节点。
  损坏 JSON、非对象参数停止覆盖，不能当空配置；0/False/空对象不按真假丢弃。
- PC 打包只从快照派生标准文档，不迁移或修改 PC 源文件。手机完整 staging 校验后，
  锁内读取新旧原件，再计算最终参数。保留时以 PC 为底、手机按整个 wf_id 覆盖；
  不保留时用 PC 值（包括空），包里缺文档则不碰手机参数。批量配置开关独立。
- 编辑、迁移、安装及文档下行共用稳定路径锁；参数提交检查读取摘要。先将原字节保存于
  本机 `session/.migrations/workflow`，再原子写新文件，最后删除旧节点；摘要冲突也
  保存旧快照而不覆盖后续编辑。失败只恢复仍为本次输出的文件，不覆盖之后的编辑。
- 不宣称整包事务或整体恢复：配置镜像/重载失败可能已部分应用，应明确报错并重试。
  校验失败不触发额外配置迁移。场景升级原件不被后续离线镜像删除。
- 云端协议/账户确认/Profile 基线不变；下行期间若文件被编辑则保留编辑并重试，失败轮
  不清脏/抬文档水位。既有云端新版本覆盖政策不变，不增加 Android 云端文档同步。

## 4. 边界

- `session.json` 其余内容（settings、名册、daily、批量草稿）保持本机，不入通道；
  批量运行草稿是运行态，只存在于 `interface.json.batch` 与内存。
- 不做端到端加密（只有 `notes` 加密）；「清除云端同步数据」会连带清掉这两个云端
  文档（本地保留）。
- 手机端不直接消费这两种云端文档：手机任务参数仍走 PC 下发；手机云端同步只覆盖
  总览数据。
- 多机同时改不同脚本的参数按**整文档**裁决，输方整份丢弃（可接受丢失的前提）。

## 5. 验收

- 保存全局参数 / 批量配置组后一轮同步上推对应文档；另一台机器登录后拉到同名文件。
- 本地旧版被云端覆盖时不产生 `.conflict-*` 文件。
- 含旧节点的 session.json 首次读写参数后，参数出现在 workflow.json、旧节点消失、
  其余 session.json 内容不变，原字节留档，新文件（含空）优先且损坏停止。
- 离线包保留/覆盖开关分别满足第 2 节的合并语义。

## 6. 预埋约束：接入 Android 云端文档同步前必须先落基线

本节记录**当前未实现**的一步及其前置条件，避免将来「给手机也开文档同步」时
直接把离线包产物送进 mtime 裁决。

**现状（事实）**：手机上的 `workflow.json` / `batch.json` / `users/*.json` 由 PC
离线包直接覆盖落盘（`core/offline_bundle.py`），文件 mtime 是**打包/落盘时刻**；
手机侧不写 `account.json` 的 `document_sync`（离线包明确排除 `session/account.json`，
见 `LOCAL_ONLY_CONFIG_FILES`），也没有 `sync_documents` 入口——`_sync_round`
（`core/ondevice/account_api.py`）只走 `/api/sync/state` 与 `/api/sync/profile/*`，
这四个受 `X-Lvjiang-E2E-Docs` 门禁的文档端点手机根本不碰。

**接入时会踩的坑**：`sync_documents` 首次运行 `first_run` 强制走
`_sync_documents_full`（`core/sync/engine.py`），裁决规则是「本地 mtime ≥ 云端
`updated_at` 则本地胜」。刚被离线包覆盖的文件 mtime 必然很新，于是手机会用
**包里那份旧配置**重新加密并反推云端，把 PC 的新版本盖掉；而且手机此时没有
`document_sync` 基线（`remote_document_id` / `stamps` / `e2e_migrated_key_fp` 全空），
这套裁决没有任何参照点。

**前置约束**（接入该功能前必须先做，缺一不可）：

1. 手机首次拿到文档时**先写基线而不是先裁决**：以云端当前 `documents_head` 作为
   `remote_document_id`、把落盘时刻记进 `stamps`，明确表达「本机这份是刚跟随云端
   拿到的副本」，而不是「本机刚改过」。
2. 首轮同步走「跟随云端」语义（等价于 `sync_definition(allow_push=False)` 的镜像端
   只拉不推），由用户显式选择后才切换为可上行。
3. 离线包落盘后若确实要把手机备份当作新数据，走既有的覆盖确认路径
   （`overwrite_warnings` + 备份），不得让 mtime 静默胜出。

在此之前，Android 侧**不得**接入 `sync_documents`；本文第 4 节「手机端不直接消费
这两种云端文档」仍是当前有效边界。
