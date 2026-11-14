"""插件命名空间包。

内置插件是本包的子包（``lvjiang/apps/<name>/``）；外置默认目录
``<PROJECT_ROOT>/plugins/`` 追加到本包搜索路径，``-reg name@path`` 的任意目录由
宿主加载器显式挂载。加载器、注册表与 ``AppHooks`` 契约属于宿主，位于
``lvjiang.core.plugins``；这里只为既有插件 / 脚本保留同名再导出。
"""
from __future__ import annotations

from ..core.plugins import (  # noqa: F401 - 兼容再导出
    _DEFAULT_PLUGINS_ROOT,
    AppHooks,
    TelemetryDisclosure,
    app_name,
    builtin_app_ids,
    discover_app_ids,
    get_registered_app_ids,
    get_registered_hooks,
    get_registry,
    load_app,
    load_config_policies,
    parse_app_spec,
    register_config_policies,
    register_hooks,
    resolve_app_specs,
)

if _DEFAULT_PLUGINS_ROOT.is_dir() and str(_DEFAULT_PLUGINS_ROOT) not in __path__:
    # 目录名等于插件名时，靠包搜索路径即可解析 lvjiang.apps.<name>；
    # 名称与目录名不一致的任意目录走宿主 load_app 里的显式挂载。
    __path__ = [*__path__, str(_DEFAULT_PLUGINS_ROOT)]
