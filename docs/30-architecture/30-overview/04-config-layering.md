# 配置分层：system / local 与更新归档

配置读取只有 system/local 两层；session 存运行数据，updates 存更新归档与事务状态。
读写入口为 `core/config/resolver.py`，安装架构见 [官方配置增量更新](05-config-update-architecture.md)。

| 层 | 目录 | 职责 | 进 git |
|----|------|------|--------|
| system | `config/system/` | 已安装的官方预置、累计增量和有效会员内容；开发仓库仅维护公开源 | 仅源文件 ✅ |
| updates | `config/updates/` | r0/rN、会员密文及安装记录，不参与配置读取 | ❌ |
| local | `config/local/` | 用户覆盖层：影子文件 + 键级 diff | ❌ |
| session | `config/session/` | 纯运行态，不经 resolver（见 `core.config.session`） | ❌ |

上表的“进 git”指公开主仓库。开发版把 `config/local/` 单独作为与 `premium`
配套的资源仓库：其中可以包含高级功能所需的场景、布局、脚本和开发数据；公开版
仍把同一路径用作用户覆盖层。`config/session/` 是另一份独立的运行数据仓库。
匿名安装标识虽然落在 local 路径下，仍只属于单机运行态，应排除在 Git 跟踪之外。

**读**：`local > system`，两种模式一致。
**写**：按模式路由——开发模式（`.git` 存在或 `LVJIANG_DEV_MODE=1`）写 system 全量；
用户模式写 local diff。在线安装器单独负责 system 的事务替换，不写 local。

---

## 一、两类文件

### 实体文件（一物一文件）

整文件影子：local 存在同名文件即整个顶掉 system。用户自建的
local 文件可直接删除；system 官方文件不允许用户删除，也不再使用
`.deleted` 墓碑隐藏。

| 位置 | 内容 |
|------|------|
| `scenes/*.yaml` | 场景定义 |
| `workflows/**/*.wf` | 工作流脚本 |
| `layouts/{布局}/{场景}.json` | 布局坐标 |
| `yysls/tuning_rules/*.yaml` | 调律规则 |
| `yysls/base_groups/*.yaml` | 基础规则组（材料/扫描处置） |
| `yysls/gear_sets/*.yaml` | 一搭配一文件，约束随搭配保存，同路径 local 整体覆盖 |
| `yysls/graduation/*.json` | 毕业率方案 |
| `references/**/*.png` | 参考图 |

### 聚合键值文件（一文件多条目）

键级 diff 深合并：dict 递归、列表与标量整键替换。

| 文件 | 顶层键 | 主要消费方 |
|------|--------|-----------|
| `app.yaml` | `input_simulation` / `delay_params` / `envs` | `load_app_config()` |
| `scenes.yaml` | `schema_version: 2` / `scenes` | `core/scene_config.py`、`scene_registry.py`、`scene_definition.py` |
| `layouts.yaml` | `schema_version: 2` / `layouts` | `core/layout_manager.py`、`screen_calib.py` |
| `ocr.yaml` | OCR 识别参数与文本规范化规则 | `core/ocr_config.py` / `core/ocr_cleaner.py` |
| `yysls/game_config/*.yaml` | 七份领域配置合并为统一视图 | `apps/yysls/config/game_config_files.py`、`manager.py` |
| `yysls/tune_config.yaml` | `quality_thresholds` / `switches` | `core/tuning_rules/manager.py` |

游戏配置目录现有七个文件；出装搭配独立放在 `yysls/gear_sets/`，不并入统一视图，
见 [04-build-calculator.md](../39-runtime/04-build-calculator.md)：

| 文件 | 内容 |
|------|------|
| `basic.yaml` | 基础配置、字体/装备卡片展示配置 |
| `seasons.yaml` | 等级、赛季 |
| `affixes.yaml` | 词组、别名、词条上限与可用部位 |
| `equipment.yaml` | 装备基础属性、命名系列、武器类型 |
| `martial_arts.yaml` | 武学 |
| `schools.yaml` | 流派 |
| `playstyles.yaml` | 玩法 |

`scenes.yaml` 的当前结构版本为 2，与 `layouts.yaml` 一样使用单一领域顶层键：

