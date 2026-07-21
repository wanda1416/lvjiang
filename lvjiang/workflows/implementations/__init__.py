"""代码工作流注册表

复杂工作流以 Python 类实现，通过 workflows.yaml 中的 class 字段引用。
"""

import importlib

_REGISTRY = {
    "auto_tuning": "lvjiang.workflows.implementations.auto_tuning.AutoTuningWorkflow",
}


def get_workflow_class(name: str):
    """根据注册名获取工作流类"""
    cls_path = _REGISTRY.get(name)
    if cls_path is None:
        raise ValueError(f"未知代码工作流: {name}，可用: {list(_REGISTRY.keys())}")
    module_path, cls_name = cls_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, cls_name)
