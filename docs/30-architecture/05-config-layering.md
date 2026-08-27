# 配置分层：system / remote / local / session

律匠的配置分四层，读写咽喉是 `src/lvjiang/core/config/resolver.py`。

| 层 | 目录 | 职责 | 进 git |
|----|------|------|--------|
| system | `config/system/` | 出厂默认，开发者提供的初始内容 | ✅ |
| remote | `config/remote/` | 在线下发层：只对实体文件生效，且只在版本更新时顶替 system（见第七节） | ❌ |
| local | `config/local/` | 用户覆盖层：影子文件 + 键级 diff + 墓碑 | ❌ |
| session | `config/session/` | 纯运行态，不经 resolver（见 `core.config.session`） | ❌ |

**读**：`local > remote（版本更新才生效）> system`，两种模式一致。
**写**：按模式路由——开发模式（`.git` 存在或 `LVJIANG_DEV_MODE=1`）写 system 全量；
用户模式写 local diff。**任何模式都不写 remote**——那层是下发下来的，
本地改动一律落 local。

---

## 一、两类文件

### 实体文件（一物一文件）

整文件影子 + 墓碑：local 存在同名文件即整个顶掉 system；删除靠
`local/<相对路径>.deleted` 空标记文件。

| 位置 | 内容 |
|------|------|
| `scenes/*.yaml` | 场景定义 |
| `workflows/**/*.wf` | 工作流脚本 |
| `layouts/{布局}/{场景}.json` | 布局坐标 |
| `yysls/tuning_rules/*.yaml` | 调律规则 |
| `yysls/base_groups/*.yaml` | 基础规则组（材料/扫描处置） |
| `yysls/graduation/*.json` | 毕业率方案 |
| `references/**/*.png` | 参考图 |

### 聚合键值文件（一文件多条目）

键级 diff 深合并：dict 递归、列表与标量整键替换。

| 文件 | 顶层键 | 主要消费方 |
|------|--------|-----------|
| `app.yaml` | `input_simulation` / `delay_params` / `envs` | `load_app_config()` |
| `scenes.yaml` | `layout_scenes` / `group_names` | `core/scene_registry.py`、`scene_definition.py` |
| `layouts.yaml` | `layouts` | `core/layout_manager.py`、`screen_calib.py` |
| `ocr_rules.yaml` | `replacements` / `patterns` | `core/ocr_cleaner.py` |
| `yysls/game_config.yaml` | `base_attrs` / `affix_caps` / `schools` / `weapon_types` 等 9 项 | `apps/yysls/config/manager.py` |
| `yysls/tune_config.yaml` | `base_rules` / `tuning_rules` / `quality_thresholds` / `switches` | `core/tuning_rules/manager.py` |

### 例外：telemetry/ 不是覆盖层镜像

`config/local/telemetry/`（install_id + 调律事件本地缓冲，见
`core/telemetry/paths.py`）不是 local 对 system 的覆盖，是纯本地运行态
——system 层没有对应内容。选它而不是 `config/session/`，是因为用户会
把 `config/session/` 打包发给作者排查问题（见
docs/60-userguide/08-feedback-and-issues.md），标识落在那里会让匿名性
失效；而这个目录又不适合套用 local 的影子/diff 语义（没有出厂内容可
覆盖）。resolver 的 `enumerate_entities`/`load_merged` 只按已知
`rel_dir` 枚举，不会遍历到这个目录，代码层面无冲突，纯粹是文档提醒：
新增同类"纯本地状态"目录时不要往这层套。

### 例外：参考图空间

`references/{空间}.yaml` **不走 resolver 的聚合接口**，
`core/reference_db.py` 自带一套条目级 diff（`references` + `deleted` 列表，
`meta_schema` 整列表替换）。新增同类配置前先确认是否该并入 resolver。

