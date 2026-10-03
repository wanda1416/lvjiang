"""主窗口预览按钮的展开状态语义。"""

from types import SimpleNamespace

from lvjiang.ui.main.menu_ops import MenuOpsMixin


class _VisibilityTarget:
    def __init__(self) -> None:
        self.visible = False

    def setVisible(self, visible: bool) -> None:  # noqa: N802 - Qt API shape
        self.visible = visible


class _TextTarget:
    def __init__(self) -> None:
        self.text = ""

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API shape
        self.text = text


def test_preview_button_checked_state_means_expanded() -> None:
    host = SimpleNamespace(
        preview_container=_VisibilityTarget(),
        btn_hide_window=_TextTarget(),
    )

    MenuOpsMixin._on_toggle_preview(host, True)
    assert host.preview_container.visible is True
    assert host.btn_hide_window.text == "隐藏预览"

    MenuOpsMixin._on_toggle_preview(host, False)
    assert host.preview_container.visible is False
    assert host.btn_hide_window.text == "显示预览"
