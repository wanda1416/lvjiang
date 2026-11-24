# 契约 A：插件装配

本文描述插件从被发现、声明配置、注册扩展点，到内容初始化与 UI/任务消费的完整装配顺序，
以及每个 hook 的声明方、调用方、时机与失败边界。框架通用机制与 yysls 插件专属事实分开表述；
框架层不出现任何插件专名。

## 一、装配总顺序

进程启动序列（`src/lvjiang/__main__.py` 的 `_bootstrap`）：

1. 实例锁、日志、状态存储初始化；`init_i18n()` 先于插件加载，避免插件 hooks 与模块级
   显示标签按默认语言固化。
2. `resolve_app_specs()` 把 `-reg` 参数与 `--all-plugins` 展开成按序加载的插件名册。
3. 对每个插件先 `load_app_i18n()`；随后 `load_config_policies()` 只装配各插件的配置策略
   声明（不装 UI/工作流）。
4. `prepare_startup_configuration()` 回滚未完成事务、安装在线配置（须发生在加载插件和
   场景注册表之前）。
5. 逐个 `load_app(spec)` 取得插件顶层导出的 `hooks: AppHooks`，再 `register_hooks(hooks)`
   注入各注册表。
6. 全部插件装配完成后才创建 QApplication 与主窗口；主窗口构造时从全局注册表读取扩展点
   （`src/lvjiang/ui/main/window.py`）。

即：**发现（第 2 步内）→ 配置声明（第 3 步）→ 注册（第 5 步）→ UI/任务消费（第 6 步及运行期）**。

## 二、插件发现（框架）

插件代码位于命名空间包 `lvjiang.apps`（`src/lvjiang/core/plugins/__init__.py`，
`src/lvjiang/apps/__init__.py`），宿主只按 `lvjiang.apps.<name>` 这一约定导入，
不认识任何具体插件。发现分三级：

- **内置插件**：`src/lvjiang/apps/<name>/` 子包，由 `pkgutil.iter_modules` 列举得到
  （frozen 环境靠 PyInstaller 的 pkgutil 运行时钩子列举 PYZ 归档），名册与安装目录无关。
- **外置默认目录**：`<PROJECT_ROOT>/plugins/<name>/`，以「子目录 + `__init__.py`」识别，
  不读取任何清单文件；`lvjiang/apps/__init__.py` 会把该目录追加进命名空间包 `__path__`。
- **外置任意目录**：`-reg <name>@<path>` 显式声明，目录名不必等于插件名，由
  `_mount_app` 构造包规格注入 `sys.modules`，失败时撤销挂载避免留下半个包。

插件名必须是合法 ASCII 标识符；非法目录名只记 warning 跳过，不影响其它插件与启动。
`resolve_app_specs` 中显式 `-reg` 保持出现顺序、同名去重，`--all-plugins` 追加扫描结果
（内置在前）。

## 三、配置声明与消费（框架）

### 声明方式

插件的 `AppHooks.config_policy_modules` 列出「import 即注册」的配置策略模块
（`src/lvjiang/core/plugins/hooks.py`）。这些模块在导入时调用 `core.config` 的注册函数
声明本插件的合并策略与版本化路径，import 之外不做任何事。

`core.config.resolver` 是 system/local 两层配置的唯一读写入口（`src/lvjiang/core/config/resolver.py`）。
core 本身不认识任何插件领域词汇；哪些路径是登记表（`register_registry_list_paths`，
local 只存 `__added__` / `__removed__` / `__order__` 增量）、哪些列表受保护
（`register_protected_list_paths`，系统条目不可删除）、哪些路径参与在线下发与
`content_version` 递增（`register_versioned_file` / `register_versioned_dir`），
全部由插件策略模块声明，不写进 core 常量表。

### 消费方式

- `load_merged` / `save_merged` 在合并与写回时按上述注册表决定合并语义、删除白名单与
  版本递增；`save_merged` 的入参必须是完整文档，diff 语义把缺失键判成用户删除。
- 配置策略有一个独立于完整装配的轻量入口 `load_config_policies(app_specs)`
  （`src/lvjiang/core/plugins/__init__.py`）：只加载各插件的配置声明，不注册 Tab、
  工作流、识别器与内置函数，供离线工具（如内容版本检查脚本）与配置层单测使用。
