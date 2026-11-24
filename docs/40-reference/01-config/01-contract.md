# 契约 B：配置与迁移

本文是配置分层、任务参数文档（workflow.json）迁移与版本号体系的权威契约。
所有断言均注明源码相对路径（相对 `src/lvjiang/`）。只描述当前事实。

## 一、配置分层：system / local / session

| 层 | 目录 | 职责 | 读写入口 |
|----|------|------|----------|
| system | `config/system/` | 已安装的官方内容（含在线下发安装的修订） | `core/config/resolver.py` |
| local | `config/local/` | 用户覆盖层（影子文件 + 键级 diff） | `core/config/resolver.py` |
| session | `config/session/` | 纯运行态与用户数据，**不经 resolver** | `core/config/session.py`（session.json）、各独立模块 |

层职责与目录拓扑声明见 `core/paths.py` 与 `core/config/resolver.py` 模块文档字符串；
分层总览另见 `docs/30-design/01-architecture/overview/04-config-layering.md`。

### 1.1 读取优先级与覆盖语义

- **读：`local > system`，两种模式一致**（`core/config/resolver.py` 模块文档、
  `ConfigResolver.resolve_read`）。local 恒为最高优先级：用户自己改过的东西，
  任何在线下发都不该盖掉（`resolver.py` `resolve_read` 注释）。
- **写：按模式路由**。开发模式（`LVJIANG_DEV_MODE=1` 或仓库存在 `.git`，判定见
  `resolver.py` `_compute_dev_mode`）写 system 全量；用户模式写 local。在线安装器
  独立负责 system 的事务替换，不写 local（`core/config/update_store.py` `_install`）。

### 1.2 两类文件、两种合并语义

1. **实体文件（一物一文件）→ 整文件影子**：local 存在同名文件即整个顶掉
   system，不做内容合并（`resolver.py` `resolve_read`、模块文档）。
2. **聚合键值文件 → 键级 diff 深合并**：读取时 system 基底 ← local diff 合并
   （`resolver.py` `load_merged`）。合并规则（`core/config/merge.py`）：
   - dict 递归合并；列表与标量整键替换；
   - 每层 dict 支持 `"__deleted__": [key, ...]` 删除键；
   - 注册表列表（`REGISTRY_LIST_PATHS` 声明）在 local 只存
     `__added__` / `__removed__` / `__order__` 增量，可按身份字段对齐
     （`REGISTRY_LIST_IDENTITY`），避免 local 冻住整张表；
   - 写回时用户模式由 `compute_diff` 逆向求 diff（diff 为空则删除 local 覆盖
     文件），开发模式全量写 system（`resolver.py` `save_merged`）。
   - **`save_merged` 入参必须是完整文档**，只传部分键会把其余键判成用户删除
     （`resolver.py` `save_merged` 文档字符串）。

列表存在两种语义：枚举设定（整键替换）与注册表（存增量），区分见
`resolver.py` 模块文档与 `merge.py`；core 的声明表只含 core 自己的路径，
插件经 `register_registry_list_paths` / `register_protected_list_paths` 注册。

### 1.3 写入行为要点

- **空操作检测**：写入内容与「不写会读到的那份」语义一致时不落盘（用户模式下
  防止「随手点保存」给整个实体生成 local 影子、从此冻住系统更新；
  `resolver.py` `_write_is_noop`）。文本比较不做逐字节比对（归一换行、忽略
  content_version 字段）。
- **带 content_version 的实体实际修改时自动递增该字段**，新文件从 1 起步；
  修订号不参与来源仲裁，local 影子恒为最高优先级（`resolver.py` `write_entity`）。
- **删除保护**：用户模式下删除/重命名 system 层实体抛
  `SystemContentProtected`（`resolver.py` `ensure_entity_deletable` /
  `delete_entity`）；停用走激活机制，不删定义。`revert_entity_to_system`
  允许放弃 local 影子回到系统版本（仅当 system 存在同名内容）。
- **写锁顺序全仓库统一**：进程内 `configuration_lock` → 跨进程文件锁
  （`core/config/content.py` `configuration_lock` / `config_write_lock`）。
- 保存前乐观并发检查见 `core/config/conflicts.py`：读取时记录基线字节，
  写入前比对，磁盘被改则弹窗确认或抛 `ConfigWriteConflict`（冲突不静默覆盖）。

### 1.4 「场景归属、排序、存在性、字段覆盖」不是同一维度

以 `config/system` 的场景/布局布局为例（完整规则见
`docs/30-design/01-architecture/scene/01-scene-layout-definition.md`，此处不复制）：

