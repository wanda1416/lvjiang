"""工作流层可向用户直接展示的错误。"""


class WorkflowUserError(Exception):
    """DSL 脚本中用户操作引发的可预期错误（类型不匹配、字段不存在等）。"""