- 离线快照同步（`src/lvjiang/core/ondevice/offline.py`、`src/lvjiang/core/offline_bundle.py`）
  通过 `get_registered_hooks()` 读取各插件声明的 `offline_configuration_patterns` 等字段，
  不各自重新加载插件。

### 时机

配置声明在 `register_hooks` 之前由 `load_config_policies` 装配（见第一节第 3 步），
确保后续的在线配置安装、场景注册表初始化与插件注册都面对完整的合并策略。

## 四、注册（框架）：`register_hooks`

`AppHooks`（`src/lvjiang/core/plugins/hooks.py`）是插件向通用引擎声明扩展点的唯一形态。
`register_hooks`（`src/lvjiang/core/plugins/__init__.py`）按固定顺序处理：

1. **依赖检查**：`requires_app_ids` 中有未注册 ID 时整个插件拒绝注册并抛错（这是少数
   会中断当前插件装配的路径）。
2. **配置策略**：`register_config_policies` 导入 `config_policy_modules`。
3. **i18n**：`i18n_directory` 声明插件自带词表目录；未声明时宿主回退到内置插件的
   `config/i18n/apps/<app>` 约定（`src/lvjiang/i18n/__init__.py` 的 `load_app_i18n`）。
4. **启动回调**：逐个执行 `startup_callbacks`（一次性数据迁移等无 UI 初始化）。
5. **注册表注入**：把 `app_ids` / `hooks` / `window_title` / 左右 Tab 构建器 / 菜单构建器 /
   主题样式构建器 / 识别器类 / 工作流实现 / 内置函数模块 / Profile 周期模块 / 统计 schema
   模块 / 同步文档 kind 模块等登记进全局注册表，并同步注入各自领域注册表
   （`core.recognizers`、`core.workflows.implementations`、`core.workflows.builtins`）。

全局注册表是进程内的一个 dict（`get_registry()`）；离线同步、历史回传、配置重载等消费方
通过 `get_registered_hooks()` / `get_registered_app_ids()` 按注册顺序只读访问，不再各自
`load_app` 取值。

### hook 失败边界一览

| hook / 阶段 | 失败时的行为 | 依据 |
|---|---|---|
| 目录扫描遇到非法名 | warning 跳过该目录 | `core/plugins/__init__.py` `_scan_root` |
| `load_app` 导入失败 / 缺 `hooks` | 抛错终止**该插件**的装配；主程序以异常结束本次插件加载，其余已注册插件保持已注册状态（加载循环在 `__main__.py` 内未捕获单个插件的导入异常） | `core/plugins/__init__.py` `load_app` |
| `requires_app_ids` 缺失 | 该插件拒绝注册，抛 RuntimeError | `register_hooks` 依赖检查 |
| `config_policy_modules` 单个模块导入失败 | 记日志跳过，其余声明继续 | `register_config_policies` |
| `startup_callbacks` 单个回调失败 | 记日志继续其余回调 | `register_hooks` |
| 识别器 / 工作流注册失败 | 记日志，不中断注册表登记 | `register_hooks` 对应 try 块 |
| 内置函数 / Profile 周期 / 统计 schema / 同步文档模块导入失败 | 记日志跳过（Profile 周期模块例外：导入失败会 `raise`，因其扩展全局共享的 Profile 周期语义，半注册不可接受） | `register_hooks` 对应循环 |
| UI 阶段单个 Tab / 菜单构建器失败 | 记日志跳过该 Tab / 菜单，其余条目与窗口启动不受影响 | `ui/main/window.py` `_add_plugin_tabs`、`ui/main/menu_ops.py` |

设计意图：**发现、声明、注入阶段的单点失败一律在本层隔离并留可行动日志**；只有
「插件依赖未满足」与「Profile 周期半注册」这两类无法安全降级的情况选择快速失败。

## 五、UI 与任务消费（框架）

- **主窗口**（`src/lvjiang/ui/main/window.py`）：构造时从注册表读 `window_title`；
  左右 Tab 按注册顺序追加（`_add_plugin_tabs`）；插件页面通过宿主公开 API
  （`active_user_name` / `request_stop` / `run_workflow_implementation` 等）与信号交互，
  不触碰主窗口私有属性。
