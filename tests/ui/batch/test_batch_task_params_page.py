"""批量「任务」页：参数改动必须写回日常使用的同一份存储。"""
from PyQt6.QtWidgets import QCheckBox, QSpinBox

from lvjiang.ui.batch import task_params_page as module
from lvjiang.ui.batch.task_params_page import TaskParamsPage

_SCRIPT = {
    "id": "demo",
    "name": "示例任务",
    "parameters": [
        {"name": "count", "label": "次数", "type": "number",
         "default": 1, "min": 0, "max": 99},
        {"name": "enabled", "label": "启用", "type": "bool", "default": False},
    ],
}


def test_global_scope_writes_wf_config_immediately(qtbot, monkeypatch):
    wf_writes = []
    monkeypatch.setattr(module, "get_wf_config", lambda _sid: {"count": "3"})
    monkeypatch.setattr(
        module, "update_wf_config",
        lambda sid, patch: wf_writes.append((sid, patch)))
    page = TaskParamsPage()
    qtbot.addWidget(page)
    page.set_context([_SCRIPT], [])

    spin = page.findChild(QSpinBox, "count")
    assert spin is not None and spin.value() == 3
    spin.setValue(7)
    assert wf_writes == [("demo", {"count": "7"})]


def test_default_values_are_not_persisted(qtbot, monkeypatch):
    """数字参数默认值是 int、控件读出是 str，不能因此把默认值写进配置。"""
    wf_writes = []
    monkeypatch.setattr(module, "get_wf_config", lambda _sid: {})
    monkeypatch.setattr(
        module, "update_wf_config",
        lambda sid, patch: wf_writes.append((sid, patch)))
    page = TaskParamsPage()
    qtbot.addWidget(page)
    page.set_context([_SCRIPT], [])
    page.set_context([_SCRIPT], [])
    assert wf_writes == []


def test_user_scope_merges_only_changed_fields(qtbot, monkeypatch):
    monkeypatch.setattr(module, "get_wf_config", lambda _sid: {"count": "3"})
    monkeypatch.setattr(
        module, "get_user_workflow_params",
        lambda _user, _sid, _dir: {"enabled": True})
    user_writes = []
    monkeypatch.setattr(
        module, "set_user_workflow_params",
        lambda user, sid, params, _dir: user_writes.append(
            (user, sid, params)))
    page = TaskParamsPage()
    qtbot.addWidget(page)
    page.set_context([_SCRIPT], ["alice"])

    combo = page._scope_combo
    index = combo.findData("alice")
    assert index > 0
    combo.setCurrentIndex(index)

    check = page.findChild(QCheckBox, "enabled")
    assert check is not None and check.isChecked()
    spin = page.findChild(QSpinBox, "count")
    assert spin is not None and spin.value() == 3
    spin.setValue(5)
    # 只把改动字段合并进该用户的独立参数，其余字段继续跟随全局。
    assert user_writes == [("alice", "demo", {"enabled": True, "count": "5"})]
