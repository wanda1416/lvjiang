"""目标环境 key 撞保留名（packages）时必须拦截，不能写进 app.yaml。

``screenshots/packages/`` 是独立任务包的截图目录，用户自建环境/布局叫这个名字
只会造成命名空间混淆，所以在收集阶段就挡住，而不是等到落盘。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox

from lvjiang.ui.settings.dialog import SettingsDialog


def test_reserved_env_key_is_rejected_and_normal_key_still_works(qtbot, monkeypatch):
    dialog = SettingsDialog(None)
    qtbot.addWidget(dialog)

    entry = dialog._add_env_row()
    entry["key"].setText("packages")

    warnings: list[str] = []

    def fake_warning(_parent, _title, text, *args, **kwargs):
        warnings.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr("lvjiang.ui.settings.dialog.QMessageBox.warning", fake_warning)

    assert dialog._collect_envs() is None
    assert warnings and "保留名" in warnings[0]

    # 换一个不撞的名字照常收集，规则不外扩（前两行是随包自带的 android/desktop）
    entry["key"].setText("my_pc")
    entry["name"].setText("我的电脑")
    envs = dialog._collect_envs()
    assert envs is not None
    assert envs[-1] == {"key": "my_pc", "name": "我的电脑"}
