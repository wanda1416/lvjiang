# 插件系统架构与开发指南

## 架构概述

律匠采用「通用引擎 + 插件」的单包架构：

- `src/`：通用视觉 RPA 引擎（截图、OCR、输入、场景、DSL 引擎、通用 GUI）
- `src/lvjiang/apps/<name>/`：内置插件，随包分发
- `plugins/<name>/`：外置插件默认目录，独立检出（公开仓库不含）
- `config/`、`data/`：全局共享，所有插件共用

启动方式：
```bash
python -m lvjiang                      # 纯通用（场景编辑 + 识别测试）
python -m lvjiang -reg yysls           # 通用 + 内置燕云插件
python -m lvjiang -reg foo -reg bar    # 多插件同时加载
python -m lvjiang -reg foo@../foo      # NAME@PATH：加载任意目录的外置插件
python -m lvjiang --all-plugins        # 内置插件 + plugins/ 默认目录全部插件
```

## 核心接口：AppHooks

插件通过 `AppHooks` 数据类向引擎声明扩展点：

```python
# src/lvjiang/apps/base.py（节选；完整字段见下面的扩展点全表）
@dataclass
class AppHooks:
    id: str = ""                              # 稳定机器标识：小写 ASCII、不可翻译（如 "yysls"）
    name: str = ""                            # 显示名，可翻译；不参与任何匹配
    requires_app_ids: tuple[str, ...] = ()    # 必须先注册的插件 ID，缺失则整个插件被拒绝
    ...
```

`id` 与 `name` 是两件东西：`id` 用于菜单登记、依赖声明、`AppEvent.app_id`、插件页归属
与日志定位；`name` 只用于展示。把翻译后的显示名当 ID 用，会在切换语言或改名后让引用
全部失效。

主应用创建唯一的通用 MainWindow；插件**不能替换主窗口类**，只能通过
builder 注入左/右 Tab 与菜单。多插件的 Tab/菜单按 `-reg` 顺序叠加。

builder 约定：

- Tab builder 签名 `builder(host: MainWindow) -> QWidget`，菜单 builder 签名
  `fn(host: MainWindow, menubar: QMenuBar) -> None`（插入位置在「帮助」菜单之前）
- builder 必须是顶层轻函数，函数体内延迟 import 实际类（保持「插件 import
  不触发 PyQt6」约定）
- 单个 builder 抛异常只记日志不中断其他插件
- 增强插件用 `requires_app_ids=("目标插件 ID",)` 声明加载顺序。宿主在任何
  启动回调或扩展注册前检查依赖，缺失时拒绝加载。
- 插件主菜单用 `ui.plugin_menus.add_plugin_menu(menubar, app_id, label)`
  按稳定 ID 登记；增强插件用 `require_plugin_menu(menubar, app_id)` 向已存在的
  菜单追加动作，不依据翻译后的菜单文字查找。

宿主 API（插件页面通过 host 使用，不摸私有属性）：

- `host.active_user_name() -> str`：当前活跃用户名
- `host.plugin_runtime: PluginRuntimePort`：查询目标、预检启动、切换目标、启动工作流、
  查询运行记录与人工提示、暂停/恢复/停止任务，以及读取热键快照和插件页。
  契约位于 `core/plugin_runtime.py`，桌面适配位于 `ui/main/plugin_runtime.py`。
  插件通过适配器访问目标登记、运行管理与界面恢复，不直接读取这些宿主私有入口。
  `run/current_run` 仍返回原运行对象，`engine/worker` 是供进度及结果读取的原对象引用；
  `pages()` 返回全部左栏控件，由插件筛选自己的页面。目前统一的是访问入口，并未隔离对象。
  目标切换仍由宿主保存旧目标草稿并恢复新目标，不能改变默认用户。
  桌面接口在 UI 线程调用；MCP 请求通过 AgentTaskBridge 的队列信号转入 UI 线程。
