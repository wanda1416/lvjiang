"""设备端插件加载 —— 把游戏插件的工作流实现与内置函数注入注册表

桌面端由 ``__main__.py`` 按命令行 ``-reg <name>`` 加载插件；设备端入口是
Chaquopy 直接调 ``task_runner`` / ``workflow_runner``，没有命令行，若不显式
加载就会出现两类静默故障：

1. **类实现退化成同名 .wf**：``discover_scripts()`` 的 class 来源取自
   ``implementations.list_workflows()``（由插件注册），注册表为空时
   ``auto_tuning`` 会解析成 ``config/system/workflows/``
   下的同名旧 DSL 文件，与类实现行为不同。
2. **插件内置函数缺失**：插件函数由 ``builtin_modules`` 导入时经
   ``@builtin_func`` 注册，未加载则 DSL 调用直接报未知函数。

插件模块顶层刻意不 import PyQt6（builder 内延迟导入），``register_hooks``
也只把 tab/menu builder 存进注册表而不调用，故设备端加载是安全的。
"""
from __future__ import annotations

import threading

from loguru import logger

_configured_app_ids: tuple[str, ...] = ()
_loaded_app_ids: set[str] = set()
_lock = threading.Lock()


def configure_apps(app_ids: str | tuple[str, ...] | list[str]) -> None:
    """由设备构建组合根声明要加载的 app；逗号分隔字符串也可用。"""
    global _configured_app_ids
    with _lock:
        values: tuple[str, ...] | list[str]
        if isinstance(app_ids, str):
            values = app_ids.split(",")
        else:
            values = app_ids
        _configured_app_ids = tuple(
            dict.fromkeys(str(item).strip() for item in values if str(item).strip())
        )


def ensure_loaded(app_ids: tuple[str, ...] | list[str] | None = None) -> None:
    """幂等加载调用方指定或设备构建已配置的 app。

    在任何依赖工作流注册表或内置函数的入口调用（list_tasks / 任务执行 /
    引擎装配）。单个插件加载失败只记日志不抛出——让任务在真正用到缺失
    实现时报具体错误，比在列任务阶段整体失败更好定位。
    """
    targets = tuple(app_ids) if app_ids is not None else _configured_app_ids
    with _lock:
        from ...apps import load_app, register_hooks

        for name in targets:
            if name in _loaded_app_ids:
                continue
            try:
                register_hooks(load_app(name))
                _loaded_app_ids.add(name)
                logger.info(f"[ondevice] 插件已加载: {name}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[ondevice] 插件加载失败 {name}: {e}")
