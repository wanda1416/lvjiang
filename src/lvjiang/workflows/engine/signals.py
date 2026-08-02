"""DSL 引擎的用户可见错误与控制流信号"""


# ─── 用户可见的 DSL 错误 ──────────────────────────────────

class WorkflowUserError(Exception):
    """DSL 脚本中用户操作引发的可预期错误（类型不匹配、字段不存在等）"""


# ─── 控制流信号 ───────────────────────────────────────────

class _BreakSignal(Exception):
    """break 语句触发的跳出信号"""


class _ContinueSignal(Exception):
    """continue 语句触发的跳过当前迭代信号"""


class _ReturnSignal(Exception):
    """return 语句触发的正常退出信号，可携带返回值"""

    def __init__(self, value=None):
        self.value = value


class _GotoSignal(Exception):
    """goto 语句触发的跳转信号"""

    def __init__(self, target: str):
        self.target = target
