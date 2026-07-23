"""插件注册表与加载器。

插件通过命令行参数 ``-reg <name>`` 注册。引擎在启动时按顺序调用
``load_app(name)`` 加载插件模块，并从模块顶层 ``hooks`` 属性获取
``AppHooks`` 实例，再通过 ``register_hooks`` 把识别器 / 工作流 /
内置函数注入到各注册表中。
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

from .base import AppHooks

logger = logging.getLogger(__name__)

# 插件名 → 模块路径。新增插件时在此登记即可。
_APP_REGISTRY: dict[str, str] = {
    "yysls": "src.apps.yysls",
}


def register_app(name: str, module_path: str) -> None:
    """程序化登记一个插件（供测试 / 动态扩展使用）。"""
    _APP_REGISTRY[name] = module_path


def list_apps() -> list[str]:
    """返回所有已登记的插件名。"""
    return list(_APP_REGISTRY.keys())


def load_app(name: str) -> AppHooks:
    """加载指定插件并返回其 ``AppHooks``。

    插件模块必须在顶层导出 ``hooks: AppHooks`` 属性。
    """
    if name not in _APP_REGISTRY:
        raise KeyError(
            f"未登记的插件: {name!r}。可用插件: {list(_APP_REGISTRY)}"
        )
    module_path = _APP_REGISTRY[name]
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("加载插件 %s 失败", module_path)
        raise RuntimeError(f"加载插件 {name!r} 失败: {exc}") from exc

    hooks = getattr(module, "hooks", None)
    if not isinstance(hooks, AppHooks):
        raise RuntimeError(
            f"插件 {name!r} 模块 {module_path} 未导出有效的 hooks: AppHooks"
        )
    return hooks


def register_hooks(hooks: AppHooks, registry: dict[str, Any] | None = None) -> None:
    """把插件声明的扩展点注入到各注册表。

    阶段 1 仅做日志登记；阶段 2/3/5 会在此处对接识别器、工作流、内置函数注册表。
    """
    registry = registry if registry is not None else _get_global_registry()

    logger.info("[plugin] 注册插件: %s", hooks.name or "<unnamed>")

    if hooks.window_title:
        registry["window_title"] = hooks.window_title
        logger.info("[plugin]   window_title = %s", hooks.window_title)

    if hooks.main_window_class is not None:
        # 支持字符串路径延迟导入
        if isinstance(hooks.main_window_class, str):
            module_path, _, class_name = hooks.main_window_class.rpartition(".")
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            registry["main_window_class"] = cls
            logger.info("[plugin]   main_window_class = %s (lazy)", class_name)
        else:
            registry["main_window_class"] = hooks.main_window_class
            logger.info("[plugin]   main_window_class = %s", hooks.main_window_class.__name__)

    if hooks.left_tab_builders:
        registry.setdefault("left_tab_builders", []).extend(hooks.left_tab_builders)
        logger.info("[plugin]   left tabs: %s", [label for label, _ in hooks.left_tab_builders])

    if hooks.right_tab_builders:
        registry.setdefault("right_tab_builders", []).extend(hooks.right_tab_builders)
        logger.info("[plugin]   right tabs: %s", [label for label, _ in hooks.right_tab_builders])

    if hooks.menu_builders:
        registry.setdefault("menu_builders", []).extend(hooks.menu_builders)
        logger.info("[plugin]   menu builders: %d", len(hooks.menu_builders))

    if hooks.recognizer_classes:
        registry.setdefault("recognizer_classes", []).extend(hooks.recognizer_classes)
        # 实际注入到识别器注册表
        try:
            from ..core.recognizers import register_recognizer
            for cls in hooks.recognizer_classes:
                register_recognizer(cls)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 识别器注册失败")
        logger.info("[plugin]   recognizers: %d", len(hooks.recognizer_classes))

    if hooks.workflow_implementations:
        registry.setdefault("workflow_implementations", {}).update(hooks.workflow_implementations)
        # 实际注入到工作流注册表
        try:
            from ..workflows.implementations import register_workflow
            for name, cls_path in hooks.workflow_implementations.items():
                register_workflow(name, cls_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 工作流注册失败")
        logger.info("[plugin]   workflows: %s", list(hooks.workflow_implementations.keys()))

    if hooks.builtin_modules:
        registry.setdefault("builtin_modules", []).extend(hooks.builtin_modules)
        # 实际导入模块触发 @builtin_func 装饰器注册
        try:
            for mod_path in hooks.builtin_modules:
                importlib.import_module(mod_path)
                logger.info("[plugin]   builtin module 已加载: %s", mod_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 内置函数模块加载失败")
        logger.info("[plugin]   builtin modules: %d", len(hooks.builtin_modules))


# ── 全局注册表（内存中的插件扩展点汇总） ─────────────────────────────
_GLOBAL_REGISTRY: dict[str, Any] = {}


def _get_global_registry() -> dict[str, Any]:
    return _GLOBAL_REGISTRY


def get_registry() -> dict[str, Any]:
    """返回当前全局注册表（供 MainWindow / 引擎读取）。"""
    return _GLOBAL_REGISTRY
