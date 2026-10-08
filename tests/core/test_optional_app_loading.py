"""Optional plugins must stay opt-in and need no public registry entry."""

import sys
from types import ModuleType

import pytest

from lvjiang.apps import load_app, register_hooks
from lvjiang.apps.base import AppHooks


def test_uninstalled_optional_app_has_clear_error():
    with pytest.raises(KeyError, match="未安装"):
        load_app("missing_optional_app")


def test_installed_optional_app_registers_by_module_name(monkeypatch):
    module = ModuleType("lvjiang.apps.optional_demo")
    module.hooks = AppHooks(id="optional_demo")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    registry = {}
    register_hooks(load_app("optional_demo"), registry)

    assert registry["app_ids"] == ["optional_demo"]


def test_builtin_app_does_not_include_optional_gather():
    hooks = load_app("yysls")
    assert "auto_gather" not in hooks.workflow_implementations
    assert [name for name, _ in hooks.left_tab_builders] == ["调律"]


def test_required_app_is_checked_before_any_registration():
    callbacks = []
    hooks = AppHooks(
        id="extension",
        requires_app_ids=("base",),
        startup_callbacks=[lambda: callbacks.append("started")],
    )
    registry = {}

    with pytest.raises(RuntimeError, match="base"):
        register_hooks(hooks, registry)

    assert registry == {}
    assert callbacks == []

    register_hooks(AppHooks(id="base"), registry)
    register_hooks(hooks, registry)
    assert registry["app_ids"] == ["base", "extension"]
    assert callbacks == ["started"]