- `host.is_running` / `host.request_stop()`：自动化运行状态与停止
- `host.append_log(text)`：写运行日志
- `host.run_workflow_implementation(impl_name, flow_name, configure, *, execution_username=None)`：
  启动已注册工作流（通用脚手架 + `configure(wf_instance, engine)` 回调写入专属参数）；
  专属 Tab 可传启动时解析出的执行用户，不传则跟随当前用户
- 信号 `automation_state_changed(str)`（"running"/"not_ready"/"ready"）、
  `user_changed(str)`
- F9 分发：当前左侧 Tab 若实现 `f9_run()` 则交由其处理，否则走通用工作流

### 扩展点全表

字段按用途分四组。**每行都是真实契约**：写错通常不会报错，而是静默不生效，新插件按表
逐项核对即可。控制台（`module: 行号`）为当前实现位置。

**身份与加载顺序**

| 字段 | 默认 | 用途 | 消费方 / 写错的后果 |
|---|---|---|---|
| `id` | `""` | 稳定机器标识，小写 ASCII、不可翻译 | `apps/__init__.py:register_hooks` 记入 `registry["app_ids"]`；被 `requires_app_ids`、`AppEvent.app_id`、菜单登记引用。留空则插件不进名册，依赖它的插件加载失败 |
| `name` | `""` | 显示名（可翻译） | 日志、插件页标签、窗口标题回退。**不要用它做任何匹配** |
| `requires_app_ids` | `()` | 必须先注册的插件 ID | `register_hooks` 首步校验；缺失时抛 `RuntimeError`，整个插件拒绝注册（不是降级加载） |
| `startup_callbacks` | `[]` | 注册后、其余扩展装配前的无 UI 初始化（一次性数据迁移等） | `register_hooks` 逐个执行；**单个回调异常只记日志**，不阻断本插件其余扩展，也不影响其他插件 |

**UI 注入**（只能注入，不能替换主窗口类）

| 字段 | 默认 | 用途 | 消费方 / 约定 |
|---|---|---|---|
| `window_title` | `None` | 覆盖主窗口标题 | `ui/main/window.py`；多插件时后注册者覆盖 |
| `left_tab_builders` | `[]` | `[(label, builder)]`，左栏 Tab | `builder(host) -> QWidget`，函数体内延迟 import PyQt6 |
| `right_tab_builders` | `[]` | `[(label, builder)]`，右栏 Tab | 同上 |
| `menu_builders` | `[]` | `[fn(host, menubar)]`，插入到「帮助」之前 | 弹窗以 `host` 作 parent；按 `ui.plugin_menus` 的 ID 登记定位 |
| `theme_stylesheet_builders` | `[]` | 追加 QSS 片段 | `ui/theme.py`；只加样式，不改主题判定逻辑 |

**能力注册**（import 即注册的模块，或直接给类/路径）

| 字段 | 默认 | 用途 | 消费方 / 写错的后果 |
|---|---|---|---|
| `recognizer_classes` | `[]` | 识别器协议实现类 | `core.recognizers.register_recognizer`；单个失败只记日志 |
| `workflow_implementations` | `{}` | `{名称: "dotted.path.ClassName"}` | `workflows.implementations.register_workflow`；专属 Tab 通过 `host.run_workflow_implementation` 启动 |
| `builtin_modules` | `[]` | `@builtin_func` 模块路径列表 | 导入触发注册；**与内置函数或别的插件重名会在加载期报错**，见 [32-grammar/07-subworkflows.md](../32-grammar/07-subworkflows.md) |
| `profile_period_modules` | `[]` | 注册 Profile 自定义周期（赛季/半赛季等） | `core.profile.register_profile_period`；**这里的导入失败会向上抛**（周期语义错会让账本算错，不能静默） |
| `telemetry_modules` | `[]` | 统计事件 schema 模块 | `core.telemetry`；导入失败只记日志 |
| `telemetry_disclosures` | `[]` | 数据用途声明（`TelemetryDisclosure`） | 通用同意框统一呈现；新增采集项必须在此声明 |

