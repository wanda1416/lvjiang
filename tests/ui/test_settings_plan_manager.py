"""配置管理 →「方案设置」Tab 的方案管理块。"""

from types import SimpleNamespace

import pytest

from lvjiang.core.config.models import UserConfig
from lvjiang.core.config.plans import (
    PLAN_MODE_ADB,
    PLAN_MODE_WINDOW,
    Plan,
    load_plans,
    save_plans,
)
from lvjiang.ui.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_settings", lambda _values: None)
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_app_config", lambda *_args: None)
    monkeypatch.setattr(
        SettingsDialog, "_available_spaces", lambda _self: ["手游", "端游"])
    monkeypatch.setattr(
        SettingsDialog, "_available_layouts",
        lambda _self: ["默认布局", "桌面布局"])
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    dlg._collect_custom = lambda: {}
    dlg._collect_envs = lambda: []
    return dlg


def test_tab_is_named_plan_settings(dialog):
    labels = [dialog._tabs.tabText(i)
              for i in range(dialog._tabs.count())]

    assert "方案设置" in labels
    assert "系统参数" not in labels


def test_new_plan_defaults_to_both_modes(dialog):
    dialog._on_new_plan()

    assert dialog._plan_mode_window.isChecked()
    assert dialog._plan_mode_adb.isChecked()
    assert dialog._plan_list.count() == 1


def test_new_plan_stores_what_the_form_shows(dialog):
    """表单是在 blockSignals 里填的，不回写的话方案三项全是空串——
    保存后主界面选中它，三个下拉框会纹丝不动。"""
    dialog._on_new_plan()

    plan = dialog._collect_plans()[0]
    assert plan.space == dialog._plan_space_combo.currentText() == "手游"
    assert plan.env == dialog._plan_env_combo.currentData()
    assert plan.layout == dialog._plan_layout_combo.currentText() == "默认布局"


def test_switching_between_plans_in_the_list_keeps_each_intact(dialog):
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("端游")
    dialog._plan_space_combo.setCurrentText("端游")
    dialog._plan_layout_combo.setCurrentText("桌面布局")
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("手游")

    dialog._plan_list.setCurrentRow(0)
    assert dialog._plan_space_combo.currentText() == "端游"
    dialog._plan_list.setCurrentRow(1)
    assert dialog._plan_space_combo.currentText() == "手游"

    first, second = dialog._collect_plans()
    assert (first.name, first.space, first.layout) == ("端游", "端游", "桌面布局")
    assert (second.name, second.space, second.layout) == ("手游", "手游", "默认布局")


def test_plan_with_a_missing_layout_is_corrected_to_what_is_shown(dialog):
    """存的布局已被删除时，表单只能显示第一项；那就以显示为准，别再留个死值。"""
    from lvjiang.core.config.plans import Plan
    dialog._plans = [Plan.create("旧方案", space="手游", env="android",
                                 layout="早就删掉的布局",
                                 modes=[PLAN_MODE_WINDOW])]
    dialog._refresh_plan_list()

    assert dialog._plan_layout_combo.currentText() == "默认布局"
    assert dialog._collect_plans()[0].layout == "默认布局"


def test_editing_fields_writes_back_to_the_selected_plan(dialog):
    dialog._on_new_plan()

    dialog._plan_name_edit.setText("端游")
    dialog._plan_space_combo.setCurrentText("端游")
    dialog._plan_layout_combo.setCurrentText("桌面布局")
    dialog._plan_mode_adb.setChecked(False)

    plan = dialog._collect_plans()[0]
    assert plan.name == "端游"
    assert plan.space == "端游"
    assert plan.layout == "桌面布局"
    assert plan.modes == [PLAN_MODE_WINDOW]
    assert dialog._plan_list.item(0).text() == "端游"


def test_save_persists_plans_to_the_session_node(dialog):
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("端游")
    dialog._plan_mode_adb.setChecked(False)

    dialog._on_save()

    stored = load_plans()
    assert [p.name for p in stored] == ["端游"]
    assert stored[0].modes == [PLAN_MODE_WINDOW]


def test_plan_built_in_the_dialog_actually_drives_the_main_selectors(
        dialog, qtbot):
    """端到端：对话框里建的方案，主界面选中后三个下拉框必须跟着变。

    这正是「切换方案三个框没跟着变」那个 bug 的现场。
    """
    from lvjiang.ui.main.run_control import RunControlMixin
    from lvjiang.ui.main.window import _ContextComboBox

    # 关键：图库和环境保持新建时显示的默认值不动——用户不会去点一个
    # 已经显示对了的下拉框，而恰恰是没被点过的那几项当初存成了空串。
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("手游")
    dialog._on_save()

    class _Host(RunControlMixin):
        def __init__(self):
            self.plan_combo = _ContextComboBox()
            self.reference_space_combo = _ContextComboBox()
            self._env_combo = _ContextComboBox()
            self.layout_combo = _ContextComboBox()
            self.reference_space_combo.addItems(["手游", "端游"])
            self._env_combo.addItem("安卓", "android")
            self._env_combo.addItem("桌面", "desktop")
            self.layout_combo.addItems(["默认布局", "桌面布局"])
            self.log_text = SimpleNamespace(append=lambda _text: None)

        def _refresh_run_button(self):
            pass

    host = _Host()
    for combo in (host.plan_combo, host.reference_space_combo,
                  host._env_combo, host.layout_combo):
        qtbot.addWidget(combo)
    host._refresh_plan_combo()
    # 主界面先停在另一套组合上，方案没生效的话这三项不会动
    host.reference_space_combo.setCurrentText("端游")
    host._env_combo.setCurrentIndex(host._env_combo.findData("desktop"))
    host.layout_combo.setCurrentText("桌面布局")

    host.plan_combo.setCurrentText("手游")
    host._on_plan_changed(host.plan_combo.currentIndex())

    assert host.reference_space_combo.currentText() == "手游"
    assert host._env_combo.currentData() == "android"
    assert host.layout_combo.currentText() == "默认布局"