```yaml
schema_version: 2
scenes:
  activity:
    name: 活动
    items:
      - activity_main
      - activity_mengzhu
    disabled:
      - activity_mengzhu
```

`disabled` 场景只从场景管理 UI 隐藏，定义仍注册并可被旧布局、工作流引用。
仅接受当前 scenes 结构；无版本号的空覆盖、元数据与 scenes 增量仍有效。
明确 v1 或旧 layout_scenes/group_names 字段报不兼容，不自动转换或落盘。
system、local 分层验证后合并，保存也验证旧 local 覆盖。

`layouts.yaml` 也使用 schema v2，但不提供旧格式兼容或自动迁移：

```yaml
schema_version: 2
layouts:
  android:
    name: 安卓布局
    canvas: {x_ratio: 0.0, y_ratio: 0.0, w_ratio: 1.0, h_ratio: 1.0}
  android_cast:
    name: 投屏布局
    extends: android
    canvas: {x_ratio: 0.0, y_ratio: 0.04, w_ratio: 1.0, h_ratio: 0.92}
```

`layouts` 下的 key 是不可变的存储标识，同时用于
`layouts/{key}/`、`templates/{key}/`、会话激活值和运行方案；`name` 只用于
UI 展示。key 和 name 都必须全局唯一，另存为不允许覆盖已有 key。

### 例外：telemetry/ 不是覆盖层镜像

`config/local/telemetry/`（install_id + 调律事件本地缓冲，见
`core/telemetry/paths.py`）不是 local 对 system 的覆盖，是纯本地运行态
——system 层没有对应内容。选它而不是 `config/session/`，是因为用户会
把 `config/session/` 打包发给作者排查问题（见
docs/60-userguide/08-feedback-and-issues.md），标识落在那里会让匿名性
失效；而这个目录又不适合套用 local 的影子/diff 语义（没有系统内容可
覆盖）。resolver 的 `enumerate_entities`/`load_merged` 只按已知
`rel_dir` 枚举，不会遍历到这个目录，代码层面无冲突，纯粹是文档提醒：
新增同类"纯本地状态"目录时不要往这层套。

### 例外：参考图空间

`references/{空间}.yaml` **不走 resolver 的聚合接口**，
`core/reference_db.py` 自带一套条目级 diff（`references` + `deleted` 列表，
`meta_schema` 整列表替换）。新增同类配置前先确认是否该并入 resolver。

空间列表没有名册文件，由**目录扫描**得出：
`config/system/references/*.yaml` ∪ `config/local/references/*.yaml`，
文件名即空间名。落一个 yaml 就是新增一个空间，作者新增的系统空间无需用户
删除本地文件即可见。两层同名 = local 是 system 的覆盖层，不是第二个空间。

system 层扫出的空间即**系统空间**（`is_system_space()`）：用户模式下可以改其中
内容，但不能删除该空间——图库管理器的空间下拉将其置灰，「删除空间」按钮禁用
并把拒绝原因写进 tooltip。用户要独立的一套图，应当新建自己的空间。

`delete_space()` 的三条闸门（`can_delete_space()` 返回拒绝原因，空串即可删）：
空间必须存在、用户模式不得删系统空间、至少保留一个空间。用户模式只清 local 层；
开发模式两层一起清，避免 system 层删掉后残留 local 覆盖层变成孤儿空间。
删的是激活空间时自动改激活、写 session 并重载。

---

## 二、列表的两种语义

同样是列表，合并方式必须区分，否则不是丢用户改动就是冻住系统更新。

### 枚举设定 → 整键替换（默认）

`quality_thresholds.武器: [gold]`、`delay_params.*.range: [1.0, 1.2]`、
`base_attrs.*._first_affixes` 之类，用户就是要**覆盖**系统值，整键替换正确。

### 注册表 → 存增量（`REGISTRY_LIST_PATHS` 声明）

可增长的条目登记表。local 若存下完整列表，系统后续新增的条目就永远进不了
合并视图，用户除非删掉自己的 local 否则再也看不到更新。

`resolver.py` 的 `REGISTRY_LIST_PATHS` 常量表**只声明 core 自己拥有的路径**
——core.config 不认识任何插件领域词汇。插件私有配置文件的路径由插件自己
经 `register_registry_list_paths()` 注册（「import 即注册」，同
`builtin_modules`/`telemetry_modules` 的 `AppHooks.config_policy_modules`
约定）：

