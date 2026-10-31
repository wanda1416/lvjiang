"""内置函数注册表核心

提供装饰器和查询接口，各子模块通过 builtin_func 装饰器注册函数。
"""

from contextlib import contextmanager
from typing import Callable

# 全局函数注册表
_FUNCTION_REGISTRY: dict[str, Callable] = {}

# 外置脚本加载期间的捕获栈：非空时 @builtin_func 写进栈顶，不进全局表。
_CAPTURE_STACK: list[dict[str, Callable]] = []


@contextmanager
def capture_registrations():
    """捕获块内 ``@builtin_func`` 的注册结果，不写入全局注册表。

    外置脚本（``config/local/workflows`` 下的 .py）用它把函数装进引擎私有
    命名空间：并发的多个引擎各捕获各自的，互不覆盖；脚本改完重新执行即生效，
    引擎结束后进程里也不留残余。
    """
    bucket: dict[str, Callable] = {}
    _CAPTURE_STACK.append(bucket)
    try:
        yield bucket
    finally:
        _CAPTURE_STACK.pop()


def builtin_func(name: str):
    """装饰器：注册内置函数

    注册时预解析首参注入类型（_engine/_wf）并缓存到 fn._inject，
    避免每次调用都走 inspect 反射。

    用法：
        @builtin_func("is_good_equip")
        def _is_good_equip(scan_result, *args):
            ...
    """
    def decorator(fn: Callable):
        import inspect
        params = list(inspect.signature(fn).parameters.keys())
        if params and params[0] == '_engine':
            fn._inject = 'engine'
        elif params and params[0] == '_wf':
            fn._inject = 'wf'
        else:
            fn._inject = None
        if _CAPTURE_STACK:
            _CAPTURE_STACK[-1][name] = fn
        else:
            _FUNCTION_REGISTRY[name] = fn
        return fn
    return decorator


def get_function(name: str) -> Callable | None:
    """获取已注册的内置函数，不存在返回 None"""
    return _FUNCTION_REGISTRY.get(name)


def list_functions() -> list[str]:
    """返回所有已注册函数名"""
    return list(_FUNCTION_REGISTRY.keys())