**配置、同步与离线包**（详见下节）

| 字段 | 默认 | 用途 | 消费方 |
|---|---|---|---|
| `config_policy_modules` | `[]` | 声明哪些列表是登记表、哪些系统条目不可删 | `core.config.resolver`（`register_registry_list_paths` / `register_protected_list_paths`） |
| `sync_document_modules` | `[]` | 声明插件用户文档的 kind 与文件后缀 | `core.sync.registry.register_user_document_kind` |
| `configuration_reload_callbacks` | `[]` | 外部完整配置快照应用后清理插件自己的只读缓存 | `core/ondevice/offline.py`（装包后调用） |
| `offline_configuration_patterns` | `()` | PC→手机同步的配置白名单，路径相对 `system`/`local` | `core/offline_bundle.py` |
| `offline_session_files` | `None` | 额外会话文件，相对 `session` | 同上 |
| `offline_database_files` | `()` | `session` 内的 SQLite，公共同步器做 backup 与完整性检查 | 同上 |
| `history_channels` | `None` | 手机历史独立回传通道 | `core/history_transfer.py`（聚合各插件的声明） |

### 注册顺序与失败边界

`register_hooks` 的处理顺序固定，插件的初始化逻辑依赖它：

1. **依赖校验** —— 缺 `requires_app_ids` 直接抛错，插件不注册（也不注册它的配置策略）。
2. **配置策略** —— `import config_policy_modules`（`register_config_policies`，离线工具走同一入口）。
3. **启动回调** —— 执行 `startup_callbacks`，异常只记日志。
4. **UI 与能力** —— 登记 `id`、`window_title`、Tab/菜单/主题 builders，注册识别器、工作流、
   内置函数模块、Profile 周期、统计 schema、同步文档声明。

失败边界：识别器 / 工作流 / 内置函数 / 统计 / 同步声明这几处的 import 或注册失败都只写
`exception` 日志并继续；`profile_period_modules` 是唯一会上抛的分支。`load_app` 阶段
（插件模块本身 import 失败、未导出 `hooks`）则整体失败——这不是"可降级"的错误。

