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

旧存储是 `session.json` 顶层 `wf_configs` 节点。首次访问任一 `wf_configs` API 时
一次性迁出到 `workflow.json` 并删除旧节点（经 `SessionStore.delete_node`，其余节点
不动）；可重复执行，没有旧节点即无事发生。不做兼容回读：旧节点在迁移后不再出现。

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
  其余 session.json 内容不变。
- 离线包保留/覆盖开关分别满足第 2 节的合并语义。
