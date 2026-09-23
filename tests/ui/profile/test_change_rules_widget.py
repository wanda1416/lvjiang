"""数据模型变动规则编辑器的紧凑布局回归测试。"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDoubleSpinBox, QPushButton, QVBoxLayout

import lvjiang.core.profile as profile_core
from lvjiang.core.profile.models import StepDef, SyncTargetDef
from lvjiang.ui.profile.settings_dialog import (
    _ChangeRulesWidget,
    _ChangeScriptFileField,
    _SyncTargetsWidget,
    _TagInputWidget,
)


@pytest.mark.parametrize("dev_mode", [True, False])
def test_change_script_picker_starts_in_mode_specific_workflow_root(
    qtbot, monkeypatch, tmp_path, dev_mode
):
    from lvjiang.core import config
    from lvjiang.ui.profile import settings_dialog

    system_dir = tmp_path / "system"
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"
    default_root = (system_dir if dev_mode else local_dir) / "workflows"
    selected = default_root / "profile" / "changed.wf"
    selected.parent.mkdir(parents=True)
    selected.write_text("log \"ok\"\n", encoding="utf-8")
    requested: list[str] = []

    resolver = SimpleNamespace(
        system_dir=system_dir,
        local_dir=local_dir,
        remote_dir=remote_dir,
        write_dir=lambda rel: requested.append(rel) or default_root,
    )
    monkeypatch.setattr(config, "get_resolver", lambda: resolver)
    dialog_args: list[tuple] = []

    def choose_file(*args):
        dialog_args.append(args)
        return str(selected), ""

    monkeypatch.setattr(settings_dialog.QFileDialog, "getOpenFileName", choose_file)
    field = _ChangeScriptFileField()
    qtbot.addWidget(field)

    field._select_file()

    assert requested == ["workflows"]
    assert dialog_args[0][2] == str(default_root)
    assert field.text() == "profile/changed.wf"
    assert field._input.isReadOnly()


@pytest.mark.parametrize(
    ("content", "message_method"),
    [("log \"ok\"\n", "information"), ("if\n", "warning")],
)
def test_change_script_validation_only_parses_syntax(
    qtbot, monkeypatch, tmp_path, content, message_method
):
    from lvjiang.ui.profile import settings_dialog
    from lvjiang.workflows import discovery

    workflow = tmp_path / "changed.wf"
    workflow.write_text(content, encoding="utf-8")
    monkeypatch.setattr(
        discovery, "resolve_workflow_path", lambda _script: (workflow, "changed.wf")
    )
    shown: list[str] = []
    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "information",
        lambda *_args: shown.append("information"),
    )
    monkeypatch.setattr(
        settings_dialog.QMessageBox,
        "warning",
        lambda *_args: shown.append("warning"),
    )
    field = _ChangeScriptFileField("changed.wf")
    qtbot.addWidget(field)

    field._validate_file()

    assert shown == [message_method]


@pytest.mark.parametrize("term_kind", ["来源", "用途"])
def test_enter_adds_term_without_removing_existing_term(qtbot, term_kind):
    dialog = QDialog()
    qtbot.addWidget(dialog)
    layout = QVBoxLayout(dialog)
    old_terms = [f"旧{term_kind}一", f"旧{term_kind}二"]
    new_term = f"新{term_kind}"
    widget = _TagInputWidget(old_terms)
    layout.addWidget(widget)
    accepted = []
    confirm = QPushButton("确定")
    confirm.setDefault(True)
    confirm.clicked.connect(lambda: accepted.append(True))
    confirm.clicked.connect(dialog.accept)
    layout.addWidget(confirm)
    dialog.show()

    widget._input.setText(new_term)
    qtbot.keyClick(widget._input, Qt.Key.Key_Return)

    assert widget.tags() == [*old_terms, new_term]
    assert accepted == []
    assert dialog.isVisible()


def test_nested_editor_buttons_never_capture_enter(qtbot, monkeypatch):
    monkeypatch.setattr(
        profile_core,
        "get_profile_config",
        lambda: SimpleNamespace(get_keys_by_model=lambda _model: []),
    )
    sync_targets = _SyncTargetsWidget()
    sync_targets.add_row()
    change_rules = _ChangeRulesWidget([])
    change_rules.add_row(change_rules._KIND_SOURCE)
    qtbot.addWidget(sync_targets)
    qtbot.addWidget(change_rules)

    assert all(not button.autoDefault() for button in sync_targets.findChildren(QPushButton))
    assert all(not button.autoDefault() for button in change_rules.findChildren(QPushButton))


def test_change_rule_rows_use_compact_amount_editor(qtbot):
    widget = _ChangeRulesWidget([
        StepDef(value=-1, source="和鸣抽奖"),
        StepDef(value=-5, source="和鸣抽奖"),
    ])
    qtbot.addWidget(widget)
    widget.resize(800, 300)
    widget.show()
    qtbot.waitExposed(widget)

    amount_editor = widget._table.cellWidget(0, 2)
    assert amount_editor is not None
    assert amount_editor.height() == 36
    assert widget._table.rowHeight(0) <= 38


def test_sync_ratio_accepts_large_negative_value(qtbot, monkeypatch):
    monkeypatch.setattr(
        profile_core,
        "get_profile_config",
        lambda: SimpleNamespace(get_keys_by_model=lambda _model: []),
    )
    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    widget.add_row(SyncTargetDef(key="stock:target", ratio=-3000))

    ratio_spin = widget._table.cellWidget(0, 1)
    assert isinstance(ratio_spin, QDoubleSpinBox)
    assert ratio_spin.value() == -3000
    assert ratio_spin.minimum() == float("-inf")
    assert ratio_spin.maximum() == float("inf")
