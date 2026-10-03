"""配置编辑必须激活保存，并在重新打开时保留。"""

import pytest
from PyQt6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QRadioButton,
    QSpinBox,
)

from lvjiang.core.config import resolver as resolver_module
from lvjiang.ui.settings_dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot, tmp_path, monkeypatch):
    resolver = resolver_module.ConfigResolver(
        system_dir=resolver_module.SYSTEM_CONFIG_DIR,
        local_dir=tmp_path / "local",
        dev_mode=False,
    )
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    # 先保存初始表单，排除其他配置归一化造成的脏状态。
    dialog._on_save()
    assert not dialog._save_btn.isEnabled()
    return dialog


def test_clamp_toggle_enables_save_and_round_trips(qtbot, dialog):
    initial = dialog._clamp_oob_cb.isChecked()

    for expected in (not initial, initial):
        dialog._clamp_oob_cb.click()
        assert dialog._clamp_oob_cb.isChecked() == expected
        assert dialog._save_btn.isEnabled()
        dialog._save_btn.click()
        assert not dialog._save_btn.isEnabled()
        assert not dialog._dirty
        reopened = SettingsDialog()
        qtbot.addWidget(reopened)
        assert reopened._clamp_oob_cb.isChecked() == expected
        reopened.close()


@pytest.mark.parametrize("field", ["key", "name"])
def test_existing_environment_edits_can_be_saved(qtbot, dialog, field):
    row = dialog._env_rows[0]
    expected = row[field].text() + "_test"
    row[field].setText(expected)
    assert dialog._save_btn.isEnabled()
    dialog._save_btn.click()
    assert not dialog._save_btn.isEnabled()
    reopened = SettingsDialog()
    qtbot.addWidget(reopened)
    assert reopened._env_rows[0][field].text() == expected


def test_other_global_save_controls_mark_dirty(dialog):
    controls = [
        dialog._lang_combo, dialog._title_edit,
        dialog._capture_stream_radio, dialog._capture_static_radio,
        dialog._android_input_adb_radio, dialog._android_input_agent_radio,
        dialog._input_bg_radio, dialog._input_fg_radio,
        dialog._desktop_capture_fg_radio, dialog._desktop_capture_bg_radio,
        dialog._offset_spin, dialog._jitter_spin, dialog._clamp_oob_cb,
        dialog._overview_font_spin, dialog._user_info_font_spin,
        *dialog._hotkey_combos.values(),
        *(spin for pair in dialog._range_spins.values() for spin in pair),
        *(row[key] for row in dialog._android_app_rows
          for key in ("name", "package", "activity", "orientation", "executable", "window_title")),
        *(row[key] for row in dialog._custom_rows for key in ("key", "label", "lo", "hi")),
        *(row[key] for row in dialog._env_rows for key in ("key", "name")),
    ]
    for control in controls:
        if isinstance(control, QRadioButton) and control.isChecked():
            other = next(button for button in control.group().buttons() if button is not control)
            other.setChecked(True)
        dialog._dirty = False
        dialog._save_btn.setEnabled(False)
        if isinstance(control, QAbstractButton):
            control.setChecked(not control.isChecked())
        elif isinstance(control, QComboBox):
            assert control.count() > 1
            control.setCurrentIndex((control.currentIndex() + 1) % control.count())
        elif isinstance(control, QLineEdit):
            control.setText(control.text() + "_test")
        elif isinstance(control, (QSpinBox, QDoubleSpinBox)):
            control.setValue(control.value() + control.singleStep()
                             if control.value() < control.maximum() else control.minimum())
        else:
            pytest.fail(f"未覆盖的控件类型：{type(control)}")
        assert dialog._dirty, control
        assert dialog._save_btn.isEnabled(), control
