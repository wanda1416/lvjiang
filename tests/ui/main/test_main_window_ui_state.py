"""主窗口界面状态持久化：窗口几何与连接分栏。

保护的真实回归：以前只存 ``window_size``（宽高数字），最大化关闭后下次
启动会还原成一个同尺寸的**普通**窗口；也没有保存连接控制台分栏高度。
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMainWindow, QTabWidget, QWidget

from lvjiang.ui.main.ui_state import UiStateMixin


class _RecordingSplitter:
    """记录 setSizes 调用，避免依赖真实分栏的像素分配。"""

    def __init__(self) -> None:
        self.sizes_value = [0, 0]
        self.calls: list[list[int]] = []

    def setSizes(self, values) -> None:  # noqa: N802 - Qt API
        self.calls.append([int(v) for v in values])
        self.sizes_value = [int(v) for v in values]

    def sizes(self):  # noqa: N802 - Qt API
        return list(self.sizes_value)


class _Host(QMainWindow, UiStateMixin):
    def __init__(self) -> None:
        super().__init__()
        self._main_splitter = _RecordingSplitter()
        self._connection_splitter = _RecordingSplitter()
        self._left_tabs = QTabWidget()
        self._left_tabs.addTab(QWidget(), "a")
        self._left_tabs.addTab(QWidget(), "b")
        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "x")
        self.tabs.addTab(QWidget(), "y")


@pytest.fixture
def isolated_page(tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.core.config.interface import reset_interface_store
    from lvjiang.core.config.session import reset_session_store

    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    reset_interface_store()
    yield
    reset_session_store()
    reset_interface_store()


def _make_host():
    host = _Host()
    host.resize(1200, 800)
    host.show()
    return host


def test_maximized_window_restores_maximized(isolated_page, qtbot):
    """核心契约：最大化关闭后重启还是最大化，而不是同尺寸的普通窗口。"""
    first = _make_host()
    first.showMaximized()
    first._save_ui_state()
    first.close()

    second = _make_host()
    second._restore_ui_state()
    assert second.isMaximized()


def test_connection_splitter_sizes_are_saved_and_applied(isolated_page, qtbot):
    first = _make_host()
    first._connection_splitter.setSizes([180, 620])
    first._main_splitter.setSizes([300, 900])
    first._save_ui_state()
    first.close()

    second = _make_host()
    second._restore_ui_state()
    assert second._main_splitter.calls[-1] == [300, 900]
    assert second._connection_splitter.calls[-1] == [180, 620]


def test_window_size_is_fallback_when_geometry_missing(isolated_page, qtbot):
    """旧配置（只有 window_size）仍能还原出接近的窗口大小。"""
    from lvjiang.core.config import update_ui_page_state

    update_ui_page_state("main_page", {"window_size": [1100, 720]})
    host = _make_host()
    host._restore_ui_state()
    assert (host.width(), host.height()) == (1100, 720)


def test_broken_geometry_falls_back_to_window_size(isolated_page, qtbot):
    from lvjiang.core.config import update_ui_page_state

    update_ui_page_state("main_page", {
        "window_geometry": "!!!not-base64!!!",
        "window_size": [1100, 720],
    })
    host = _make_host()
    host._restore_ui_state()
    assert (host.width(), host.height()) == (1100, 720)
