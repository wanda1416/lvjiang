# 插件系统架构与开发指南

## 架构概述

律匠采用「通用引擎 + 插件」的单包架构：

- `src/`：通用视觉 RPA 引擎（截图、OCR、输入、场景、DSL 引擎、通用 GUI）
- `src/lvjiang/apps/<name>/`：游戏/场景专属插件
- `config/`、`data/`：全局共享，所有插件共用

启动方式：
```bash
python -m src                 # 纯通用（场景编辑 + 识别测试）
python -m src -reg yysls      # 通用 + 燕云插件
python -m src -reg foo -reg bar  # 多插件同时加载
```

## 核心接口：AppHooks

插件通过 `AppHooks` 数据类向引擎声明扩展点：

```python
# src/lvjiang/apps/base.py
@dataclass
class AppHooks:
    name: str                                  # 插件显示名
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

宿主 API（插件页面通过 host 使用，不摸私有属性）：

- `host.active_user_name() -> str`：当前活跃用户名
- `host.is_running` / `host.request_stop()`：自动化运行状态与停止
- `host.append_log(text)`：写运行日志
- `host.run_workflow_implementation(impl_name, flow_name, required_scenes, configure)`：
  启动已注册工作流（通用脚手架 + `configure(wf_instance, engine)` 回调写入专属参数）
- 信号 `automation_state_changed(str)`（"running"/"not_ready"/"ready"）、
  `user_changed(str)`
- F9 分发：当前左侧 Tab 若实现 `f9_run()` 则交由其处理，否则走通用工作流
```

## 插件目录结构

```
src/lvjiang/apps/<name>/
├── __init__.py        # 导出 hooks: AppHooks 实例
├── constants.py       # 插件专属常量
├── core/              # 插件专属能力（识别器、数据库等）
├── workflows/
│   ├── implementations/  # 复杂工作流实现
│   └── builtins/         # 插件专属内置函数
└── ui/                # 插件专属 UI 组件（Tab 页面、对话框、菜单 builder）
```

## 开发新插件

### 1. 创建插件目录

```bash
mkdir -p src/lvjiang/apps/mygame
touch src/lvjiang/apps/mygame/__init__.py
```

### 2. 注册插件

在 `src/lvjiang/apps/__init__.py` 的 `_APP_REGISTRY` 中添加：

```python
_APP_REGISTRY: dict[str, str] = {
    "yysls": "lvjiang.apps.yysls",
    "mygame": "lvjiang.apps.mygame",  # 新增
}
```

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

插件加载时，`register_hooks()` 会自动：

1. **识别器**：调用 `lvjiang.core.recognizers.register_recognizer()` 注册
2. **工作流**：调用 `lvjiang.workflows.implementations.register_workflow()` 注册
3. **内置函数**：导入 `builtin_modules` 中的模块，触发 `@builtin_func` 装饰器注册
4. **UI 扩展**：将 tab/menu builders 收集到注册表，由通用 MainWindow 构建时消费
   （多插件 extend 叠加，按 `-reg` 顺序）

## 设计决策

1. **单包 + 插件**：避免双包发布的复杂度，`src/` 是唯一 Python 包
2. **`-reg` 参数**：显式注册，避免自动发现带来的意外加载
3. **全局 config/data**：所有插件共用一份配置，避免配置分散
4. **AppHooks 数据类**：插件通过数据声明扩展点，而非覆盖通用代码
5. **注入式 UI**：插件只能注入 Tab/菜单，不能替换主窗口类，保证多插件共存

## 现有插件

### 燕云十六声（yysls）

- **路径**：`src/lvjiang/apps/yysls/`
- **功能**：装备词条解析、评分规则、自动调律、材料识别
- **工作流**：`auto_tuning`（自动调律）
- **内置函数**：`to_equipment`、`check_scroll`、`traverse_bag` 等
