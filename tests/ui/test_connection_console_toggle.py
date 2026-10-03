"""主窗口连接控制台的折叠状态契约。"""

from types import SimpleNamespace

from lvjiang.ui.main.window import MainWindow


class _Console:
    def __init__(self) -> None:
        self.hidden = False

    def isHidden(self) -> bool:  # noqa: N802 - Qt API shape
        return self.hidden

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt API shape
        self.hidden = not visible


class _Button:
    def __init__(self) -> None:
        self.text = "隐藏连接控制台"

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API shape
        self.text = text


def test_connection_console_defaults_visible_and_toggles_without_rebuilding(
    monkeypatch,
) -> None:
    """折叠只改变容器可见性，再次展开仍复用同一个控制台。"""
    monkeypatch.setattr("lvjiang.ui.main.window.tr", lambda text: text)
    console = _Console()
    host = SimpleNamespace(
        connection_console=console,
        btn_toggle_connection_console=_Button(),
    )

    MainWindow._on_toggle_connection_console(host)
    assert console.hidden is True
    assert host.btn_toggle_connection_console.text == "显示连接控制台"

    MainWindow._on_toggle_connection_console(host)
    assert console.hidden is False
    assert host.btn_toggle_connection_console.text == "隐藏连接控制台"
