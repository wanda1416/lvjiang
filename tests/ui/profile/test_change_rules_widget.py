"""数据模型变动规则编辑器的紧凑布局回归测试。"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

import lvjiang.core.profile as profile_core
from lvjiang.core.profile.models import StepDef, SyncTargetDef, parse_steps
from lvjiang.ui.profile.settings_dialog import (
    ProfileDefinitionDialog,
    _ChangeRulesWidget,
    _ChangeScriptFileField,
    _SyncTargetsWidget,
)
from lvjiang.ui.tag_input import TagInputWidget


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


def test_key_editor_is_wider_and_editable_inputs_have_no_visual_noise(
    qtbot, monkeypatch,
):
    captured: list[QDialog] = []
    monkeypatch.setattr(
        QDialog,
        "exec",
        lambda dialog: captured.append(dialog) or 0,
    )
    parent = QDialog()
    qtbot.addWidget(parent)

    result = ProfileDefinitionDialog.open_key_editor(
        parent, "quota", None, set()
    )

    assert result is None
    assert len(captured) == 1
    dialog = captured[0]
    assert dialog.minimumWidth() == 806
    editable_inputs = [
        item for item in dialog.findChildren(QLineEdit)
        if not item.isReadOnly()
    ]
    assert editable_inputs
    assert all(not item.placeholderText() for item in editable_inputs)


@pytest.mark.parametrize("term_kind", ["来源", "用途"])
def test_enter_adds_term_without_removing_existing_term(qtbot, term_kind):
    dialog = QDialog()
    qtbot.addWidget(dialog)
    layout = QVBoxLayout(dialog)
    old_terms = [f"旧{term_kind}一", f"旧{term_kind}二"]
    new_term = f"新{term_kind}"
    widget = TagInputWidget(old_terms)
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
    assert widget._table.rowCount() == 2


def test_change_rules_do_not_merge_existing_rows_automatically(qtbot):
    widget = _ChangeRulesWidget([
        StepDef(value=-1, source="其他任务"),
        StepDef(value=-5, source="其他任务"),
    ])
    qtbot.addWidget(widget)

    assert widget._table.rowCount() == 2
    assert widget.validation_error() == ""


def test_change_rules_preserve_explicit_multi_value_row(qtbot):
    steps = parse_steps([{
        "values": [-1, -5],
        "sources": ["其他任务", "妙妙喵"],
    }])
    widget = _ChangeRulesWidget(steps)
    qtbot.addWidget(widget)

    assert widget._table.rowCount() == 1
    term_editor = widget._table.cellWidget(0, 1)
    amount_editor = widget._table.cellWidget(0, 2)
    assert isinstance(term_editor, TagInputWidget)
    assert isinstance(amount_editor, TagInputWidget)
    assert term_editor.tags() == ["其他任务", "妙妙喵"]
    assert amount_editor.tags() == ["1", "5"]
    saved = widget.get_steps()
    assert saved == [
        StepDef(value=-1, source="其他任务"),
        StepDef(value=-5, source="其他任务"),
        StepDef(value=-1, source="妙妙喵"),
        StepDef(value=-5, source="妙妙喵"),
    ]
    assert len({step._group_id for step in saved}) == 1


def test_change_rule_columns_fix_type_and_split_remaining_space_6_to_4(qtbot):
    widget = _ChangeRulesWidget([StepDef(value=1, source="来源")])
    qtbot.addWidget(widget)
    widget.resize(800, widget.sizeHint().height())
    widget.show()
    qtbot.waitExposed(widget)

    assert widget._table.columnWidth(0) == widget._table._TYPE_WIDTH
    flexible_width = (
        widget._table.columnWidth(1) + widget._table.columnWidth(2)
    )
    assert widget._table.columnWidth(1) == flexible_width * 6 // 10


def test_rule_tables_show_actual_rows_plus_one(qtbot, monkeypatch):
    monkeypatch.setattr(
        profile_core,
        "get_profile_config",
        lambda: SimpleNamespace(get_keys_by_model=lambda _model: []),
    )
    sync_targets = _SyncTargetsWidget()
    sync_targets.add_row()
    change_rules = _ChangeRulesWidget([
        StepDef(value=1, source="A"),
        StepDef(value=2, source="B"),
    ])
    qtbot.addWidget(sync_targets)
    qtbot.addWidget(change_rules)

    for table in (sync_targets._table, change_rules._table):
        header = table.horizontalHeader()
        vertical = table.verticalHeader()
        expected = (
            header.sizeHint().height()
            + sum(table.rowHeight(row) for row in range(table.rowCount()))
            + vertical.defaultSectionSize()
            + table.frameWidth() * 2
        )
        assert table.height() == expected


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
