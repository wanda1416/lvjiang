"""工作流 DSL v2 引擎

运行时主体：直接持有硬件后端，管理 session/context 生命周期，
递归执行 AST 节点，支持完整控制流（if/for/loop/break/goto）。
模块化通过 import/def/call proc 实现过程复用，变量隔离。

包结构（WorkflowEngine 由各职责 Mixin 组合，见 core.py）：
- signals.py       WorkflowUserError 与控制流信号
- core.py          主类：生命周期、执行入口、语句分发
- actions.py       基础指令：click/drag/wait
- panel.py         panel 对齐与 cell 级操作
- data_ops.py      数据指令：scan/recognize/collect/eval/call proc
- control_flow.py  控制流：if/for/for-range/loop
- evaluation.py    条件求值与变量解析
"""
from .core import WorkflowEngine
from .signals import WorkflowUserError

__all__ = [
    "WorkflowEngine",
    "WorkflowUserError",
]