`load_config_policies(app_ids)` 只装配第 2 步，不注入 UI 与工作流：离线工具
（`scripts/add_content_version.py`、配置层单测）需要完整登记表但不需要副作用。
```

## 插件目录结构

内置与外置插件的包结构相同，区别只在检出位置：

```
src/lvjiang/apps/<name>/          # 内置插件（随包分发）
plugins/<name>/                   # 外置插件（独立检出，公开仓库不含）
├── __init__.py        # 导出 hooks: AppHooks 实例
├── constants.py       # 插件专属常量
├── core/              # 插件专属能力（识别器、数据库等）
├── workflows/
│   ├── implementations/  # 复杂工作流实现
│   └── builtins/         # 插件专属内置函数
└── ui/                # 插件专属 UI 组件（Tab 页面、对话框、菜单 builder）
```

插件只保留领域规则和领域状态。已经稳定的通用机制由插件依赖公共层：

- `core.config.document_store.DocumentDirectoryStore`：线程内互斥、版本标记和
  跨文档事务恢复的 JSON 文档目录；插件通过初始化钩子实现自己的旧数据迁移。
- `workflows.ports`：工作流实现使用的场景自动化与子流程引擎 Protocol；插件端
  Protocol 组合这些能力并补充领域常量和状态。
- `ui.layout_helpers`、`ui.presentation`：配置布局、主题判断和轻量展示组件。

`core.app_events.AppEvent` 是无 GUI 依赖的公共事件信封；领域主题由插件自己的
`core/events.py` 声明，Qt 路由与去抖仍留在插件 UI。

用户资料的 `User.extra_fields` 保留公共模型未解释的顶层扩展字段。燕云的
`config/analysis_settings.py` 负责按方案 ID 读写毕业率设置；原用户文件位置和
`graduation_analysis` 字段不变，不迁移、不维护另一份数据。公共资料编辑必须保留这些字段。

手机同步的公共数据库只有 Profile 与日常执行历史。插件通过
`AppHooks.offline_database_files` 声明额外数据库，路径相对 session；燕云声明
`tuning_history.db`。公共同步器负责 SQLite backup，安装时按实际包内数据库检查完整性，
不根据调律等领域名称决定需要哪些数据库。

公共模块不直接导入具体业务插件；组合根通过通用注册接口装配声明。
装备、调律、地图等业务步骤及失败语义仍由插件定义；
仅因代码位于插件的 `core` 目录，不构成迁入公共层的理由。

## 开发新插件

### 1. 创建插件目录

```bash
mkdir -p src/lvjiang/apps/mygame     # 内置插件
mkdir -p plugins/mygame              # 或：外置插件（默认目录）
touch src/lvjiang/apps/mygame/__init__.py
```

### 2. 加载插件

插件分三级，宿主主动发现前两级：

| 级别 | 位置 | 加载方式 | 公开发布包 |
|---|---|---|---|
| 内置 | `src/lvjiang/apps/<name>/` | 目录扫描 | 随包携带 |
| 外置默认 | `plugins/<name>/` | 目录扫描 | 打包时目录存在才携带 |
| 外置任意 | 任意目录 | `-reg <name>@<path>` 显式声明 | 不携带 |

插件以「子目录 + `__init__.py`」识别，不读取清单文件；目录名必须是合法 Python
标识符（因此用 `yysls_premium` 而非 `yysls-premium`），非法目录名只记 warning
跳过，不影响其它插件与主程序启动。

本地 `dev.sh` / `dev.bat` 与主程序一致：不传 `-reg` 就是纯通用模式，`--all`
交给宿主扫描内置根与 `plugins/` 默认目录，不在这里维护名册。

### 3. 实现 hooks

```python
# src/lvjiang/apps/mygame/__init__.py
from ..base import AppHooks


def _build_auto_tab(host):
    from .ui.auto_tab import AutoTab   # 函数体内延迟 import，避免插件 import 触发 PyQt6
    return AutoTab(host)


def _build_menu(host, menubar):
    from .ui.menus import build_menu
    build_menu(host, menubar)


hooks = AppHooks(
    id="mygame",                              # 稳定标识：菜单登记、依赖声明、事件归属都用它
    name="我的游戏",                           # 显示名，可翻译
    window_title="律匠 - 我的游戏自动化工具",
    # 可选：左/右 Tab 与菜单（注入通用 MainWindow）
    left_tab_builders=[
        ("自动化", _build_auto_tab),
    ],
    menu_builders=[_build_menu],
    # 可选：识别器
    recognizer_classes=[MyGameRecognizer],
    # 可选：复杂工作流
    workflow_implementations={
        "auto_farm": "lvjiang.apps.mygame.workflows.implementations.auto_farm.AutoFarmWorkflow",
    },
    # 可选：内置函数模块（导入即触发 @builtin_func 注册）
    builtin_modules=[
        "lvjiang.apps.mygame.workflows.builtins.items",
    ],
)
```

### 4. 添加识别器（可选）

```python
# src/lvjiang/apps/mygame/core/recognizer.py
from lvjiang.core.recognizers._registry import Recognizer

class MyGameRecognizer:
    name = "mygame_recognizer"
    
    def recognize(self, image: np.ndarray, **kwargs) -> dict:
        # 实现识别逻辑
        return result
```

### 5. 添加内置函数（可选）

```python
# src/lvjiang/apps/mygame/workflows/builtins/items.py
from lvjiang.workflows.builtins._registry import builtin_func

@builtin_func("get_item_name")
def _get_item_name(_engine, item_id: str) -> str:
    """根据物品 ID 获取名称"""
    return ITEMS.get(item_id, "未知物品")
