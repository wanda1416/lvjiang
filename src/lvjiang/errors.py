"""跨层异常类别：决定日志要不要记录堆栈。

日志里的堆栈只有一个用途——定位「代码没预料到」的位置。而业务主动拒绝
（`profile_reject`）、DSL 脚本自身的可预期错误（变量名写错、类型不匹配）
这些异常的消息里已经写清了原因、行号和上下文，堆栈里只剩引擎内部帧，
打出来只会把一条结论渲染成连环崩溃。

所以异常按「要不要堆栈」分成两类：

- `ExpectedError`：预期结果，记一行原因，不附堆栈；
- 其余异常：意外，记原因 + 堆栈。

判定统一由类型决定，各个 catch 点不要再自己写 getattr 或按消息文本猜。
"""

from __future__ import annotations

import traceback

from loguru import logger


class ExpectedError(Exception):
    """预期结果：消息已说明发生了什么，日志只记一行原因，不附堆栈。

    引擎照常把它向上抛出，不做吞并——是否判定失败、如何提示用户由调用方决定。
    ``log_level`` 是这一行的记账级别：业务规则正常生效用 ``WARNING``，
    需要用户改脚本用 ``ERROR``。
    """

    log_level = "WARNING"

    def log_reason(self, message: str) -> None:
        """按本类的级别记一行失败原因（不带堆栈）。"""
        logger.log(self.log_level, f"{message}: {self}")


def log_failure(message: str, error: BaseException) -> None:
    """按异常类别记录一次失败：预期结果一行原因，意外异常附堆栈。

    堆栈取自 `error` 自身（而不是当前 `except` 上下文），所以在异常已经穿过
    几层边界、或不在 `except` 块里调用时同样能打出真实调用栈。
    """
    if isinstance(error, ExpectedError):
        error.log_reason(message)
        return
    detail = "".join(traceback.format_exception(error)).rstrip()
    logger.error(f"{message}: {error}\n{detail}")
