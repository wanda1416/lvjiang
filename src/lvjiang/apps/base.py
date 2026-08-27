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

    # 统计事件 schema 模块路径列表（与 builtin_modules 同款「import 即注册」
    # 语义）：["lvjiang.apps.yysls.telemetry.schemas", ...]。core.telemetry
    # 不认识任何插件领域词汇，字段声明由插件自己在这些模块里注册。
    telemetry_modules: list[str] = field(default_factory=list)

    # 配置合并策略模块路径列表（同上「import 即注册」语义）：
    # ["lvjiang.apps.yysls.config.merge_policy", ...]。core.config 不认识
    # 任何插件领域词汇，插件私有配置文件（如 yysls/game_config.yaml）里
    # 哪些列表是登记表、哪些列表的出厂条目不可删除，由插件自己在这些模块里
    # 调用 core.config.resolver 的 register_registry_list_paths /
    # register_protected_list_paths 声明。
    config_policy_modules: list[str] = field(default_factory=list)
