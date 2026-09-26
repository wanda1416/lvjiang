"""连接方案管理员权限选项应与其余方案字段一起保存。"""
from __future__ import annotations

from types import SimpleNamespace

from lvjiang.core.config.plans import PLAN_MODE_WINDOW, Plan
from lvjiang.ui.settings_dialog import SettingsDialog


class _Combo:
    def __init__(self, text: str = "", data: str = "") -> None:
        self._text = text
        self._data = data

    def currentText(self) -> str:  # noqa: N802 - Qt API shape
        return self._text

    def currentData(self):  # noqa: N802 - Qt API shape
        return self._data


class _Check:
    def __init__(self, checked: bool) -> None:
        self._checked = checked

    def isChecked(self) -> bool:  # noqa: N802 - Qt API shape
        return self._checked


def test_plan_form_writes_admin_requirement():
    form = SimpleNamespace(
        _plan_space_combo=_Combo("端游"),
        _plan_env_combo=_Combo(data="desktop"),
        _plan_layout_combo=_Combo(data="desktop"),
        _plan_mode_window=_Check(True),
        _plan_mode_adb=_Check(False),
        _plan_requires_admin=_Check(True),
        _plan_distribute=_Check(False),
    )
    plan = Plan.create("端游")

    changed = SettingsDialog._write_form_into_plan(form, plan)

    assert changed
    assert plan.requires_admin is True
    assert plan.modes == [PLAN_MODE_WINDOW]
