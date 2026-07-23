# 插件系统架构与开发指南

## 架构概述

律匠采用「通用引擎 + 插件」的单包架构：

- `src/`：通用视觉 RPA 引擎（截图、OCR、输入、场景、DSL 引擎、通用 GUI）
- `src/apps/<name>/`：游戏/场景专属插件
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
# src/apps/base.py
@dataclass
class AppHooks:
    name: str                                  # 插件显示名
    window_title: str | None = None            # 覆盖主窗口标题
    main_window_class: type | str | None = None  # 自定义主窗口类（支持字符串路径延迟导入）
    left_tab_builders: list = field(default_factory=list)   # [(label, builder), ...]
    right_tab_builders: list = field(default_factory=list)
    menu_builders: list = field(default_factory=list)       # [fn(menubar), ...]
    recognizer_classes: list = field(default_factory=list)
    workflow_implementations: dict = field(default_factory=dict)
    builtin_modules: list = field(default_factory=list)
```

## 插件目录结构

```
src/apps/<name>/
├── __init__.py        # 导出 hooks: AppHooks 实例
├── constants.py       # 插件专属常量
├── core/              # 插件专属能力（识别器、数据库等）
├── workflows/
│   ├── implementations/  # 复杂工作流实现
│   └── builtins/         # 插件专属内置函数
└── ui/                # 插件专属 UI 组件
    └── main_window.py # 自定义主窗口（可选）
```

## 开发新插件

### 1. 创建插件目录

```bash
mkdir -p src/apps/mygame
touch src/apps/mygame/__init__.py
```

### 2. 注册插件

在 `src/apps/__init__.py` 的 `_APP_REGISTRY` 中添加：

```python
_APP_REGISTRY: dict[str, str] = {
    "yysls": "src.apps.yysls",
    "mygame": "src.apps.mygame",  # 新增
}
```

### 3. 实现 hooks

```python
# src/apps/mygame/__init__.py
from ..base import AppHooks

hooks = AppHooks(
    name="我的游戏",
    window_title="律匠 - 我的游戏自动化工具",
    # 可选：自定义主窗口（字符串路径延迟导入，避免 import 时触发 PyQt6）
    main_window_class="src.apps.mygame.ui.main_window.MainWindow",
    # 可选：左侧 Tab
    left_tab_builders=[
        ("自动化", build_auto_panel),
    ],
    # 可选：右侧 Tab
    right_tab_builders=[
        ("状态", build_status_panel),
    ],
    # 可选：识别器
    recognizer_classes=[MyGameRecognizer],
    # 可选：复杂工作流
    workflow_implementations={
        "auto_farm": "src.apps.mygame.workflows.implementations.auto_farm.AutoFarmWorkflow",
    },
    # 可选：内置函数模块（导入即触发 @builtin_func 注册）
    builtin_modules=[
        "src.apps.mygame.workflows.builtins.items",
    ],
)
```

### 4. 添加识别器（可选）

```python
# src/apps/mygame/core/recognizer.py
from src.core.recognizers._registry import Recognizer

class MyGameRecognizer:
    name = "mygame_recognizer"
    
    def recognize(self, image: np.ndarray, **kwargs) -> dict:
        # 实现识别逻辑
        return result
```

### 5. 添加内置函数（可选）

```python
# src/apps/mygame/workflows/builtins/items.py
from src.workflows.builtins._registry import builtin_func

@builtin_func("get_item_name")
def _get_item_name(_engine, item_id: str) -> str:
    """根据物品 ID 获取名称"""
    return ITEMS.get(item_id, "未知物品")
```

### 6. 添加复杂工作流（可选）

```python
# src/apps/mygame/workflows/implementations/auto_farm.py
from src.workflows.base import BaseWorkflow

class AutoFarmWorkflow(BaseWorkflow):
    """自动 farming 工作流"""
    
    def execute(self, **params):
        # 实现工作流逻辑
        pass
```

## 注册机制

插件加载时，`register_hooks()` 会自动：

1. **识别器**：调用 `src.core.recognizers.register_recognizer()` 注册
2. **工作流**：调用 `src.workflows.implementations.register_workflow()` 注册
3. **内置函数**：导入 `builtin_modules` 中的模块，触发 `@builtin_func` 装饰器注册
4. **UI 扩展**：将 tab/menu builders 注入通用 MainWindow

## 设计决策

1. **单包 + 插件**：避免双包发布的复杂度，`src/` 是唯一 Python 包
2. **`-reg` 参数**：显式注册，避免自动发现带来的意外加载
3. **全局 config/data**：所有插件共用一份配置，避免配置分散
4. **AppHooks 数据类**：插件通过数据声明扩展点，而非覆盖通用代码
5. **延迟导入**：`main_window_class` 支持字符串路径，避免 import 时触发 PyQt6

## 现有插件

### 燕云十六声（yysls）

- **路径**：`src/apps/yysls/`
- **功能**：装备词条解析、评分规则、自动调律、材料识别
- **工作流**：`auto_tuning`（自动调律）
- **内置函数**：`to_equipment`、`check_scroll`、`traverse_bag` 等
