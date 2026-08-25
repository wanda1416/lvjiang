"""统计探针的通用护栏：``@never_raises``。

统计是附加行为，长流程（调律、批量任务）里任何一次意外都不能让主流程
中断。参照 ``equip_parser/parser.py`` 合法性判定器的处理：那里丢的是
一条标注，这里丢的是一条统计样本，两者都远比让用户的自动化任务炸掉轻。

只吞 ``Exception``；``KeyboardInterrupt``/``SystemExit`` 照常向上传播，
不能把用户主动中止也吞掉。
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TypeVar

from loguru import logger

F = TypeVar("F", bound=Callable)


def never_raises(fn: F) -> F:
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as e:  # noqa: BLE001 —— 统计探针的设计契约，见模块 docstring
            logger.debug(f"[telemetry] 探针 {fn.__name__} 异常已忽略: {e}")
            return None
    return wrapper  # type: ignore[return-value]
