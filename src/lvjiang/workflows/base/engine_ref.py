"""Python 工作流程访问 WorkflowEngine 原语的统一入口。

BaseWorkflow 的各 Mixin 都是 WorkflowEngine 公共原语的薄门面，自身不再
保留第二份实现。引擎引用由 ``WorkflowEngine._execute_python_workflow``
在 ``run()`` 之前注入，所有运行入口（工作流程运行器、脚本工作台、批量
运行器、设备端运行器）都经由引擎，因此拿不到引擎属于装配错误，必须当场
报错——留一条"没有引擎就自己干"的兜底分支，等于把刚删掉的那份实现又
藏回来，而且永远不会被执行到，也就永远不会被测到。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..engine.core import WorkflowEngine


def require_engine(owner, primitive: str) -> "WorkflowEngine":
    """取出注入的引擎引用；未注入则说明工作流程不是由引擎启动的。

    Args:
        owner: 持有 ``_engine`` 的工作流程实例。
        primitive: 原语名，用于报错时指明是哪类能力拿不到引擎。
    """
    engine = getattr(owner, "_engine", None)
    if engine is None:
        raise RuntimeError(
            f"{primitive}必须由 WorkflowEngine 执行；"
            "请通过工作流程运行器启动 Python 业务流"
        )
    return engine
