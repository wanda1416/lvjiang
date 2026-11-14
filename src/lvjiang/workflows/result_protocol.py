"""wf 顶层返回值协议（单次运行与批量共用）。

框架只解释标准结构，不认识任何具体任务：

- ``{"status": "failed", "message": ...}``：失败，``message`` 为错误文案；
- 小于 0 的数字（如 ``return -1``）：失败；
- 其余返回值（``None``、``0``、正数、其他 dict 等）不视为失败。

批量生命周期另有 ``success/skipped/stopped`` 等状态，常量同样定义在这里。
"""

from __future__ import annotations

from ..i18n import tr

RESULT_SUCCESS = "success"
RESULT_SKIPPED = "skipped"
RESULT_FAILED = "failed"
RESULT_STOPPED = "stopped"


def return_failure_message(value: object) -> str | None:
    """顶层返回值表示失败时给出错误文案，否则返回 ``None``。"""
    if isinstance(value, dict):
        if value.get("status") != RESULT_FAILED:
            return None
        message = value.get("message")
        return str(message) if message else tr("脚本返回失败")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
        return tr("脚本返回失败（返回值 {value}）").format(value=_format_number(value))
    return None


def _format_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
