"""通用工作流内置函数包。

按功能分类组织（仅通用部分）：

- ``arithmetic``: 基础运算 (add/sub/mul/div/mod/min/max/abs)
- ``general``:    通用工具 (concat/range/count_key/contains/find_key/append)
- ``system``:     系统与 UI (messagebox/save/panel_rows/panel_cols)

燕云专属内置函数（``equipment`` / ``bag_traversal``）位于
``lvjiang.apps.yysls.workflows.builtins``，由燕云插件在加载时注册。
"""

# 导出注册表核心接口（base.py 通过 builtins.get_function() 调用）
from ._registry import builtin_func, get_function, list_functions  # noqa: F401

# 导入子模块触发函数注册
from . import arithmetic   # noqa: F401
from . import general      # noqa: F401
from . import system       # noqa: F401