| 文件 | 路径 | 声明方 |
|------|------|--------|
| `scenes.yaml` | `scenes.*.items` / `scenes.*.disabled` | core（`resolver.py` 常量表） |
| `yysls/tune_config.yaml` | `base_rules`（仅为兼容旧 local diff 保留，现已无此键） | 插件（`apps/yysls/config/merge_policy.py`） |

local 形如：

```yaml
scenes:
  general:
    items:
      __added__: [my_scene]
      __removed__: [some_scene]
      __order__: [...]            # 仅在用户调过顺序时才写
```

读取时 `system 基底 − __removed__ + __added__`，再按 `__order__` 排序；
`__order__` 未提到的条目（系统新增的）排末尾并保持 system 相对顺序。

调律规则与基础规则组**不再有登记表**：存在性由 `yysls/tuning_rules/`、
`yysls/base_groups/` 目录决定，顺序由各文件的 `order`（升序、同序按 key，
预置项 10、20、…，新建默认 10）声明，规则的启停由文件自己的 `disabled`
声明。旧版本 `tune_config.yaml` 里的 `tuning_rules` / `base_rules` 由规则
管理器一次性迁移（禁用声明写进对应规则文件）后移除。

存量 local 里的普通列表仍按整键替换处理——已无法区分「用户主动删了某条」
和「那条当时还不存在」，硬转会误伤；用户下次保存时自动转成增量形式。

### 开发者的私有实体

开发模式默认写 system（随包发布）。`write_entity(..., layer="local")` /
`delete_entity(..., layer=...)` 允许开发者把某个实体明确放在 local——例如
只在自己机器上用的基础规则组（调律配置 → 基础规则页的"保存位置"列，普通
用户看不到这一列）。local 恒高于 system，所以同 key 的 local 组会在这台机器
上覆盖 system 版本；用户模式下 `layer` 只能是 local，不是给用户开的口子。

---

## 三、删除：默认禁止

**system 目录里的东西，local 用户一律不能删除**——除非把自己切成开发模式
（`LVJIANG_DEV_MODE=1` 或仓库带 `.git`），那时他就是 system 身份。
用户该做的是改值、复制、另存为、新建；想停用某项走**激活机制**，
而不是删掉定义本身：

| 想停用 | 正确做法 |
|--------|----------|
| 某条调律规则 | 规则文件里 `disabled: true`。用户模式停用没改过的系统规则时，local 只写一个**停用桩** `{key, disabled: true}`，正文继续跟随系统/远程更新；重新启用 = 删掉这个桩（手动删文件也行）。只有用户已经改过内容（完整 local 影子）时才在影子里改字段 |
| 某个系统布局 | 不选它即可（布局按需切换） |
| 某张系统参考图 | 新建图库空间，放自己的图 |
| 某个脚本不在日常页显示 | 脚本配置里取消勾选（存 session，见下） |

技术上：`compute_diff` 只会为**system 里存在的键**产生 `__deleted__`。
用户自建的条目不在 system 基底里，删除它压根不产生删除标记——因此
「默认禁止 `__deleted__`」恰好只挡住系统内容，用户自建内容仍可自由删除。

**`DELETABLE_PATHS` 目前是空表**——梳理下来系统内容没有一样是该让用户删的。
保留这个扩展点是为了将来真出现例外时有地方声明，不是给现在留口子。
未声明的删除会被拦下并记 warning。

实体文件同样受保护：用户模式下对 system 层实体调 `delete_entity` 会抛
`SystemContentProtected`。用户可以通过停用、取消展示、切换到自建内容或复制到
local 改造来获得所需行为，不允许删除系统预置文件。历史 `.deleted`
文件不再参与解析，可直接清理。

参考图库（`reference_db.py`，自带一套条目级 diff）同样拒绝删除系统条目：
想要一套自己的图请**新建图库空间**，而不是把系统图去掉。

### 列表型系统内容

列表走整键替换，绕开了 `__deleted__` 那条保护——用户存一份少了几项的列表
就把系统条目抹掉了。`PROTECTED_LIST_PATHS` 按**条目身份字段**比对，
系统条目缺失即补回（插回系统列表中的原下标），用户的新增、改值、重排
一概保留。

