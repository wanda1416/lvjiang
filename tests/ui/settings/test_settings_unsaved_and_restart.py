"""配置管理未保存退出提醒与语言切换后立即询问原地重启。"""

from __future__ import annotations

import os
import sys

import pytest
from PyQt6.QtWidgets import QMessageBox

from lvjiang.app import build_restart_command, build_restart_env
from lvjiang.ui.settings import dialog as settings_dialog
from lvjiang.ui.settings.dialog import SettingsDialog


@pytest.fixture
def dialog(qtbot):
    widget = SettingsDialog(None)
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_clean_close_does_not_prompt(dialog, monkeypatch):
    """未做任何修改直接关闭，不应弹出未保存确认框。"""
    called = False

    def fail_if_called(_box):
        nonlocal called
        called = True
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(settings_dialog, "exec_styled_message_box", fail_if_called)
    dialog._close_btn.click()
    assert not called
    assert not dialog.isVisible()


def test_unsaved_language_change_prompts_on_close_cancel_and_discard(dialog, monkeypatch):
    """修改界面语言后未保存退出：点取消留在本页，点放弃才关闭。"""
    ja_idx = dialog._lang_combo.findData("ja_JP")
    assert ja_idx >= 0
    dialog._lang_combo.setCurrentIndex(ja_idx)
    assert dialog._has_unsaved_changes()

    prompts: list[str] = []

    def choose_cancel(box):
        prompts.append(box.text())
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(settings_dialog, "exec_styled_message_box", choose_cancel)
    dialog._close_btn.click()
    assert len(prompts) == 1
    assert dialog.isVisible()

    # Esc (reject) 同样被拦下
    dialog.reject()
    assert len(prompts) == 2
    assert dialog.isVisible()

    # 选择放弃后正常关闭且不二次弹窗
    monkeypatch.setattr(
        settings_dialog,
        "exec_styled_message_box",
        lambda _box: QMessageBox.StandardButton.Discard,
    )
    dialog._close_btn.click()
    assert not dialog.isVisible()


def test_unsaved_ai_page_change_prompts_on_close(dialog, monkeypatch):
    """AI 设置页独立保存项有未保存编辑时，关闭配置管理同样提醒。"""
    assert not dialog._has_unsaved_changes()
    dialog._ai_page.url.setText("https://example.invalid/v1")
    assert dialog._has_unsaved_changes()

    monkeypatch.setattr(
        settings_dialog,
        "exec_styled_message_box",
        lambda _box: QMessageBox.StandardButton.Cancel,
    )
    dialog.close()
    assert dialog.isVisible()

    # 测试结束前清掉脏状态以便 qtbot 干净关闭
    dialog._bypass_close_guard = True
    dialog.close()


def test_language_save_prompts_restart_immediately_and_handles_both_choices(dialog, monkeypatch):
    """保存新语言后立刻弹窗询问「立即重启 / 稍后重启」，并明确说明不重启不会撤销修改。"""
    monkeypatch.setattr("lvjiang.i18n.current_language", lambda: "zh_CN")
    monkeypatch.setattr(settings_dialog, "save_settings", lambda *_a, **_kw: None)
    monkeypatch.setattr(settings_dialog, "save_app_config", lambda *_a, **_kw: None)
    monkeypatch.setattr("lvjiang.core.config.plans.save_plans", lambda *_a, **_kw: None)

    restarted: list[bool] = []
    dialog.restart_requested.connect(lambda: restarted.append(True))

    captured: dict[str, object] = {}

    def pick_later(self_box):
        captured["title"] = self_box.windowTitle()
        captured["text"] = self_box.text()
        captured["buttons"] = [b.text() for b in self_box.buttons()]
        later = next(b for b in self_box.buttons() if b.text() == "稍后重启")
        monkeypatch.setattr(self_box, "clickedButton", lambda b=later: b)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", pick_later)
    ja_idx = dialog._lang_combo.findData("ja_JP")
    dialog._lang_combo.setCurrentIndex(ja_idx)
    assert dialog._on_save() is True
    assert captured["buttons"] == ["立即重启", "稍后重启"]
    assert "不会撤销已保存的修改" in str(captured["text"])
    assert restarted == []
    assert dialog.isVisible()
    assert not dialog._restart_hint.isHidden()

    # 再次改为英语并点击「立即重启」→ 触发 restart_requested 并关闭对话框
    def pick_restart(self_box):
        btn = next(b for b in self_box.buttons() if b.text() == "立即重启")
        monkeypatch.setattr(self_box, "clickedButton", lambda b=btn: b)
        return 0

    monkeypatch.setattr(QMessageBox, "exec", pick_restart)
    en_idx = dialog._lang_combo.findData("en_US")
    dialog._lang_combo.setCurrentIndex(en_idx)
    assert dialog._on_save() is True
    assert restarted == [True]
    assert not dialog.isVisible()


def test_restart_command_and_env_cleanup(monkeypatch):
    """原地重启命令区分 PyInstaller 发行版与源码模式，并清理 _MEIPASS2/LD_LIBRARY_PATH。"""
    monkeypatch.setattr(sys, "argv", ["lvjiang", "--log-level", "DEBUG"])
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert build_restart_command() == [sys.executable, "-m", "lvjiang", "--log-level", "DEBUG"]

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert build_restart_command() == [sys.executable, "--log-level", "DEBUG"]

    monkeypatch.setitem(os.environ, "_MEIPASS2", "/tmp/_MEI123")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH", "/tmp/_MEI123:/usr/lib")
    monkeypatch.setitem(os.environ, "LD_LIBRARY_PATH_ORIG", "/usr/lib")
    cleaned = build_restart_env()
    assert "_MEIPASS2" not in cleaned
    assert "LD_LIBRARY_PATH_ORIG" not in cleaned
    assert cleaned["LD_LIBRARY_PATH"] == "/usr/lib"