- **菜单**（`src/lvjiang/ui/main/menu_ops.py`）：逐个执行 `menu_builders`，插件菜单插在
  「帮助」之前。
- **主题**（`src/lvjiang/ui/widgets/theme.py`）：拼接 `theme_stylesheet_builders` 返回的 QSS。
- **遥测同意框**（`src/lvjiang/ui/notices/telemetry_consent_dialog.py`、
  `src/lvjiang/ui/settings/dialog.py`）：读取 `telemetry_disclosures` 统一呈现数据用途授权。
- **任务执行**：插件注册的识别器进入 `core.recognizers` 注册表、工作流实现进入
  `core.workflows.implementations`、内置函数经 `@builtin_func` 进入 DSL 内置函数表；
  脚本/任务运行期与通用引擎消费这些注册表，不感知插件来源。
- **外部 Agent 运行接口**：主窗口暴露 `PluginRuntimePort` 适配器
  （`src/lvjiang/core/plugins/runtime.py` 协议、`src/lvjiang/ui/main/plugin_runtime.py` 实现），
  向插件页面提供目标选择、任务状态、运行控制与工作流启动的受限通道。

## 六、yysls 插件专属事实

以下内容属于插件层，不约束其他插件。

- **声明**：`src/lvjiang/apps/yysls/__init__.py` 顶层导出 `hooks = AppHooks(id="yysls", ...)`：
  注入左侧「调律」Tab、右侧「备战方案」「调律管理」Tab、菜单与装备品阶主题样式；
  注册工作流实现 `auto_tuning`、六个内置函数模块、Profile 周期模块、统计 schema 模块与
  一条调律过程数据用途披露；声明配置重载回调（外部快照应用后失效各业务缓存）、
  离线快照路径模式与调律历史回传通道。
- **配置声明**：`src/lvjiang/apps/yysls/config/merge_policy.py` 注册
  `yysls/game_config/equipment.yaml` 的 `weapon_types`、seasons 的等级/赛季列表为受保护列表；
  把 `yysls/game_config/*.yaml`、`yysls/tune_config.yaml`、`yysls/gear_sets/`、
  `yysls/tuning_rules/`、`yysls/base_groups/`、`yysls/graduation/` 登记为可在线下发的
  版本化文件/目录。毕业率模型的身份写在文件自身（schema_version + 流派/方案/等级/版本），
  发现层按 schema 过滤，不另设方案名清单。
- **Agent 文档布局**：`src/lvjiang/apps/yysls/core/agent/documents.py` 声明
  `DOCUMENT_DIRECTORIES = ("10-game", "30-architecture", "guide", "70-agent")`（物理目录 `guide` 经 `categories={"guide": "60-userguide"}` 维持逻辑分类键 `60-userguide`）、
  稳定别名表 `AGENT_DOCUMENTS`（如 `entry`、`tool-schema`）与 `AGENT_DOCUMENT_LAYOUT`。
  该声明同时是发行文档收录范围的唯一来源（见 docs/00-meta/00-doc-standard.md 第七节）。
- **Agent 工具装配**：`src/lvjiang/apps/yysls/core/agent/catalog.py` 的 `tool_catalog`
  把 `AgentService`（`src/lvjiang/apps/yysls/core/agent/service.py`，经
  `ConfigResolver.resolve_read` 读配置、`DocumentDirectoryStore` 管生成结果）的只读/操作
  方法与文档工具 `list_docs` / `read_doc` / `search_docs` 组装成一个目录。
  `src/lvjiang/apps/yysls/ui/tuning/agent_page.py` 在用户启动 MCP 服务时用该目录构建
  本地 MCP 服务器，文档源由 `core.ai.agent_docs.AgentDocuments` 按 `AGENT_DOCUMENT_LAYOUT`
  从 `PROJECT_ROOT/docs` 读取（打包态随包文档、源码检出态直读仓库），并提供
  `lvjiang://docs/{doc_id}` 资源与工具 schema 回填。
- **运行前校验**：`src/lvjiang/apps/yysls/core/agent/launch.py` 的 `prepare_tuning` 负责把
  UI 与 Agent 共用的调律运行配置校验、冻结为 `TuningRunContext`，UI 与 Agent 消费同一契约。
