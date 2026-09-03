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
