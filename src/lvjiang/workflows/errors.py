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
