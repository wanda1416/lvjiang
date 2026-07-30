"""内置函数注册表核心

提供装饰器和查询接口，各子模块通过 builtin_func 装饰器注册函数。
"""

from typing import Callable

# 全局函数注册表
_FUNCTION_REGISTRY: dict[str, Callable] = {}


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
        _FUNCTION_REGISTRY[name] = fn
        return fn
    return decorator


def get_function(name: str) -> Callable | None:
    """获取已注册的内置函数，不存在返回 None"""
    return _FUNCTION_REGISTRY.get(name)


def list_functions() -> list[str]:
    """返回所有已注册函数名"""
    return list(_FUNCTION_REGISTRY.keys())
