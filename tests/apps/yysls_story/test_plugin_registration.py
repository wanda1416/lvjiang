"""yysls_story 作为内置插件的注册声明：随包发现、依赖顺序与失败边界。"""

from __future__ import annotations

import pytest

from lvjiang.core.plugins import (
    builtin_app_ids,
    discover_app_ids,
    load_app,
    register_hooks,
    resolve_app_specs,
)


def test_yysls_story_is_a_discovered_builtin_app():
    assert "yysls_story" in builtin_app_ids()
    assert "yysls_story" in discover_app_ids()


def test_story_requires_yysls_and_rejects_standalone_registration():
    """单独加载 yysls_story 必须在注册期报错，而不是进入半工作状态。"""
    hooks = load_app("yysls_story")
    assert hooks.requires_app_ids == ("yysls",)
    with pytest.raises(RuntimeError, match="yysls"):
        register_hooks(hooks, registry={})


def test_story_registers_after_yysls():
    """依赖满足时正常注册；菜单/builtin 声明齐备。"""
    registry: dict = {}
    register_hooks(load_app("yysls"), registry)
    hooks = load_app("yysls_story")
    register_hooks(hooks, registry)
    assert registry["app_ids"] == ["yysls", "yysls_story"]
    assert "lvjiang.apps.yysls_story.builtins" in hooks.builtin_modules
    assert "lvjiang.apps.yysls_story.config_policy" in hooks.config_policy_modules
    assert hooks.startup_callbacks


def test_discovery_order_puts_yysls_before_story():
    """默认扫描（--all-plugins / dev --all）的顺序天然满足依赖：yysls 在前。"""
    ordered = resolve_app_specs((), all_plugins=True)
    assert ordered.index("yysls") < ordered.index("yysls_story")
