"""内置函数归属由插件加载器登记，界面不靠模块路径猜插件。"""
import sys
import textwrap

from lvjiang.apps import register_hooks
from lvjiang.apps.base import AppHooks
from lvjiang.workflows.builtins import _registry
from lvjiang.workflows.builtins._registry import FunctionMeta, describe_function


def test_register_hooks_records_app_id_for_declared_builtin_modules(tmp_path, monkeypatch):
    package = tmp_path / "fake_plugin_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "funcs.py").write_text(textwrap.dedent("""
        from lvjiang.workflows.builtins._registry import builtin_func

        @builtin_func("fake_plugin_probe", group="测试", summary="探针")
        def _probe():
            return 1
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(_registry, "_FUNCTION_REGISTRY", dict(_registry._FUNCTION_REGISTRY))
    try:
        register_hooks(
            AppHooks(id="fakeapp", name="假插件", builtin_modules=["fake_plugin_pkg.funcs"]),
            registry={},
        )
        meta = describe_function("fake_plugin_probe")
        assert meta is not None and meta.app_id == "fakeapp"
    finally:
        for name in ("fake_plugin_pkg.funcs", "fake_plugin_pkg"):
            sys.modules.pop(name, None)


def test_assign_app_covers_modules_imported_before_loader():
    fn = lambda: None  # noqa: E731
    fn._meta = FunctionMeta(name="x", group="g", summary="s", module="pkg.mod.sub")
    registry = {"x": fn}
    original = _registry._FUNCTION_REGISTRY
    _registry._FUNCTION_REGISTRY = registry
    try:
        assert _registry.assign_app("demo", ["pkg.mod"]) == 1
        assert fn._meta.app_id == "demo"
        # 已登记的不会被后来者改写
        assert _registry.assign_app("other", ["pkg"]) == 0
    finally:
        _registry._FUNCTION_REGISTRY = original