- **存在性与归属**：目录是唯一事实源——`scenes/{group}.yaml` 是分组定义，
  `scenes/{group}/{scene}.yaml` 是场景文件；文件落盘即注册
  （`core/config/versioning.py` `register_versioned_dir("scenes", ..., depth=1/2)`）。
- **排序**：顶层顺序由 `scenes/_meta.json` / `layouts/_meta.json` 声明
  （`versioning.py` `register_versioned_file`）；local 的 `_meta.json` 是排序补丁，
  不是内容覆盖。
- **字段覆盖**：字段级补丁走独立文件（如 `local/layouts/{key}.yaml`），与
  「local 同名文件整体顶掉 system」的实体影子、`_meta.json` 的排序补丁是
  三个不同机制。
- **启停/展示**：`disabled` 是场景文件自身字段，只影响 UI 展示，定义仍注册；
  用户顺序/启停偏好存 session（`docs/30-design/01-architecture/overview/04-config-layering.md`
  第四节），不写回配置层。

结论：给某个维度加能力时不得借用另一维度的机制（例如用字段覆盖实现排序、
用删除实现停用）。

## 二、任务参数文档 workflow.json 与旧 wf_configs 迁移

### 2.1 存储位置与结构

- 新文档：`config/session/workflow.json`（`constants.py` `WORKFLOW_CONFIG_PATH`）。
  结构为 `{"workflow_params": {wf_id: {参数 dict}}}`，一级 key 只有
  `workflow_params`（`core/config/wf_configs.py` 模块文档、`_NODE_KEY`）。
- 旧存储：`config/session/session.json` 顶层 `wf_configs` 节点
  （`wf_configs.py` `_LEGACY_NODE_KEY`）。
- workflow.json 是**账户级同步文档**（`core/sync/core_documents.py` 登记
  `kind=workflow`、云端 `label=__global__`）；冲突输方不留副本，按云端最新版
  直接覆盖（`wf_configs.py` 模块文档）。

### 2.2 读取顺序与权威性

`core/config/workflow_document.py` `read_workflow_source`：

1. **workflow.json 存在即权威——包括空配置**。只要文件存在（哪怕是空对象），
   就读它并校验 `workflow_params`，**不再回读 session.json 的 `wf_configs`**
   （`workflow_document.py`：`originals[workflow_path] is not None` 分支优先）。
2. 仅当 **workflow.json 不存在**且 session.json 有 `wf_configs` 节点时，才把旧
   节点内容当作迁移来源（`workflow_document.py` `elif "wf_configs" in session`）。
3. 两者都没有时视为「包内缺文档」（`document=None`），不等于显式空文档
   （`workflow_document.py` `commit_workflow_state` 文档字符串）。

### 2.3 损坏不能当空

- workflow.json 顶层不是对象、或 `workflow_params` 不是「按 wf_id 登记的对象」
  时，`validate_workflow_document` / `read_workflow_source` **抛
  `ValueError`，停止参数覆盖**——损坏的 workflow.json 绝不会被当成空配置
  然后被新写入覆盖（`workflow_document.py` `_object`、
  `validate_workflow_document`）。
- session.json 顶层同理：顶层非对象抛错停止覆盖（`workflow_document.py` `_object`）。
  （注意：`core/config/session.py` `SessionStore._read_disk` 对普通运行态节点
  是「解析失败按空配置处理」，那是另一条路径；任务参数迁移走的
  `workflow_document.py` 是严格路径。）

### 2.4 原字节保护与冲突不覆盖

`commit_workflow_state`（调用者持有 session.json 与 workflow.json 两路径锁；
`core/config/workflow_document.py`）：

1. **先存档原字节**：读取时记录两文件的原始字节（`originals`）；提交前把每个
   原件写入存档 `config/session/.migrations/workflow/{sha256}-{文件名}`。
   存档已存在但内容不同 → `RuntimeError("任务参数原件存档冲突，停止写入")`
   （`workflow_document.py` 存档段）。
2. **冲突不覆盖**：提交前核对磁盘当前内容仍等于读取时的 `originals`；
   期间被改过则 `RuntimeError`（旧快照已留档，未覆盖后续编辑）；
   写入过程中再次核对，失败即回滚已写入文件为原件（或删除新文件）并重抛
   （`workflow_document.py` `changed` 检查、写入 try/except 段）。
3. **相同值不写**：与磁盘内容语义相同的输出跳过（`workflow_document.py`
   `writes` 过滤）。迁移只在 session.json 仍有 `wf_configs` 时执行，可重复
   （`migrate_workflow_document`）；旧节点迁移成功后从 session.json 删除。
