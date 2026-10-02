"""工作流错误类型。

可预期的脚本错误与引擎执行失败必须保持不同语义：前者可直接提示用户修改
脚本，后者表示工作流没有正常完成，不能把已经产生的部分输出伪装成成功结果。
"""

from __future__ import annotations

from typing import Any


class WorkflowUserError(Exception):
    """DSL 脚本中用户操作引发的可预期错误（类型不匹配、字段不存在等）。"""


class WorkflowExecutionError(RuntimeError):
    """Python 工作流意外失败，并携带失败前已经产生的部分输出。"""

    def __init__(
        self,
        workflow_name: str,
        partial_output: dict[str, Any],
    ) -> None:
        self.workflow_name = workflow_name
        self.partial_output = dict(partial_output)
        super().__init__(f"Python 工作流 {workflow_name} 执行失败")


class WorkflowAbort(Exception):
    """宿主要求中止本次执行——不是执行错误，引擎不按异常记账。

    典型来源是无人值守批量：工作流调 pause/confirm 要人动手，宿主当场改道
    中止，再由恢复流程收拾现场。这条路径在无人值守下是**预期分支**，所以
    引擎只负责让它穿透，不打 ERROR、不打 traceback；要不要记失败、怎么恢复
    由宿主决定（见 ui/batch 的 UnattendedInterrupt）。

    它不属于 DSL ``try/catch`` 能捕获的四类错误，业务脚本的 try 吞不掉它，
    否则无人值守会被某个脚本的兜底 catch 消化成「成功」。
    """
