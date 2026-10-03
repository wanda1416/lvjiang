"""调律管理按运行实例隔离进度页。"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.core.tuning_history.repository import (
    TuningHistoryRepository,
)
from lvjiang.apps.yysls.ui.tuning.management_widget import (
    TuningManagementWidget,
)
from lvjiang.apps.yysls.ui.tuning.progress_hub import TuningProgressHub

pytestmark = pytest.mark.usefixtures("qapp")


def test_running_pages_are_isolated_and_only_terminal_page_can_close(tmp_path):
    widget = TuningManagementWidget(
        history_repository=TuningHistoryRepository(tmp_path / "history.db"))
    first = widget.register_run("run-a", TuningProgressHub(), title="设备 A")
    second = widget.register_run("run-b", TuningProgressHub(), title="设备 B")

    widget.set_run_paused("run-a", True)
    assert widget._pages == {"run-a": first, "run-b": second}
    assert widget._tabs.count() == 3

    widget._close_page_widget(first)
    assert widget._tabs.count() == 3

    widget.mark_run_done("run-a", "completed")
    widget._close_page_widget(first)
    assert widget._tabs.count() == 2
    assert "run-a" not in widget._pages
    assert "run-b" in widget._pages
