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
# src/lvjiang/apps/base.py
@dataclass
class AppHooks:
    name: str                                  # 插件显示名
    requires_app_ids: tuple[str, ...] = ()      # 必须先注册的插件稳定 ID
    window_title: str | None = None            # 覆盖主窗口标题（多插件时后注册者覆盖）
    left_tab_builders: list = field(default_factory=list)   # [(label, builder), ...]
    right_tab_builders: list = field(default_factory=list)  # builder(host) -> QWidget
    menu_builders: list = field(default_factory=list)       # [fn(host, menubar), ...]
    recognizer_classes: list = field(default_factory=list)
    workflow_implementations: dict = field(default_factory=dict)
    builtin_modules: list = field(default_factory=list)
```

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

- `core.config.document_store.DocumentDirectoryStore`：主节点写入、线程内互斥、版本标记和
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
    name="我的游戏",
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

- [公共后台服务](../38-platform/11-background-services.md)
- [游戏配置加载与查询](../31-models/01-equipment-models.md#游戏配置加载与查询)
- [布局实现职责](../34-scene/02-scene-layout-editing.md#布局实现职责)
- [最优组合页面](../36-graduation/08-scoring-layer.md#最优组合页面边界)
