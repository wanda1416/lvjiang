"""内置函数面板按加载器登记的 app_id 显示归属，不解析模块路径。"""
from lvjiang.workflows.builtins._registry import FunctionMeta


def test_panel_source_label_uses_recorded_app_id():
    from lvjiang.ui.scripts.builtin_panel import BuiltinFunctionPanel

    label = BuiltinFunctionPanel._source_label
    assert label(FunctionMeta("a", "g", "s", module="any.layout.equipment", app_id="demo")).endswith(
        "demo/equipment")
    assert "demo" not in label(FunctionMeta("a", "g", "s", module="x.apps.demo.workflows.builtins.m"))
    assert label(FunctionMeta("a", "g", "s")) == "内置"
