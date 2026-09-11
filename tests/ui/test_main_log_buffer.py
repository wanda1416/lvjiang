from types import SimpleNamespace

from lvjiang.ui.main.window import MainWindow


def test_log_append_buffers_before_log_widget_exists():
    host = SimpleNamespace(_log_buffer=[], _log_min_level=20)

    MainWindow._log_append(host, "[批量] 配置中的脚本当前不可用")

    assert len(host._log_buffer) == 1
    assert host._log_buffer[0][1] == "[批量] 配置中的脚本当前不可用"