空间列表没有名册文件，由**目录扫描**得出：
`config/system/references/*.yaml` ∪ `config/local/references/*.yaml`，
文件名即空间名。落一个 yaml 就是新增一个空间，作者新增的出厂空间无需用户
删除本地文件即可见。两层同名 = local 是 system 的覆盖层，不是第二个空间。

system 层扫出的空间即**出厂空间**（`is_system_space()`）：用户模式下可以改其中
内容，但不能删除该空间——图库管理器的空间下拉将其置灰，「删除空间」按钮禁用
并把拒绝原因写进 tooltip。用户要独立的一套图，应当新建自己的空间。

`delete_space()` 的三条闸门（`can_delete_space()` 返回拒绝原因，空串即可删）：
空间必须存在、用户模式不得删出厂空间、至少保留一个空间。用户模式只清 local 层；
开发模式两层一起清，避免 system 层删掉后残留 local 覆盖层变成孤儿空间。
删的是激活空间时自动改激活、写 session 并重载。

---

## 二、列表的两种语义

同样是列表，合并方式必须区分，否则不是丢用户改动就是冻住出厂更新。

### 枚举设定 → 整键替换（默认）

`quality_thresholds.武器: [gold]`、`delay_params.*.range: [1.0, 1.2]`、
`base_attrs.*._first_affixes` 之类，用户就是要**覆盖**出厂值，整键替换正确。

### 注册表 → 存增量（`REGISTRY_LIST_PATHS` 声明）

可增长的条目登记表。local 若存下完整列表，出厂后续新增的条目就永远进不了
合并视图，用户除非删掉自己的 local 否则再也看不到更新。

`resolver.py` 的 `REGISTRY_LIST_PATHS` 常量表**只声明 core 自己拥有的路径**
——core.config 不认识任何插件领域词汇。插件私有配置文件的路径由插件自己
经 `register_registry_list_paths()` 注册（「import 即注册」，同
`builtin_modules`/`telemetry_modules` 的 `AppHooks.config_policy_modules`
约定）：

| 文件 | 路径 | 声明方 |
|------|------|--------|
| `scenes.yaml` | `layout_scenes.*` | core（`resolver.py` 常量表） |
| `yysls/tune_config.yaml` | `base_rules` | 插件（`apps/yysls/config/merge_policy.py`） |

local 形如：

```yaml
base_rules:
  __added__: [我的规则组]
  __removed__: [aggressive]
  __order__: [...]            # 仅在用户调过顺序时才写
```

读取时 `system 基底 − __removed__ + __added__`，再按 `__order__` 排序；
`__order__` 未提到的条目（出厂新增的）排末尾并保持 system 相对顺序。

存量 local 里的普通列表仍按整键替换处理——已无法区分「用户主动删了某条」
和「那条当时还不存在」，硬转会误伤；用户下次保存时自动转成增量形式。

---

## 三、删除：默认禁止

**system 目录里的东西，local 用户一律不能删除**——除非把自己切成开发模式
（`LVJIANG_DEV_MODE=1` 或仓库带 `.git`），那时他就是 system 身份。
用户该做的是改值、复制、另存为、新建；想停用某项走**激活机制**，
而不是删掉定义本身：

| 想停用 | 正确做法 |
|--------|----------|
| 某条调律规则 | `tune_config.yaml` 的 `tuning_rules: {key: false}` |
| 某个出厂布局 | 不选它即可（布局按需切换） |
| 某张出厂参考图 | 新建图库空间，放自己的图 |
| 某个脚本不在日常页显示 | 脚本配置里取消勾选（存 session，见下） |

技术上：`compute_diff` 只会为**system 里存在的键**产生 `__deleted__`。
用户自建的条目不在 system 基底里，删除它压根不产生删除标记——因此
「默认禁止 `__deleted__`」恰好只挡住出厂内容，用户自建内容仍可自由删除。

**`DELETABLE_PATHS` 目前是空表**——梳理下来出厂内容没有一样是该让用户删的。
保留这个扩展点是为了将来真出现例外时有地方声明，不是给现在留口子。
未声明的删除会被拦下并记 warning。

