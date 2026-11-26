"""Tools-only opt-in preview controls; no simulation GUI pretence or persisted enable."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QDialogButtonBox, QLabel, QMainWindow, QWidget

from lvjiang.core.task_diagnostics import TaskDiagnostics
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.task_diagnostics_dialog import TaskDiagnosticsDialog


def test_tools_menu_opens_one_modeless_diagnostic_dialog(qtbot):
    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()
    assert getattr(host, "_task_diagnostics", None) is None
    tools = next(a.menu() for a in host.menuBar().actions() if a.text() == "工具")
    action = next(a for a in tools.actions() if a.text() == "任务诊断")
    # 菜单保持四字短标签；「开发预览」的说明放在对话框正文，不塞进菜单标题。
    assert action.text() == "任务诊断"
    action.trigger()
    dialog = host._modeless_tool_windows["task_diagnostics"]
    # pytest-qt also closes registered widgets at teardown; avoid double-close
    # after the production modeless helper schedules deleteLater on close.
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    assert isinstance(dialog, TaskDiagnosticsDialog) and not dialog.isModal()
    action.trigger()
    assert host._modeless_tool_windows["task_diagnostics"] is dialog
    assert len(host._modeless_tool_windows) == 1
    assert host._close_modeless_tools()


def test_diagnostic_tabs_defaults_ephemeral_settings_and_launch_freeze(qtbot):
    service = TaskDiagnostics()
    dialog = TaskDiagnosticsDialog(service)
    qtbot.addWidget(dialog)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "诊断调试",
        "模拟运行",
    ]
    assert dialog.tabs.widget(1).findChildren(QWidget) == []
    assert {c.text() for c in dialog.findChildren(QCheckBox)} == {
        "启用开发诊断模式",
        "跟踪最新",
    }
    assert dialog.btn_dump_toggle.isCheckable()
    assert not dialog.btn_dump_toggle.isChecked()
    assert dialog.btn_dump_toggle.text() == "开始落盘"
    assert dialog.btn_dump_toggle.property("btnVariant") == "action"
    assert dialog.btn_open_dump_dir.property("btnVariant") == "neutral"
    for nav_btn in (dialog.btn_first, dialog.btn_prev, dialog.btn_next, dialog.btn_last):
        assert nav_btn.property("btnVariant") == "neutral"
        assert nav_btn.property("btnDensity") == "compact"
    close_box = dialog.findChild(QDialogButtonBox)
    assert close_box is not None
    close_btn = close_box.button(QDialogButtonBox.StandardButton.Close)
    assert close_btn is not None and close_btn.property("btnVariant") == "neutral"

    assert dialog.follow_check.isChecked()
    assert dialog.enabled_check.text() == "启用开发诊断模式"
    # 开发预览的口径写在对话框里，菜单标题只保留四字。
    assert any("开发预览" in label.text() for label in dialog.findChildren(QLabel))
    assert not dialog.enabled_check.isChecked() and service.freeze() is None
    assert dialog.frames_spin.value() == 100 and dialog.capacity_spin.value() == 50
    assert not dialog.frames_spin.isEnabled() and not dialog.capacity_spin.isEnabled()
    assert service.options.max_bytes == 50_000_000
    dialog.enabled_check.setChecked(True)
    assert dialog.frames_spin.isEnabled() and dialog.capacity_spin.isEnabled()
    frozen = service.freeze()
    dialog.frames_spin.setValue(2)
    dialog.capacity_spin.setValue(1)
    assert frozen.options.max_frames == 100 and frozen.options.max_bytes == 50_000_000
    assert service.freeze().options.max_frames == 2
    dialog.close()
    reopened = TaskDiagnosticsDialog(service)
    qtbot.addWidget(reopened)
    assert (
        reopened.enabled_check.isChecked()
    )  # Reopening isn't restarting the application.
    restarted = TaskDiagnosticsDialog(TaskDiagnostics())
    qtbot.addWidget(restarted)
    assert not restarted.enabled_check.isChecked()


def test_real_main_controls_no_longer_expose_capture_checkbox(qtbot, monkeypatch):
    """Build the real main control area, without managers, devices or unrelated tabs."""
    from types import SimpleNamespace

    from lvjiang.core.config.models import UserConfig
    from lvjiang.ui.main import window

    class Host(window.MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self._user_config = UserConfig()
            self._backend = None
            self._reference_db = SimpleNamespace(
                get_spaces=lambda: [], get_active_space=lambda: ""
            )
            self._setup_ui()

        def _apply_backend_ui(self, backend):
            pass

        def _build_capture_panel(self):
            return QWidget()

        def _build_left_tabs(self):
            pass

        def _build_right_tabs(self):
            pass

        def _restore_ui_state(self):
            pass

        def _setup_log_redirect(self):
            pass

        def _resize_execution_target_columns(self):
            pass

        def closeEvent(self, event):  # noqa: N802 - Qt
            QMainWindow.closeEvent(self, event)

    monkeypatch.setattr(window, "load_available_envs", lambda: [])
    monkeypatch.setattr(window, "load_env", lambda: "")
    host = Host()
    qtbot.addWidget(host)
    assert not hasattr(host, "chk_full_task_capture")
    assert not hasattr(host, "_task_diagnostics")
    assert all("全帧采集" not in c.text() for c in host.findChildren(QCheckBox))


def _observe_color(history, seq: int, bgr: tuple[int, int, int], source: str = "ocr") -> None:
    import numpy as np

    img = np.zeros((24, 32, 3), dtype=np.uint8)
    img[:, :] = bgr
    history.observe({"frame_seq": seq, "source": source, "captured_ns": seq * 1_000_000}, img)


def test_carousel_follow_latest_and_pinned_frame_survives_fifo_eviction(qtbot):
    """轮播支持跟踪最新与跳转旧帧；正在查看的旧帧留在临时区，即使 FIFO 淘汰也不被清除。"""
    import time

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    service.options = DiagnosticOptions(enabled=True, max_frames=2, max_bytes=10_000_000)
    history = RecentFrameHistory(service.options, {"task_run_id": "t1"})
    service._remember(history)

    dialog = TaskDiagnosticsDialog(service)
    qtbot.addWidget(dialog)

    _observe_color(history, 1, (20, 30, 220), "ocr")
    _observe_color(history, 2, (20, 220, 30), "template")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)

    dialog.refresh_frames()
    assert "#2" in dialog.meta_label.text()
    assert dialog.index_label.text() == "2 / 2"
    assert dialog._pinned_pixmap is not None and not dialog._pinned_pixmap.isNull()

    # 切到最早一帧 (#1) 并脱离跟踪模式
    dialog._jump_first()
    assert not dialog.follow_check.isChecked()
    assert "#1" in dialog.meta_label.text()
    assert dialog.index_label.text() == "1 / 2"

    # 继续写入 #3、#4，使 #1 被底层 FIFO (max_frames=2) 淘汰
    _observe_color(history, 3, (220, 30, 20), "ocr")
    _observe_color(history, 4, (200, 200, 30), "color")
    deadline = time.monotonic() + 3.0
    while history.snapshot()["evicted"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)

    dialog.refresh_frames()
    # 正在查看的 #1 仍保留在临时区，不被清除也不被跳走；上一帧按钮禁用且显示「回到最新」
    assert "#1" in dialog.meta_label.text()
    assert "临时区保留" in dialog.meta_label.text()
    assert "原缓存已淘汰" in dialog.meta_label.text()
    assert not dialog.btn_prev.isEnabled()
    assert dialog.btn_next.isEnabled()
    assert not dialog.btn_return_latest.isHidden()
    assert dialog._pinned_pixmap is not None and not dialog._pinned_pixmap.isNull()

    # 点击「回到最新」一键切回跟踪最新，立即跳到 #4
    dialog.btn_return_latest.click()
    assert dialog.follow_check.isChecked()
    assert dialog.btn_return_latest.isHidden()
    assert "#4" in dialog.meta_label.text()
    assert dialog.index_label.text() == "2 / 2"

    history.close("completed")
    dialog.close()


def test_keyboard_arrow_navigation_works_when_child_widgets_focused_and_respects_spinbox(qtbot):
    """焦点在 TabBar/按钮/滑块上时方向键能切帧且不误切 Tab；焦点在 SpinBox 时不劫持方向键。"""
    import time

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    service.options = DiagnosticOptions(enabled=True, max_frames=5, max_bytes=10_000_000)
    history = RecentFrameHistory(service.options, {"task_run_id": "kbd"})
    service._remember(history)

    dialog = TaskDiagnosticsDialog(service)
    qtbot.addWidget(dialog)
    dialog.show()

    for seq in (1, 2, 3):
        _observe_color(history, seq, (seq * 50, 120, 200), "ocr")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 3 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()
    assert "#3" in dialog.meta_label.text()

    # 焦点在 TabBar 上按 ←：应切到 #2，且不切换到「模拟运行」Tab
    tab_bar = dialog.tabs.tabBar()
    tab_bar.setFocus()
    qtbot.keyClick(tab_bar, Qt.Key.Key_Left)
    assert dialog.tabs.currentIndex() == 0
    assert "#2" in dialog.meta_label.text()

    # 焦点在普通按钮上按 Home / → / End
    dialog.btn_next.setFocus()
    qtbot.keyClick(dialog.btn_next, Qt.Key.Key_Home)
    assert "#1" in dialog.meta_label.text()
    qtbot.keyClick(dialog.btn_next, Qt.Key.Key_Right)
    assert "#2" in dialog.meta_label.text()
    qtbot.keyClick(dialog.btn_next, Qt.Key.Key_End)
    assert "#3" in dialog.meta_label.text()
    assert dialog.follow_check.isChecked()

    # 焦点在帧数 SpinBox 时按 ← 不应劫持切帧
    dialog.frames_spin.setFocus()
    qtbot.keyClick(dialog.frames_spin, Qt.Key.Key_Left)
    assert "#3" in dialog.meta_label.text()

    history.close("completed")
    dialog.close()


def test_dump_toggle_writes_existing_and_new_frames_into_unique_session_folders(qtbot, tmp_path):
    """开启落盘开关会创建独立新目录，导出当前帧+临时区帧+后续新帧；再次开启新建目录。"""
    import json
    import time

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    service.options = DiagnosticOptions(enabled=True, max_frames=2, max_bytes=10_000_000)
    history = RecentFrameHistory(service.options, {"task_run_id": "dump-run"})
    service._remember(history)

    dump_root = tmp_path / "logs" / "diagnostics"
    dialog = TaskDiagnosticsDialog(service, dump_base_dir=dump_root)
    qtbot.addWidget(dialog)

    _observe_color(history, 1, (10, 20, 240), "ocr")
    _observe_color(history, 2, (10, 240, 20), "template")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()

    # 钉住 #1 并让 #3 淘汰 #1，确保落盘会把临时区帧 #1 + 当前缓存 #2/#3 一起落盘
    dialog._jump_first()
    _observe_color(history, 3, (240, 20, 10), "color")
    deadline = time.monotonic() + 3.0
    while history.snapshot()["evicted"] < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()

    # 第一次开启落盘
    dialog.btn_dump_toggle.setChecked(True)
    assert dialog.btn_dump_toggle.text() == "停止落盘"
    assert dialog.btn_dump_toggle.property("btnVariant") == "danger"
    dir1 = dialog._dumper.session_dir
    assert dir1 is not None and dir1.parent == dump_root and dir1.is_dir()

    # 落盘期间又产生新帧 #4
    _observe_color(history, 4, (120, 120, 120), "ocr")
    deadline = time.monotonic() + 3.0
    while history.snapshot()["evicted"] < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()

    # 关闭落盘并刷盘完成
    dialog.btn_dump_toggle.setChecked(False)
    assert dialog.btn_dump_toggle.text() == "开始落盘"
    assert dialog.btn_dump_toggle.property("btnVariant") == "action"
    assert not dialog._dumper.active
    assert dialog._dumper.written_count == 4

    webp_files = sorted(dir1.glob("*.webp"))
    assert len(webp_files) == 4
    lines = (dir1 / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    seqs = [json.loads(line)["frame_seq"] for line in lines]
    assert seqs == [1, 2, 3, 4]

    # 第二次开启落盘必须生成全新的目录
    dialog.btn_dump_toggle.setChecked(True)
    dir2 = dialog._dumper.session_dir
    assert dir2 is not None and dir2 != dir1 and dir2.parent == dump_root
    dialog.btn_dump_toggle.setChecked(False)

    history.close("completed")
    dialog.close()


def test_push_dumper_captures_all_frames_without_polling_even_when_hidden(qtbot, tmp_path):
    """即使 max_frames=1 且对话框处于隐藏/不轮询状态，推送式落盘也不丢任何中间帧。"""
    import json
    import time

    from PyQt6.QtWidgets import QSizePolicy

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    dialog = TaskDiagnosticsDialog(service, dump_base_dir=tmp_path / "logs" / "diagnostics")
    qtbot.addWidget(dialog)

    # 未开启诊断且无缓存帧时，落盘开关应禁用，且预览区使用 Ignored 策略不锁死缩窗
    assert not dialog.btn_dump_toggle.isEnabled()
    assert dialog.preview_label.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Ignored
    assert dialog.preview_label.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Ignored

    dialog.enabled_check.setChecked(True)
    assert dialog.btn_dump_toggle.isEnabled()
    dialog.btn_dump_toggle.setChecked(True)
    session_dir = dialog._dumper.session_dir
    assert session_dir is not None

    # 隐藏对话框（停止 UI 轮询定时器），通过 service._emit_frame 连续高速写入 5 帧（FIFO 上限=1）
    dialog.hide()
    assert not dialog._poll_timer.isActive()
    opts = DiagnosticOptions(enabled=True, max_frames=1, max_bytes=10_000_000)
    history = RecentFrameHistory(opts, {"task_run_id": "burst"}, on_frame=service._emit_frame)
    service._remember(history)
    for seq in range(1, 5):
        _observe_color(history, seq, (seq * 40, 100, 200), "ocr")
    history.close("completed")

    deadline = time.monotonic() + 3.0
    while dialog._dumper.written_count < 4 and time.monotonic() < deadline:
        time.sleep(0.02)

    dialog.btn_dump_toggle.setChecked(False)
    lines = (session_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["frame_seq"] for line in lines] == [1, 2, 3, 4]
    dialog.close()


def test_clear_cache_button_clears_history_and_pinned_frame_before_next_dump(qtbot, tmp_path):
    """落盘行提供清空缓存按钮；清空后历史帧与临时区帧均被清除，后续开始落盘不再带入旧帧。"""
    import json
    import time

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    service.options = DiagnosticOptions(enabled=True, max_frames=2, max_bytes=10_000_000)
    history = RecentFrameHistory(
        service.options, {"task_run_id": "run-1"}, on_frame=service._emit_frame
    )
    service._remember(history)

    dump_root = tmp_path / "logs" / "diagnostics"
    dialog = TaskDiagnosticsDialog(service, dump_base_dir=dump_root)
    qtbot.addWidget(dialog)

    assert dialog.btn_clear_cache.text() == "清空缓存"
    assert dialog.btn_clear_cache.property("btnVariant") == "neutral"
    assert dialog.btn_clear_cache.property("btnDensity") == "compact"
    assert not dialog.btn_clear_cache.isEnabled()

    # 采集 3 帧并钉住被淘汰的 #1，再封口任务
    _observe_color(history, 1, (10, 20, 240), "ocr")
    _observe_color(history, 2, (10, 240, 20), "template")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()
    dialog._jump_first()
    _observe_color(history, 3, (240, 20, 10), "color")
    history.close("completed")
    dialog.refresh_frames()

    assert dialog.btn_clear_cache.isEnabled()
    assert dialog._pinned_frame is not None

    # 点击清空缓存：释放历史句柄、清空临时区预览并恢复跟踪最新
    dialog.btn_clear_cache.click()
    assert service.latest is None
    assert dialog._pinned_frame is None
    assert dialog._current_frames == ()
    assert dialog.follow_check.isChecked()
    assert dialog.index_label.text() == "0 / 0"
    assert dialog.summary_label.text() == "暂无诊断帧记录"
    assert dialog.preview_label.text() == "暂无画面"
    assert not dialog.btn_clear_cache.isEnabled()

    # 清空后开始落盘：不应写入任何旧帧，仅写入后续新采集的帧
    dialog.btn_dump_toggle.setChecked(True)
    session_dir = dialog._dumper.session_dir
    assert session_dir is not None

    history2 = RecentFrameHistory(
        service.options, {"task_run_id": "run-2"}, on_frame=service._emit_frame
    )
    service._remember(history2)
    _observe_color(history2, 10, (50, 150, 250), "ocr")
    history2.close("completed")

    deadline = time.monotonic() + 3.0
    while dialog._dumper.written_count < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.btn_dump_toggle.setChecked(False)

    lines = (session_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["frame_seq"] for line in lines] == [10]
    dialog.close()


def test_clear_cache_while_recording_keeps_receiving_new_frames(qtbot, tmp_path):
    """任务录制中清空缓存，仅清掉已缓冲帧，不影响当前任务继续写入和落盘新帧。"""
    import json
    import time

    from lvjiang.core.task_diagnostics import DiagnosticOptions, RecentFrameHistory

    service = TaskDiagnostics()
    service.options = DiagnosticOptions(enabled=True, max_frames=5, max_bytes=10_000_000)
    history = RecentFrameHistory(
        service.options, {"task_run_id": "live-run"}, on_frame=service._emit_frame
    )
    service._remember(history)

    dialog = TaskDiagnosticsDialog(service, dump_base_dir=tmp_path / "logs" / "diagnostics")
    qtbot.addWidget(dialog)

    _observe_color(history, 1, (10, 20, 240), "ocr")
    _observe_color(history, 2, (10, 240, 20), "template")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()
    assert dialog.index_label.text() == "2 / 2"

    # 录制中清空缓存
    dialog.btn_clear_cache.click()
    assert service.latest is history
    assert history.frames() == ()
    assert history.snapshot()["retained_bytes"] == 0
    assert dialog.index_label.text() == "0 / 0"
    assert not dialog.btn_clear_cache.isEnabled()

    # 开启落盘并继续采集新帧 #3
    dialog.btn_dump_toggle.setChecked(True)
    session_dir = dialog._dumper.session_dir
    assert session_dir is not None

    _observe_color(history, 3, (200, 100, 50), "ocr")
    deadline = time.monotonic() + 3.0
    while len(history.frames()) < 1 and time.monotonic() < deadline:
        time.sleep(0.02)
    dialog.refresh_frames()
    assert dialog.index_label.text() == "1 / 1"
    assert "#3" in dialog.meta_label.text()
    assert dialog.btn_clear_cache.isEnabled()

    history.close("completed")
    dialog.btn_dump_toggle.setChecked(False)
    lines = (session_dir / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["frame_seq"] for line in lines] == [3]
    dialog.close()
