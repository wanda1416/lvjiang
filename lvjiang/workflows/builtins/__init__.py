"""工作流内置函数包

按功能分类组织：
- arithmetic: 基础运算 (add/sub/mul/div/mod/min/max/abs)
- general:    通用工具 (concat/range/count_key/contains/find_key/append)
- equipment:  装备解析与判定 (to_equipment/evaluate/affix_cap/is_good_equip/...)
- bag_traverse: 背包遍历 (make_fingerprint/check_scroll/notify_scroll/...)
- system:     系统与 UI (messagebox/save/panel_rows/panel_cols)
"""

# 导出注册表核心接口（base.py 通过 builtins.get_function() 调用）
from ._registry import builtin_func, get_function, list_functions  # noqa: F401

# 导入子模块触发函数注册
from . import arithmetic   # noqa: F401
from . import general      # noqa: F401
from . import equipment    # noqa: F401
from . import bag_traverse # noqa: F401
from . import system       # noqa: F401