同 `REGISTRY_LIST_PATHS`，`resolver.py` 里这张表对 core 保持**空**——core
没有自己的受保护列表。以下三条都是插件私有配置，经
`register_protected_list_paths()` 由 `apps/yysls/config/merge_policy.py`
注册：

| 文件 | 路径 | 身份字段 | 声明方 |
|------|------|----------|--------|
| `yysls/game_config/equipment.yaml` | `weapon_types` | `name` | 插件 |
| `yysls/game_config/seasons.yaml` | `level_configs` | `level` | 插件 |
| `yysls/game_config/seasons.yaml` | `season_configs` | `season_number` | 插件 |

新赛季、新装备等增量内容用户可以自己加条目，不需要删任何系统设定。

开发模式全量写 system、可直删实体，不受以上约束——编排系统配置是开发者的职责。

### UI 侧：不给系统内容留删除入口

后端拦下删除后，用户点了按钮却静默失败是糟糕的体验。设置面板统一经
`apps/yysls/ui/game_settings/factory_guard.py` 判断选中条目是否为系统内容，
是则把删除按钮置灰并在 tooltip 说明替代方案（停用 / 只能改值）。
用户自建的条目不受影响，照常可删。

列表型系统内容（`weapon_types`、`level_configs`、`season_configs`）虽然走整键替换，
后端同样拦得住：`PROTECTED_LIST_PATHS` 按上表声明的条目身份字段把缺失的系统
条目补回，UI 置灰只是额外的一道提示。

---

## 四、用户偏好不进配置层

**顺序、启停、显示名这类用户偏好一律存 session，不写回系统配置。**
写回去会把系统后续新增的条目冻住——`workflows.yaml` 就是栽在这上面，
已于 0.5.4 作废。

日常脚本的三层职责：

| 层 | 由谁决定 | 存哪 |
|----|----------|------|
| 脚本全集 | 目录约定（`workflows/policy.py` 的 `WorkflowDiscoveryPolicy`） | 不可配置 |
| 默认是否展示 | 作者声明：`.wf` 的 `#% hidden: true` / 内置类 `HIDDEN` | 随脚本走 |
| 顺序 / 启停 / 显示名 / 性质 | 用户 | `session.json` 的 `daily.scripts` |

`daily.scripts.visible` 是**覆盖**而非全集，只记与作者声明不同的项，
因此系统新增的脚本自动出现，用户不用做任何事。

> `interface.json` 的 `ui_state` 保存界面状态及批量运行草稿；日常脚本偏好仍放在
> `session.json` 的 `daily.scripts`。

---

## 五、`save_merged` 的调用约定

**入参必须是完整文档**，不是「本次要改的那几个键」。

```python
doc = get_resolver().load_merged("app.yaml")   # 先取完整合并视图
doc["input_simulation"] = ...                  # 改需要改的键
get_resolver().save_merged("app.yaml", doc)    # 整个传回去
```

只传部分键会让其余顶层键在用户模式下被判成删除（现已被白名单拦下并记
warning），在开发模式下则会直接从系统配置里消失——后者没有保护，
因为开发者本就有权重排 system。

---

## 六、插件声明自己的合并策略

`REGISTRY_LIST_PATHS`/`PROTECTED_LIST_PATHS` 曾经把 `yysls/*` 的路径直接
写死在 `resolver.py` 的常量表里——`core.config` 因此"认识" `weapon_types`
该按 `name` 判同一性这类纯游戏领域知识。这是
`core` 不该背的债：往下所有插件的私有配置策略都会挤在同一张 core 常量表
里，core 和插件各自维护一套对同一份配置的理解，随时可能对不上。

现在这两张表在 `resolver.py` 里只声明 core 自己拥有的路径（目前只有
`scenes.yaml`），插件经 `register_registry_list_paths()` /
`register_protected_list_paths()`（`resolver.py` 里的两个函数）自己注册。
接入方式沿用 `builtin_modules`/`telemetry_modules` 已有的「import 即注册」
约定（见 `apps/base.py` 的 `AppHooks.config_policy_modules`）：