实体文件同样受保护：用户模式下对 system 层实体调 `delete_entity` 会抛
`SystemContentProtected`，不再落墓碑。**已存在的旧墓碑仍然生效**，
避免升级后突然冒出用户当初隐藏掉的脚本。

参考图库（`reference_db.py`，自带一套条目级 diff）同样拒绝删除出厂条目：
想要一套自己的图请**新建图库空间**，而不是把出厂图去掉。

### 列表型出厂内容

列表走整键替换，绕开了 `__deleted__` 那条保护——用户存一份少了几项的列表
就把出厂条目抹掉了。`PROTECTED_LIST_PATHS` 按**条目身份字段**比对，
出厂条目缺失即补回（插回出厂列表中的原下标），用户的新增、改值、重排
一概保留。

同 `REGISTRY_LIST_PATHS`，`resolver.py` 里这张表对 core 保持**空**——core
没有自己的受保护列表。以下三条都是插件私有配置，经
`register_protected_list_paths()` 由 `apps/yysls/config/merge_policy.py`
注册：

| 文件 | 路径 | 身份字段 | 声明方 |
|------|------|----------|--------|
| `yysls/game_config.yaml` | `weapon_types` | `name` | 插件 |
| `yysls/game_config.yaml` | `level_configs` | `level` | 插件 |
| `yysls/game_config.yaml` | `season_configs` | `season_number` | 插件 |

新赛季、新装备等增量内容用户可以自己加条目，不需要删任何出厂设定。

开发模式全量写 system、可直删实体，不受以上约束——编排出厂配置是开发者的职责。

### UI 侧：不给出厂内容留删除入口

后端拦下删除后，用户点了按钮却静默失败是糟糕的体验。设置面板统一经
`apps/yysls/ui/game_settings/factory_guard.py` 判断选中条目是否为出厂内容，
是则把删除按钮置灰并在 tooltip 说明替代方案（停用 / 只能改值）。
用户自建的条目不受影响，照常可删。

**已知缺口**：删除保护目前只覆盖 dict 键。列表型出厂内容
（`weapon_types`、`level_configs`、`season_configs`）走整键替换，
后端拦不住，目前只靠上述 UI 置灰把关。要在后端补齐，需要给这些列表定义
条目身份字段（如 `weapon_types` 用 `name`）并纳入注册表列表语义。

---

## 四、用户偏好不进配置层

**顺序、启停、显示名这类用户偏好一律存 session，不写回出厂配置。**
写回去会把出厂后续新增的条目冻住——`workflows.yaml` 就是栽在这上面，
已于 0.5.4 作废。

日常脚本的三层职责：

| 层 | 由谁决定 | 存哪 |
|----|----------|------|
| 脚本全集 | 目录约定（`workflows/policy.py` 的 `WorkflowDiscoveryPolicy`） | 不可配置 |
| 默认是否展示 | 作者声明：`.wf` 的 `#% hidden: true` / 内置类 `HIDDEN` | 随脚本走 |
| 顺序 / 启停 / 显示名 / 性质 | 用户 | `session.json` 的 `daily.scripts` |

`daily.scripts.visible` 是**覆盖**而非全集，只记与作者声明不同的项，
因此出厂新增的脚本自动出现，用户不用做任何事。

> `ui_state` 节点严格只放与业务无关的窗口控件位置关系，脚本偏好不放那里。

---

## 五、`save_merged` 的调用约定

**入参必须是完整文档**，不是「本次要改的那几个键」。

```python
doc = get_resolver().load_merged("app.yaml")   # 先取完整合并视图
doc["input_simulation"] = ...                  # 改需要改的键
get_resolver().save_merged("app.yaml", doc)    # 整个传回去
```

只传部分键会让其余顶层键在用户模式下被判成删除（现已被白名单拦下并记
warning），在开发模式下则会直接从出厂配置里消失——后者没有保护，
因为开发者本就有权重排 system。

---

## 六、插件声明自己的合并策略

