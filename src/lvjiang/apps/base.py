"""插件钩子数据类定义。

插件通过导出一个 ``AppHooks`` 实例向通用引擎注册自身的扩展点。
引擎在启动时按顺序加载所有注册的插件，并将其声明的 Tab、菜单、
识别器、工作流、内置函数等注入到通用 UI / DSL 引擎中。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AppHooks:
    """插件向通用引擎注册的钩子集合。"""

    # 插件显示名（例如 "燕云十六声"）
    name: str = ""

    # 覆盖主窗口标题（None 表示使用通用默认标题；多插件时后注册者覆盖）
    window_title: str | None = None

    # 左侧 Tab 构建器：[(label, builder), ...]
    # builder 签名：(host: MainWindow) -> QWidget，函数体内延迟 import PyQt6
    left_tab_builders: list[tuple[str, Callable[..., Any]]] = field(default_factory=list)

    # 右侧 Tab 构建器：[(label, builder), ...]，签名同上
    right_tab_builders: list[tuple[str, Callable[..., Any]]] = field(default_factory=list)

    # 菜单栏扩展：[fn(host: MainWindow, menubar: QMenuBar) -> None, ...]
    # 插入位置在「帮助」菜单之前；弹对话框用 host 作 parent
    menu_builders: list[Callable[..., None]] = field(default_factory=list)

    # 识别器类列表（实现 lvjiang.core.recognizers 协议的类）
    recognizer_classes: list[type] = field(default_factory=list)

    # 复杂工作流实现注册：{name: "dotted.path.ClassName"}
    workflow_implementations: dict[str, str] = field(default_factory=dict)

    # 内置函数模块路径列表：["lvjiang.apps.yysls.workflows.builtins.equipment", ...]
    builtin_modules: list[str] = field(default_factory=list)
