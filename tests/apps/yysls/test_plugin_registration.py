"""yysls 作为内置插件的注册声明：随包发现、Tab 与工作流范围。"""
from lvjiang.core.plugins import builtin_app_ids, discover_app_ids, load_app


def test_yysls_is_a_discovered_builtin_app():
    assert "yysls" in builtin_app_ids()
    assert "yysls" in discover_app_ids()


def test_builtin_app_does_not_include_optional_gather():
    hooks = load_app("yysls")
    assert "auto_gather" not in hooks.workflow_implementations
    assert [name for name, _ in hooks.left_tab_builders] == ["调律"]