def test_save_emits_so_the_main_window_can_refresh(dialog):
    fired = []
    dialog.plans_saved.connect(lambda: fired.append(True))
    dialog._on_new_plan()

    dialog._on_save()

    assert fired == [True]


def test_save_is_refused_when_a_plan_has_no_mode(dialog, monkeypatch):
    warned = []
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]))
    dialog._on_new_plan()
    dialog._plan_mode_window.setChecked(False)
    dialog._plan_mode_adb.setChecked(False)

    dialog._on_save()

    assert warned and "至少" in warned[-1]
    assert load_plans() == []


def test_save_is_refused_when_a_plan_name_is_blank(dialog, monkeypatch):
    warned = []
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.QMessageBox.warning",
        lambda *args, **kwargs: warned.append(args[-1]))
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("   ")

    dialog._on_save()

    assert warned
    assert load_plans() == []


def test_existing_plans_are_loaded_into_the_list(qtbot, monkeypatch):
    save_plans([Plan.create("端游", space="端游", env="desktop",
                            layout="桌面布局", modes=[PLAN_MODE_WINDOW])])
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        SettingsDialog, "_available_spaces", lambda _self: ["手游", "端游"])
    monkeypatch.setattr(
        SettingsDialog, "_available_layouts",
        lambda _self: ["默认布局", "桌面布局"])

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)

    assert dlg._plan_list.item(0).text() == "端游"
    assert dlg._plan_name_edit.text() == "端游"
    assert dlg._plan_layout_combo.currentText() == "桌面布局"
    assert dlg._plan_mode_window.isChecked()
    assert not dlg._plan_mode_adb.isChecked()


def test_legacy_empty_plan_can_open_settings_and_is_marked_dirty(
        qtbot, monkeypatch):
    """旧版方案三项为空时，初始化归一化发生在保存按钮创建之前。"""
    save_plans([Plan.create(
        "旧空方案",
        modes=[PLAN_MODE_WINDOW, PLAN_MODE_ADB],
    )])
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        SettingsDialog, "_available_spaces", lambda _self: ["手游", "端游"])
    monkeypatch.setattr(
        SettingsDialog, "_available_layouts",
        lambda _self: ["默认布局", "桌面布局"])

    dlg = SettingsDialog()
    qtbot.addWidget(dlg)

    plan = dlg._collect_plans()[0]
    assert (plan.space, plan.env, plan.layout) == (
        "手游",
        dlg._plan_env_combo.currentData(),
        "默认布局",
    )
    assert dlg._save_btn.isEnabled()


def test_delete_removes_the_selected_plan(dialog, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    dialog._on_new_plan()
    dialog._plan_name_edit.setText("要删的")

    dialog._on_delete_plan()

    assert dialog._collect_plans() == []
    assert dialog._plan_list.count() == 0
    # 没有方案时右侧表单整体禁用，避免编辑一个不存在的东西
    assert not dialog._plan_name_edit.isEnabled()


def test_delete_can_be_cancelled(dialog, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.No)
    dialog._on_new_plan()

    dialog._on_delete_plan()

    assert len(dialog._collect_plans()) == 1


def test_new_from_current_copies_the_main_window_context(qtbot, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        SettingsDialog, "_available_spaces", lambda _self: ["手游", "端游"])
    monkeypatch.setattr(
        SettingsDialog, "_available_layouts",
        lambda _self: ["默认布局", "桌面布局"])
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    monkeypatch.setattr(dlg, "parent", lambda: SimpleNamespace(
        reference_space_combo=SimpleNamespace(currentText=lambda: "端游"),
        _env_combo=SimpleNamespace(currentData=lambda: "desktop"),
        layout_combo=SimpleNamespace(currentText=lambda: "桌面布局"),
        _backend=PLAN_MODE_WINDOW,
    ))

    dlg._on_new_plan_from_current()

    plan = dlg._collect_plans()[-1]
    assert plan.space == "端游"
    assert plan.env == "desktop"
    assert plan.layout == "桌面布局"
    # 当前就是窗口模式，方案默认只声明它
    assert plan.modes == [PLAN_MODE_WINDOW]


def test_new_from_current_without_a_backend_allows_both(qtbot, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        SettingsDialog, "_available_spaces", lambda _self: ["手游"])
    monkeypatch.setattr(
        SettingsDialog, "_available_layouts", lambda _self: ["默认布局"])
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    monkeypatch.setattr(dlg, "parent", lambda: SimpleNamespace(_backend=None))

    dlg._on_new_plan_from_current()

    assert dlg._collect_plans()[-1].modes == [PLAN_MODE_WINDOW, PLAN_MODE_ADB]
