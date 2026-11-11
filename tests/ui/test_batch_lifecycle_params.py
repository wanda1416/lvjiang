"""生命周期参数面板：类型往返与 require 显隐，值只属于本次运行草稿。"""
from lvjiang.core.batch_config import BatchWorkflows
from lvjiang.ui.batch import lifecycle_params as module
from lvjiang.ui.batch.lifecycle_params import LifecycleParamsPanel


def test_panel_round_trips_typed_values_and_reveals_dependent_fields(
    qtbot, monkeypatch,
):
    definitions = {
        "prepare_item": [
            {"name": "count", "label": "滚动次数", "type": "number",
             "default": 5, "min": 1, "max": 10},
            {"name": "skip", "label": "跳过等待", "type": "bool",
             "default": False},
            {"name": "wait", "label": "等待时间", "type": "number",
             "default": 60, "require": "$skip"},
        ],
    }
    monkeypatch.setattr(
        module, "lifecycle_parameter_definitions", lambda _wf: definitions)
    panel = LifecycleParamsPanel()
    qtbot.addWidget(panel)
    panel.set_workflows(
        BatchWorkflows(), {"prepare_item": {"count": 7}})

    assert panel.has_content()
    assert panel.values() == {
        "prepare_item": {"count": 7, "skip": False, "wait": 60}}
    wait = panel._widgets[("prepare_item", "wait")]
    assert wait.isHidden(), "依赖未满足的字段不展示"

    changes = []
    panel.changed.connect(lambda: changes.append(True))
    panel._widgets[("prepare_item", "skip")].setChecked(True)

    assert not wait.isHidden()
    assert changes
    assert panel.values()["prepare_item"]["skip"] is True