`REGISTRY_LIST_PATHS`/`PROTECTED_LIST_PATHS` 曾经把 `yysls/*` 的路径直接
写死在 `resolver.py` 的常量表里——`core.config` 因此"认识" `base_rules`
是登记表、`weapon_types` 该按 `name` 判同一性这类纯游戏领域知识。这是
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
register_protected_list_paths("yysls/game_config.yaml", {
    "weapon_types": "name", "level_configs": "level",
    "season_configs": "season_number",
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

## 七、remote：在线配置下发

### 为什么需要 `content_version`

插一层 remote 之后，"remote 优先"是**错的**：用户升了 App，system 带来 v5
的场景坐标，而远端还停在给旧版本热修的 v3——无脑覆盖会把配置**静默回退**，
没有报错，只表现为"识别又坏了"。

所以每个参与下发的文件带一个顶层 `content_version`（整数，内容变一次加一），
remote 只在**严格新于** system 时才生效：

```
remote 生效 ⟺ remote.content_version > system.content_version
```

这个字段和 `graduation/*.json` 的 `schema_version`（**结构**版本，决定怎么
解析）是两回事，可以并存；也和 `references/*.yaml` 里那个从未被消费的
`version: 1` 无关——**加了不读的字段迟早会烂**（`config/i18n/*.yaml` 的
`version: 0.2.5` 至今停在旧版本没人管），这个字段是真的被 resolver 读来做
仲裁的。定义在 `core/config/versioning.py`。

**system 侧缺失即拒绝任何 remote 替换**（fail-safe）：将来漏给某个新文件加
字段，后果是"这个文件收不到在线更新"，而不是"这个文件被远端悄悄接管"。
前者能被人发现，后者不能。

### 谁维护版本号

开发模式经 `write_entity` 落盘时**自动 +1**，不靠人手改——这些文件全是 UI
编辑器（场景编辑器/布局编辑器/规则编辑器）写出来的，手写的版本号存一次就
没了。内容没变则保持原号，避免"打开编辑器又原样关掉"也推高版本。
`scripts/add_content_version.py` 负责给存量/新增文件补初始值（幂等，
`--check` 供 CI 用）。

### 哪些文件参与

同合并策略，core 只声明自己的，插件声明自己的：

| 目录 | 层数 | 允许远端新增 | 声明方 |
|------|------|--------------|--------|
| `scenes/*.yaml` | 1 | ❌ | core |
| `layouts/{布局}/{场景}.json` | 2 | ❌ | core |
| `yysls/tuning_rules/*.yaml` | 1 | ✅ | 插件 |

**`allow_remote_new` 的不对称是有意的**：新场景/新布局要在 `scenes.yaml`
注册表里登记才有意义，而注册表本身走发版（改它通常伴随代码改动），远端凭空
多一个场景文件是死的，只会让编辑器列表里冒出用不了的条目。调律规则相反——
规则管理器对"未在 `tune_config.tuning_rules` 里声明的规则"是追加到末尾而非
报错，所以远端下发一条全新规则能直接生效，这正是在线下发最有价值的场景。

顶层的 `scenes.yaml`/`layouts.yaml` **不参与**：那是注册表，改动伴随代码，
走发版。参与的只有它们名下的实体文件。

### manifest 与四道闸门

`core/config/remote.py`，形态与公告（`core/announcement.py`）、群二维码同款：
GitHub Pages 静态 JSON，不依赖 GitHub API、不需要服务端。

```
https://wanda1416.github.io/lvjiang/config/config.json
```

```json
{
  "schema_version": 1,
  "config_version": 12,
  "updated_at": "2026-08-27T00:00:00Z",
  "files": [
    {
      "rel_path": "layouts/桌面布局/game_login_page.json",
      "url": "https://wanda1416.github.io/lvjiang/config/remote/layouts/%E6%A1%8C%E9%9D%A2%E5%B8%83%E5%B1%80/game_login_page.json",
      "sha256": "…",
      "content_version": 3,
      "min_app_version": "0.7.0",
      "max_app_version_exclusive": "0.9.0"
    }
  ]
}
```

> 布局目录名是中文，URL 必须 percent-encode。manifest 里 `url` 存**已编码
> 的完整地址**、`rel_path` 单独存本地相对路径，不让客户端自己拼——不同平台
> 的编码行为不一致。

四道闸门：

1. **协议版本**：`schema_version` 对不上整份拒绝，不做旧协议兼容
2. **客户端版本区间**：`min_app_version` / `max_app_version_exclusive`。
   这是"远端配置和本地代码对不上"的唯一防线——布局里的 region key 被 `.wf`
   脚本按名字引用（`[场景].[区域]`），代码改了 key 而远端还在下发老配置，
   脚本会直接崩。改了就抬门槛。
3. **sha256**：逐文件校验，半截/被篡改的文件不落盘；先写 `.part` 再
   `replace()`，中途崩溃不会留下半截配置被 resolver 读到
4. **路径合法性**：拒绝 `..`、绝对路径、盘符、反斜杠，且只接受
   `versioning` 注册表里声明过的目录——manifest 是远端内容，不能让它决定
   往哪写盘

### 撤回

manifest 是**全量声明**：本地 `config/remote/` 里存在、但本轮 manifest 没列
（或版本区间不适用）的文件一律删除。推错了配置，从 manifest 移除该条目即可
撤回，不用发明 `revoked: true` 之类的标志位。

注意撤回**不能靠把 `content_version` 改小**：闸门要求远端严格大于 system，
调小只会让它不生效，文件还留在本地；只有从 manifest 移除才会真正删掉。

### 拒绝倒退

同步侧对"版本变小"一律不采信，两个层级各一道：

- **单文件**：清单声称的 `content_version` 比本地已有的旧就不下载
  （比较是 `>=` 而非 `==`）
- **整份清单**：`config_version` 比本地已同步过的旧，整轮忽略

拿到旧清单的成因不止一种——CDN 缓存没失效、发布目录被回滚、甚至是重放；
照做就是把配置静默降级回旧版，正是 `content_version` 这套机制要防的事故。

### 失败重试

有文件没拿到（网络/校验失败）时**不记 ETag**。记了的话下次带
`If-None-Match` 会收到 304、整轮直接跳过，这些文件就永远停在旧版或缺失，
直到作者恰好又发一版新清单为止。宁可下次多拉一次清单（几 KB）。

### 开发模式照常参与——而且必须看得见

**开发模式不豁免 remote 层**。曾经考虑过让开发模式跳过在线配置（"作者是
配置源头，不该被自己发布的东西盖住"），但那是错的：开发者本地跑出来的行为
一旦和每一个用户都不一样，用户报"识别坏了"时他**永远复现不出来**——而这
恰恰是在线下发最需要被排查的一类问题。

代价是必须让人**随时意识到**自己在看哪一份，否则远端顶替是完全静默的。
两处可见性：

- **日志**：`ConfigResolver._log_supersede` 在远端顶替出厂时记一条
  `[在线配置] 生效：scenes/xxx.yaml（出厂 v1 → 远端 v4）`。按
  (文件, 远端版本) 去重——`resolve_read` 是热路径，`enumerate_entities`
  还会逐个调，不去重会刷屏到没人看日志。
- **界面**：场景编辑器工具栏在「管理视图」右侧显示
  `场景 v4·远端　布局 v1·系统`，远端来源标橙色。没人会盯着日志编辑配置，
  界面上随时可见才真正解决问题。数据来自
  `ConfigResolver.describe_entity()`，文案由
  `ui/scene_editor/scene_tab.py:describe_scene_origin()` 组装。

**场景与布局分别标注**：场景编辑器实际在编辑两个文件（`scenes/{key}.yaml`
的定义、`layouts/{布局}/{key}.json` 的坐标），两者能被远端独立顶替，只标
一个就正好留下盲点。

### 开发模式写入：版本号以远端为下限

开发者在编辑器里看到的可能是远端 v3 的内容。这时保存若拿出厂 v2 做基线，
会写出一个同为 v3、内容却不同的文件——**v3 有两份不同内容**，一份在仓库
一份在线上，版本号不再唯一标识内容。故 `write_entity` 在远端正顶替时把
`max(出厂, 远端) + 1` 作为新版本号（`next_version_for_write` 的
`floor_version`），作者的改动确定性地胜出、立刻可见。

### 线程与生效时机

同步走后台线程（`core/remote_config_sync.py`），三段式拆分，逐条对应
`core/telemetry/reporter.py` 那三条硬约束——尤其**worker 线程绝对不能写
SessionStore**（`main_window` 给它注册了 UI 回调，写锁超时会从调用线程弹
QMessageBox，非主线程弹原生模态框是未定义行为）。所以 `build_sync_job()`
在主线程读状态，worker 只做网络与磁盘，`apply_outcome()` 回主线程写状态。

拉下来的配置**不热切换**，下次启动生效——而且这是**机制保证**的，不是靠
约定：下载落在暂存层 `config/remote.staging/`，由 `__main__` 在启动早期
（任何配置读取之前）经 `promote_pending()` 整目录提升为 `config/remote/`。

光写在注释里做不到：工作流每次启动都会重新 `load_layout()`
（`ui/main/run_control.py`），会立刻读到写进生效层的新布局，而场景注册表
只在进程启动时加载一次——半新半旧配在一起，出的问题极难查。

暂存层以生效层的当前内容为基线（`prepare_stage()` 先整份复制），否则提升
上去等于把本轮没变的文件全删了；"清单没列的一律删"那条差集也需要看到完整
现状才成立。提升用「先挪开现役、再改名暂存、最后删挪开那份」，中途崩溃最坏
留一个 `.old` 残留（下次启动清掉），不会出现生效层缺失的窗口。

### 用户开关

并入已有的 `settings.network`（与公告/更新同层），**不另立开关**——否则
"完全离线模式"这个总闸管不到在线配置，用户勾了离线却还在后台拉配置。
默认**开**，与统计的"默认关 + 首启询问"相反：统计是把数据传出去，在线配置
是把修复拿回来。设置页「网络与隐私」标签页可关闭。

关闭的语义是**回到出厂配置**，不只是停止下载：下次启动时
`promote_pending()` 会把暂存层与生效层一并清掉。只停下载是不够的——已下发
的配置会一直顶替出厂内容，用户遇到一份有问题的远端配置就没有退路了。
重新打开会重新同步，删掉没有损失。

> 状态展示要区分"已生效"和"已下载待生效"：配置落在暂存层时显示
> 「已下载在线配置 vN，重启后生效」，直接报"当前版本 vN"是错的——本次会话
> 跑的还是上一版。

`UserConfig` 解析 `settings.network` 时按 **dataclass 字段名**过滤未知键，
不另写白名单：手写白名单漏同步新字段会把用户存下的值静默丢掉、回退成默认值
（`remote_config` 就这么漏过一次，表现为"关掉了，重启又自己打开"）。

### 尚未覆盖

- **设备端（Android）**：`versionCode` 兼着"配置解压 stamp"
  （见 `android/app/build.gradle.kts` 注释），与整目录解压那套怎么共存要
  单独设计，当前只做桌面端。
- 其余配置（`app.yaml`、`ocr_rules.yaml`、`workflows/`、`references/`、
  `graduation/`）暂不参与，机制已就位，需要时按上表加一行注册即可。

---

## 关联文档

- 实体模型与指纹：[31-models/01-equipment-models.md](31-models/README.md)
- 调律规则的启用与顺序：[10-game/10-tuning-rules/](../10-game/10-tuning-rules/README.md)
- 用户视角的脚本管理：[60-userguide/06-workflows.md](../60-userguide/06-workflows.md)
