from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox, QTextEdit

from lvjiang.ui.main.window import MainWindow


def test_log_append_buffers_before_log_widget_exists():
    host = SimpleNamespace(_log_buffer=[], _log_min_level=20)

    MainWindow._log_append(host, "[批量] 配置中的脚本当前不可用")

    assert len(host._log_buffer) == 1
    assert host._log_buffer[0][1] == "[批量] 配置中的脚本当前不可用"


def test_clear_log_removes_hidden_entries_and_new_logs_still_arrive(qtbot):
    log_text = QTextEdit()
    qtbot.addWidget(log_text)
    level_combo = QComboBox()
    qtbot.addWidget(level_combo)
    level_combo.addItem("DEBUG", 10)
    level_combo.addItem("INFO", 20)
    host = SimpleNamespace(
        _log_buffer=[], _log_min_level=20,
        log_text=log_text, _log_level_combo=level_combo,
    )
    MainWindow._log_append(host, "[DEBUG] 隐藏记录")
    MainWindow._log_append(host, "[INFO] 可见记录")

    MainWindow._clear_log(host)
    level_combo.setCurrentIndex(0)
    MainWindow._on_log_level_changed(host)

    assert host._log_buffer == []
    assert log_text.toPlainText() == ""
    MainWindow._log_append(host, "[INFO] 新记录")
    assert log_text.toPlainText() == "[INFO] 新记录"