```python
# apps/yysls/config/merge_policy.py —— import 即触发注册，不做其他事
from ....core.config.resolver import (
    register_protected_list_paths, register_registry_list_paths,
)

register_registry_list_paths("yysls/tune_config.yaml", ("base_rules",))
register_protected_list_paths("yysls/game_config/equipment.yaml", {
    "weapon_types": "name",
})
register_protected_list_paths("yysls/game_config/seasons.yaml", {
    "level_configs": "level", "season_configs": "season_number",
})
```

```python
# apps/yysls/__init__.py —— hooks 声明该模块，插件加载时 register_hooks
# 会 import 一遍，注册动作在 import 时自动发生
hooks = AppHooks(
    ...,
    config_policy_modules=["lvjiang.apps.yysls.config.merge_policy"],
)
```

新增插件若也需要声明自己的注册表列表/受保护列表，照此模式新建一个
`config/merge_policy.py`（或等价模块）并挂到自己的 `config_policy_modules`，
不要往 `resolver.py` 的常量表里加路径。

---

## 七、在线配置安装

所有官方 system 文件均可通过累计增量下发，包括未知格式与已有 WF。客户端使用精确
应用系列索引，校验后事务替换 system，local 仍按前述领域语义优先。content_version
不再仲裁文件来源；当前 system 的该字段统一为 1，结构 schema_version 继续保留。

配置下载成功后即时安装并刷新相关缓存。正在执行的任务持有独立配置副本，整个批量任务
保持同一快照；多实例通过安装锁和生效代次协调。失败保留上一完整可用版本，不要求重启。
设置里的“接收在线配置更新”控制公开及会员两渠道，关闭后恢复 r0，不修改用户 local。

开发源码目录不接受自动在线安装。正式构建写入可信基底身份及 r0.zip；会员完整包独立
授权，明文只进入有效 system，密文归档于 updates/member。

手机使用 APK 预置或 PC 同步的 system/local，本轮不自行检查线上配置；PC 同步保留手机
updates 和原始基底身份，不把 PC 快照转换成 local，也不将其记录为官方 r0。

全部文件读取策略、目录及人工恢复说明见 [更新架构](05-config-update-architecture.md)。

## 关联文档

- 实体模型与指纹：[31-models/01-equipment-models.md](../31-models/01-equipment-models.md)
- 调律规则的启用与顺序：[10-game/10-tuning-rules/](../../10-game/10-tuning-rules/README.md)
- 用户视角的脚本管理：[60-userguide/06-workflows.md](../../60-userguide/06-workflows.md)

## 八、运行与界面状态分文件

`config/session/session.json` 由 `SessionStore` 管理执行选择和设置等运行节点。
`config/session/interface.json` 由 `InterfaceStore` 管理以下五个业务根节点：

| 节点 | 内容 |
|------|------|
| `ui_state` | 窗口尺寸、分栏、页签、列宽，以及批量活动组和运行草稿 |
| `profile` | 总览分组、列定义、顺序和活动组 |
| `alert_history` | Profile 提醒去重历史，原为 `profile.alert_history` |
| `alert_info` | 告警面板消息列表 |
| `server_config` | 更新提示、公告、在线配置与遥测状态 |

新文件元数据 `version: 1` 表示首版格式。初始化入口 `initialize_state_stores()`
必须在 QApplication 和页面构造前执行；读取与一次性迁移同步完成后才允许显示窗口。

`InterfaceStore` 复用 `SessionStore` 的节点读写原语，并使用独立路径、文件锁、格式版本
和只读实例临时字段清单。UI 页面继续统一通过 `load_ui_page_state` / `update_ui_page_state`
读取和浅合并自己的子节点；业务消费者直接使用新存储对应节点，不维护平行缓存。

仅新文件不存在时，迁移会在 interface → session 的固定锁序内读取旧格式。先原子写入
新文件和 `migration: {source: session, version: 1}` 标记，再删除旧文件四个节点，最后
移除标记。新文件已存在但标记尚在时只重试清理；不存在标记时不回读旧数据。
旧文件损坏不会被空数据覆盖。更完整的边界见
[任务历史与界面状态需求](../../20-requirements/60-editor/02-task-history.md#5-界面与辅助状态独立存储)。

手机同步包含新文件，配置应用后重置并加载两个存储。Profile key 重命名维护新文件，
失败恢复日志也覆盖新路径。用户元数据内按用户保存的界面状态仍属于 `users/*.json`。
