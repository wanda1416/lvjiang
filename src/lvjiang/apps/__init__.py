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
    "yysls": "lvjiang.apps.yysls",
}



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

    if hooks.id:
        app_ids = registry.setdefault("app_ids", [])
        if hooks.id not in app_ids:
            app_ids.append(hooks.id)

    if hooks.window_title:
        registry["window_title"] = hooks.window_title
        logger.info("[plugin]   window_title = %s", hooks.window_title)

    if hooks.left_tab_builders:
        registry.setdefault("left_tab_builders", []).extend(hooks.left_tab_builders)
        logger.info("[plugin]   left tabs: %s", [label for label, _ in hooks.left_tab_builders])

    if hooks.right_tab_builders:
        registry.setdefault("right_tab_builders", []).extend(hooks.right_tab_builders)
        logger.info("[plugin]   right tabs: %s", [label for label, _ in hooks.right_tab_builders])

    if hooks.menu_builders:
        registry.setdefault("menu_builders", []).extend(hooks.menu_builders)
        logger.info("[plugin]   menu builders: %d", len(hooks.menu_builders))

    if hooks.theme_stylesheet_builders:
        registry.setdefault("theme_stylesheet_builders", []).extend(
            hooks.theme_stylesheet_builders
        )

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

    if hooks.profile_period_modules:
        registry.setdefault("profile_period_modules", []).extend(
            hooks.profile_period_modules
        )
        for mod_path in hooks.profile_period_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   profile period module 已加载: %s", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] Profile 周期模块加载失败: %s", mod_path)
                raise

    if hooks.telemetry_modules:
        registry.setdefault("telemetry_modules", []).extend(hooks.telemetry_modules)
        # 实际导入模块触发 register_schema() 注册；单个插件的 schema
        # 声明出错不该拖垮整个插件加载，仅记日志跳过。
        for mod_path in hooks.telemetry_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   telemetry module 已加载: %s", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] 统计事件 schema 模块加载失败: %s", mod_path)

    if hooks.telemetry_disclosures:
        registry.setdefault("telemetry_disclosures", []).extend(
            hooks.telemetry_disclosures
        )

    if hooks.config_policy_modules:
        registry.setdefault("config_policy_modules", []).extend(hooks.config_policy_modules)
        # 实际导入模块触发 register_registry_list_paths /
        # register_protected_list_paths 注册；单个插件的声明出错不该拖垮
        # 整个插件加载，仅记日志跳过。
        for mod_path in hooks.config_policy_modules:
            try:
                importlib.import_module(mod_path)
                logger.info("[plugin]   config policy module 已加载: %s", mod_path)
            except Exception:  # noqa: BLE001
                logger.exception("[plugin] 配置合并策略模块加载失败: %s", mod_path)


def load_config_policies() -> None:
    """只加载各已登记插件的配置策略声明，不注入 Tab / 工作流 / 识别器。

    离线工具（`scripts/add_content_version.py`、配置层单测）需要完整的
    「哪些路径是登记表 / 哪些目录带 content_version」注册表，但不需要——
    也不该——把插件的 UI 与工作流一并装配起来。走 ``register_hooks`` 会
    连带注册识别器与内置函数，对一个补字段的脚本是多余的副作用。

    单个插件声明出错只记日志跳过，理由同 register_hooks 里的处理。
    """
    for name, module_path in _APP_REGISTRY.items():
        try:
            module = importlib.import_module(module_path)
        except Exception:  # noqa: BLE001
            logger.exception("[plugin] 加载插件模块失败: %s", module_path)
            continue
        hooks = getattr(module, "hooks", None)
        if not isinstance(hooks, AppHooks):
            continue
        for mod_path in hooks.config_policy_modules:
            try:
                importlib.import_module(mod_path)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[plugin] 配置策略模块加载失败 (%s): %s", name, mod_path)


# ── 全局注册表（内存中的插件扩展点汇总） ─────────────────────────────
_GLOBAL_REGISTRY: dict[str, Any] = {}


def _get_global_registry() -> dict[str, Any]:
    return _GLOBAL_REGISTRY


def get_registry() -> dict[str, Any]:
    """返回当前全局注册表（供 MainWindow / 引擎读取）。"""
    return _GLOBAL_REGISTRY


def get_registered_app_ids() -> tuple[str, ...]:
    """返回已装配 app 的稳定 ID，保持注册顺序。"""
    return tuple(str(item) for item in _GLOBAL_REGISTRY.get("app_ids", ()))
