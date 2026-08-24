"""代码工作流注册表（通用）。

复杂工作流以 Python 类实现，由插件在 ``workflow_implementations`` 中注册。
插件可通过 ``register_workflow()`` 注入自身实现。

燕云专属工作流（如 ``auto_tuning``）位于
``lvjiang.apps.yysls.workflows.implementations``，由燕云插件在加载时注册。
"""
from __future__ import annotations

import importlib
import logging

logger = logging.getLogger(__name__)

# 通用注册表（初始为空，由插件注入）
_REGISTRY: dict[str, str] = {}


def register_workflow(name: str, class_path: str) -> None:
    """注册一个代码工作流实现。

    Args:
        name: 工作流名字（即脚本 id）
        class_path: 完整类路径，例如 "lvjiang.apps.yysls.workflows.implementations.auto_tuning.AutoTuningWorkflow"
    """
    if name in _REGISTRY:
        logger.warning("工作流 %s 重复注册，旧实现将被覆盖", name)
    _REGISTRY[name] = class_path
    logger.info("[workflow] 注册: %s -> %s", name, class_path)


def get_workflow_class(name: str):
    """根据注册名获取工作流类"""
    cls_path = _REGISTRY.get(name)
    if cls_path is None:
        raise ValueError(f"未知代码工作流: {name}，可用: {list(_REGISTRY.keys())}")
    module_path, cls_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)


def list_workflows() -> list[str]:
    """返回所有已注册工作流名字。"""
    return list(_REGISTRY.keys())
