"""串行化定义维护与 Profile 写入，不获取任何用户执行锁。"""

from functools import wraps
from threading import RLock
from typing import Callable, ParamSpec, TypeVar

profile_lock = RLock()
P = ParamSpec("P")
T = TypeVar("T")


def profile_operation(fn: Callable[P, T]) -> Callable[P, T]:
    @wraps(fn)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        with profile_lock:
            return fn(*args, **kwargs)
    return guarded