4. 写入使用原子替换（`atomic_write_bytes`，`core/fs_util.py`）。

### 2.5 中断恢复

- 迁移三步「存档原字节 → 写新文档 → 删旧节点」没有独立的事务日志；中断后
  依赖幂等：任意一步没完成，下次读取重走 `read_workflow_source` +
  `migrate_workflow_document`，条件仍满足则重做（`workflow_document.py`
  `migrate_workflow_document`，`wf_configs.py` `_read_params` 每次读前调用）。
- 中断不会丢数据：旧节点要么还在 session.json（未删），要么 workflow.json 已
  写成（原字节已在 `.migrations/workflow/` 存档）。两种中间态下次读取都能收敛。
- 人工修复边界：**仅当** workflow.json 存在但损坏（校验抛错）时程序拒绝继续，
  需要人工处理——从 `.migrations/workflow/` 存档核对原件，修复或移除损坏文件
  后重试；程序不会自动用旧节点或空配置顶掉损坏文件。

## 三、五种版本号

| 名称 | 形态 | 定义位置 | 用途 | 语义边界 |
|------|------|----------|------|----------|
| 应用版本 app_version | `X.Y.Z`、`X.Y.Z.N`、`X.Y.ZbN/rcN` | `core/versioning.py` `application_version` | 客户端发布版本 | 配置只比较三段系列，软件升级另按完整版本比较（`config_version`） |
| 在线系列 app_series | 三段 `X.Y.Z` | `core/versioning.py` `application_series`；包清单 `core/updates/bundle.py` `make_manifest` | 在线配置包归属系列；预发布基底各自隔离（`update_namespace`） | 配置包与客户端系列不匹配拒绝安装（`update_store.py` `_install`） |
| 在线修订号 revision (rN) | 正整数，r0 为基线 | `core/updates/bundle.py` 清单 `revision`；归档目录 `update_store.py` `_archive_directory` | 在线公开配置包递增修订；安装只比较修订号，不同就从 r0 重建整棵树（`update_store.py` `_install`） | 只作用于 system 层在线内容，不触碰 local |
| content_version | 文件内顶层字段（JSON key / YAML 顶层行），正整数 | `core/config/versioning.py` `CONTENT_VERSION_KEY`；`scripts/ci/add_content_version.py` 补齐存量 | 实体文件内部修订号：实际保存自动递增，供修订索引（`core/config/content.py` `rebuild_content`）与读取缓存失效使用 | **不参与来源仲裁**（local 影子恒优先）；不在 UI 展示；`.wf` 与模板 PNG 等不带此字段、随整包修订下发（`versioning.py` 注册表 `has_content_version=False`） |
| schema_version | 各文件/协议自定义整数 | 各格式模块（如 `core/db/schema.py`、`core/updates/bundle.py` 清单协议） | 声明**结构格式**版本，格式不兼容时由消费方校验拒绝 | 与 content_version 互不影响（`core/config/versioning.py` 模块文档：结构 schema_version 不受 content_version 影响） |

要点区分：

- content_version 是**文件内容修订**（谁改了这个文件几次），revision 是**在线包
  修订**（这批 system 树是第几版），二者互不推导。
- `scripts/ci/add_content_version.py` 只给 `config/system` 下参与版本管理的实体补
  content_version（清单来自 `versioning.py` 注册表 + 插件注册，脚本不自己维护
  一份），幂等、`--check` 供 CI；改 `config/system` 的提交须跑
  `--check` 并把受影响文件 content_version 加 1（AGENTS.md 第五节）。
- 应用版本/系列/修订号由发布与在线更新流程维护，content_version 由保存动作
  自动维护；不要手工把 revision 写进配置文件，也不要把 content_version 当
  版本发布依据。

## 四、人工修复边界（汇总）

程序自动处理的：迁移重放、中断收敛、空操作跳过、冲突确认后覆盖。
必须人工介入的：

1. workflow.json 或 session.json 在迁移路径上校验失败（损坏不能当空，
   `workflow_document.py` 抛错）——核对 `.migrations/workflow/` 存档后修复；
2. 存档冲突（同 hash 路径已存在但内容不同，`workflow_document.py`）——说明
   有两份不同原件竞争同一存档位，需人工核对取舍；
3. `ConfigWriteConflict` 反复出现（`conflicts.py`）——另一进程持续改盘，先
   停掉竞争方再保存。

## 关联文档

- 分层总览：`docs/30-design/01-architecture/overview/04-config-layering.md`
- 修订缓存：`docs/30-design/01-architecture/overview/05-content-cache.md`
- 用户侧恢复步骤：`docs/60-guide/05-recovery/01-config-migration.md`
