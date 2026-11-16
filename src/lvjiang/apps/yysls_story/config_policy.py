"""剧情文案库的配置类型登记（导入即注册）。

``yysls/auto_plot/<组名>.json`` 带 ``content_version``。``tools/auto_plot`` 原来
由 core 的 ``versioning.py`` 登记；自动剧情归插件之后，这条路径也由插件自己声明，
core 不再认识任何剧情目录。

导入由 ``AppHooks.config_policy_modules`` 触发，所以离线工具
（``scripts/add_content_version.py``、配置层单测）也能看到这份登记。
"""

from __future__ import annotations

from lvjiang.core.config.versioning import register_versioned_dir

register_versioned_dir("yysls/auto_plot", "*.json", depth=1)

__all__ = ["register_versioned_dir"]