```

函数名在全局命名空间里唯一：与内置函数或其他插件重名会在加载阶段直接报错（不静默覆盖），
首参命名为 `_engine` / `_wf` 时分别注入引擎与工作流实例。

### 6. 添加复杂工作流（可选）

```python
# src/lvjiang/apps/mygame/workflows/implementations/auto_farm.py
from lvjiang.workflows.base import BaseWorkflow

class AutoFarmWorkflow(BaseWorkflow):
    """自动 farming 工作流"""
    
    def execute(self, **params):
        # 实现工作流逻辑
        pass
```

## 插件测试与门禁

插件代码的回归网分三层，新增插件时按这三层补齐：

| 层 | 位置 | 保护什么 |
|---|---|---|
| 发现与加载 | `tests/core/test_plugin_discovery.py` | 内置名册与实际源码目录一致（漏登/多登都会失败）、三级扫描、"子目录 + `__init__.py`"识别、非法目录名只跳过、`-reg NAME@PATH` 挂载、`--all-plugins` 去重追加 |
| 无 Qt 契约 | `tests/core/test_plugin_contracts.py` | 公共插件契约（`AppEvent`、`PluginRuntimePort`、`ProfileProcessor` 等）在屏蔽 PyQt6 的子进程里仍能 import；插件碰到的公共层不得偷偷依赖 Qt |
| 领域行为 | `tests/yysls/`、外置插件自己的 `tests/` | 装备解析、调律规则、采集流程等业务契约 |

跑法与全局约束：

- 统一 `QT_QPA_PLATFORM=offscreen`；不要指望人工点对话框，模态弹窗在测试里一律被换成
  立即抛错。
- 测试进程装了写门禁（`tests/config_write_guard.py`）：任何对仓库真实 `config/` 的写入都会
  失败。插件测试必须把配置、数据库、session 全指向 `tmp_path`，绝不写真实
  `config/session`、`config/local` 或用户数据库。
- 外置插件在主仓库根执行：
  `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest plugins/<name>/tests`。
- 改完插件目录或 `_BUILTIN_APP_IDS` 名册，至少跑 `tests/core/test_plugin_discovery.py`；
  改配置声明跑 `python scripts/add_content_version.py --check`。
- 打包侧另行校验插件清单与对应 `local` 资源是否随包（见 `plugins/README.md`）。

## 注册机制

配置策略由组合根显式装配：桌面在恢复在线配置前调用
`apps.load_config_policies(app_ids)`，手机在声明 app 组合和应用配置时调用同一入口，
离线字段工具显式选择 `builtin_app_ids()`。已有 hooks 对象可调用
`register_config_policies(hooks)`；这两个轻量入口都不执行启动回调、不注入 UI。
完整 `register_hooks` 在启动回调前装配策略；底层 resolver、修订索引和运行快照
只消费登记结果，不自行发现或加载插件。

### 完整插件注册

插件加载时，`register_hooks()` 会自动：

1. **识别器**：调用 `lvjiang.core.recognizers.register_recognizer()` 注册
2. **工作流**：调用 `lvjiang.workflows.implementations.register_workflow()` 注册
3. **内置函数**：导入 `builtin_modules` 中的模块，触发 `@builtin_func` 装饰器注册
4. **UI 扩展**：将 tab/menu builders 收集到注册表，由通用 MainWindow 构建时消费
   （多插件 extend 叠加，按 `-reg` 顺序）

## 插件参与离线包与同步的声明面

手机端的配置下发、数据库同步与历史回传都由公共层执行，**公共层不认识任何插件领域词汇**。
插件只声明"我这里有什么"，具体是哪些文件、哪些表由公共同步器决定怎么搬：

| 字段 | 路径基准 | 公共层怎么用 |
|---|---|---|
| `offline_configuration_patterns` | 相对 `system` / `local` | 打包 PC→手机离线包时按通配符收集配置文件；未声明就不下发 |
| `offline_session_files` | 相对 `session` | 追加会话级文件（函数形式，便于按当前状态决定）；下载安装时同样按白名单处理 |
| `offline_database_files` | `session` 内的文件名 | 公共同步器对 SQLite 统一做 backup、安装时按包内实际数据库检查完整性；**不按"调律"等领域名称猜需要哪些库** |
| `sync_document_modules` | Python 模块路径 | 导入即注册插件用户文档的 kind 与后缀（如出装 `.loadouts.json`），账号服务文档通道据此识别 |
| `history_channels` | 函数，返回 `HistoryChannel` 元组 | 手机历史独立回传（如调律历史）：这些库**不属于配置下发**，走自己的通道 |
| `configuration_reload_callbacks` | 回调列表 | 手机装完外部完整配置快照后调用，让插件清掉自己的只读缓存；**不重注册 UI、不执行任务** |

两条纪律：

- 声明面只描述"文件/表属于谁"，不要在公共层加领域分支；反过来，插件也不该自己
  复制一套打包或同步逻辑。
- 新增数据库必须同时确认它属于哪一类：配置下发（`offline_*`）、历史回传
  （`history_channels`），还是都不参与。漏声明不会报错，表现是手机上就是缺这份数据。

## 设计决策

1. **单包 + 插件**：`src/lvjiang` 是唯一 Python 包；内置插件与引擎同仓库，
   外置插件以独立仓库检出到 `plugins/`，不进公开发布
2. **显式加载**：`-reg` 逐个显式注册，只有 `--all-plugins` 才按目录扫描加载全部，
   避免自动发现带来的意外加载
3. **全局 config/data**：所有插件共用一份配置，插件资源按 app 名命名空间
4. **AppHooks 数据类**：插件通过数据声明扩展点，而非覆盖通用代码
5. **注入式 UI**：插件只能注入 Tab/菜单，不能替换主窗口类，保证多插件共存
6. **插件即数据目录**：外置插件不打进可执行文件，放在 exe 旁 `plugins/` 即被发现，
   新增插件无需重新打包

## 现有插件

### 燕云十六声（yysls）

- **路径**：`src/lvjiang/apps/yysls/`（内置）
- **功能**：装备词条解析、评分规则、自动调律、材料识别
- **工作流**：`auto_tuning`（自动调律）
- **内置函数**：`to_equipment`、`check_scroll`、`scroll_advance` 等

外置插件（如会员功能）检出到 `plugins/<name>/`，形态与内置插件相同；公开发布
的安装包只携带内置插件，外置插件由开发者放在 exe 旁的 `plugins/` 下。

## 相关实现边界

- [插件与宿主运行边界](../../20-requirements/50-platform/22-plugin-runtime.md)：`PluginRuntimePort`
  的契约与线程约束（需求侧）
- [插件领域数据归属](../../20-requirements/50-platform/21-plugin-data.md)：插件数据与公共
  Profile / 同步的边界（需求侧）
- [配置策略装配](../../20-requirements/50-platform/24-config-policy-bootstrap.md)：
  `config_policy_modules` 的 bootstrap 顺序与门禁
- [配置分层：system / local](04-config-layering.md)：插件声明自己的合并策略与受保护列表
- [DSL 模块化与内置函数](../32-grammar/07-subworkflows.md)：`@builtin_func` 注册、
  重名拒绝与 `_engine` / `_wf` 注入
- [插件国际化](../38-platform/08-i18n.md)：`config/i18n/apps/<app>/<lang>.yaml` 与
  `load_app_i18n`
- [公共后台服务](../38-platform/11-background-services.md)
- [游戏配置加载与查询](../31-models/01-equipment-models.md#游戏配置加载与查询)
- [布局实现职责](../34-scene/02-scene-layout-editing.md#布局实现职责)
- [最优组合页面](../36-graduation/08-scoring-layer.md#最优组合页面边界)
