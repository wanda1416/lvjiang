"""燕云剧情插件（yysls_story）：剧情选项文案库 + 自动剧情内置函数。

原来的 ``lvjiang.core.story``「通用工具」，现在是独立插件：只有燕云的剧情
跳过工作流在用它——选项文案是游戏内容，识别区域是燕云的对话场景。整块能力
集中在**本目录**，声明依赖 ``yysls`` 插件：

- ``requires_app_ids=("yysls",)``：单独加载或排在 yysls 之前都会在注册期
  明确报错，而不是半工作状态；
- 菜单入口「自动剧情」通过 ``require_plugin_menu(menubar, "yysls")`` 注入
  燕云插件创建的「燕云」菜单（与 ``yysls_premium`` 的「采集录制」同款）；
- 配置目录 ``yysls/auto_plot/``，content_version 由本插件经
  ``config_policy_modules`` 登记，core 不认识任何剧情目录。

本包顶层**不得** import PyQt6：设备端装配 hooks 时会导入内置函数模块，
编辑器对话框单独放在 ``.dialog``，由菜单构建器延迟导入。
"""

from __future__ import annotations

from lvjiang.core.plugins import AppHooks
from lvjiang.i18n import tr

# 纯逻辑接口继续从包内子模块转出，供测试与 .wf 内置函数使用；
# 这里不转出 .dialog（Qt）。
from .matching import MatchResult, match, normalize, pick_answer, similarity
from .models import (
    AUTO_PLOT_DIR,
    DEFAULT_THRESHOLD,
    StoryDoc,
    StoryEntry,
    delete_entry,
    delete_group,
    list_groups,
    load_all,
    load_group,
    save_group,
    set_group_threshold,
    upsert_entry,
)

__all__ = [
    "AUTO_PLOT_DIR",
    "DEFAULT_THRESHOLD",
    "MatchResult",
    "StoryDoc",
    "StoryEntry",
    "delete_entry",
    "delete_group",
    "list_groups",
    "load_all",
    "load_group",
    "match",
    "normalize",
    "pick_answer",
    "save_group",
    "set_group_threshold",
    "similarity",
    "upsert_entry",
]


def _migrate_story_library() -> None:
    """把剧情文案库从旧的 ``tools/auto_plot`` 搬进插件目录（幂等，只做一次）。"""
    from .migration import migrate_story_library

    migrate_story_library()


def _build_menu(host, menubar):
    from .menu import build_menu

    build_menu(host, menubar)


hooks = AppHooks(
    id="yysls_story",
    name=tr("燕云剧情"),
    # 必须在 yysls 之后加载：菜单要注入燕云插件创建的「燕云」菜单。
    # 单独 `-reg yysls_story` 或顺序颠倒都会在注册期明确报错。
    requires_app_ids=("yysls",),
    menu_builders=[_build_menu],
    # 内置函数 story_options / story_pick / story_learn（导入即注册）
    builtin_modules=["lvjiang.apps.yysls_story.builtins"],
    # 剧情文案库的 content_version 归属（导入即 register_versioned_dir）
    config_policy_modules=["lvjiang.apps.yysls_story.config_policy"],
    # 注册后、装配其余扩展前的一次性数据整理：把剧情文案库从旧的
    # tools/auto_plot 搬进插件目录（搬完源消失，不会二次迁移）。
    startup_callbacks=[_migrate_story_library],
)
