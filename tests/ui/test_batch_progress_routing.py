"""批量执行：勾选状态补齐 与 进度表定位。

这两处过去都靠「界面一套算法、执行另一套算法」隐式对齐，一旦两边算不出
同一个结果就静默跑偏——行显示打勾却被跳过、状态刷到别人的行上。
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.core.config.models import UserConfig
from lvjiang.ui.batch.batch_runner import ST_PENDING, ST_SUCCESS, BatchScript
from lvjiang.ui.batch.batch_tab import BatchTab


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    is_running = False

    def __init__(self):
        super().__init__()
        self._user_config = UserConfig()

    def append_log(self, _message: str) -> None:
        pass


def _make_tab(qtbot, monkeypatch, config: BatchConfigItem, enabled=None):
    cfg = BatchConfig(active_config=config.name, configs={config.name: config})
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: cfg
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _c: None
    )
    monkeypatch.setattr(
        "lvjiang.core.batch_config.load_enabled_rows",
        lambda: ({config.name: list(enabled)} if enabled is not None else {}),
    )
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", list
    )
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    return tab


def _rows(count: int) -> list[dict]:
    return [{"role": f"acct{i}"} for i in range(count)]


def test_short_enabled_array_keeps_ui_and_execution_in_step(qtbot, monkeypatch):
    """存量 enabled 数组比行数短时，界面勾选与实际执行必须一致。"""
    config = BatchConfigItem(
        name="demo", user_column="role", columns=["role"], rows=_rows(5)
    )
    # 「批量配置」加过行，但 enabled_rows 只记了前 3 行。
    tab = _make_tab(qtbot, monkeypatch, config, enabled=[True, False, True])

    visible = [cb.isChecked() for cb, _ in tab._entry_checkboxes]
    executed = [row["role"] for _idx, row in tab._get_enabled_rows()]

    assert visible == [True, False, True, True, True]
    # 补齐位视为启用：界面打勾的行，执行时一行都不能少。
    assert executed == ["acct0", "acct2", "acct3", "acct4"]
    assert [
        role
        for role, on in zip(
            [r["role"] for r in config.rows], visible, strict=True
        )
        if on
    ] == executed


def test_progress_routes_by_index_not_by_label_text(qtbot, monkeypatch):
    """同名条目也必须各刷各的行，不能第一行吃掉后面所有状态。"""
    config = BatchConfigItem(
        name="demo", user_column="role", columns=["role"],
        # 两行同名：user_column 指向了非唯一列。
        rows=[{"role": "同名"}, {"role": "同名"}],
    )
    tab = _make_tab(qtbot, monkeypatch, config)
    scripts = [BatchScript(id="a", name="脚本A", wf_file="a.wf")]
    enabled_rows = tab._get_enabled_rows()
    tab._build_progress_table(enabled_rows, config, scripts)

    assert tab._progress_table.rowCount() == 2

    # 第二个条目完成，第一个还没跑。
    tab.update_progress(1, "同名", "a", ST_SUCCESS)

    assert tab._progress_table.item(0, 2).text() == ST_PENDING
    assert tab._progress_table.item(1, 2).text() == ST_SUCCESS


def test_progress_routes_when_label_text_differs(qtbot, monkeypatch):
    """执行侧标签算法与进度表不一致时，状态仍要落到正确的行。"""
    config = BatchConfigItem(
        name="demo", user_column="role", columns=["role"],
        rows=[{"role": ""}],  # user_column 为空：两边算出的标签不同
    )
    tab = _make_tab(qtbot, monkeypatch, config)
    scripts = [BatchScript(id="a", name="脚本A", wf_file="a.wf")]
    tab._build_progress_table(tab._get_enabled_rows(), config, scripts)

    # 进度表这行的标签是 ""，worker 发来的却是它自己的兜底文案。
    assert tab._progress_table.item(0, 0).text() == ""
    tab.update_progress(0, "(行 0)", "a", ST_SUCCESS)

    assert tab._progress_table.item(0, 2).text() == ST_SUCCESS


def test_unknown_progress_key_is_ignored_not_misrouted(qtbot, monkeypatch):
    config = BatchConfigItem(
        name="demo", user_column="role", columns=["role"], rows=_rows(1)
    )
    tab = _make_tab(qtbot, monkeypatch, config)
    scripts = [BatchScript(id="a", name="脚本A", wf_file="a.wf")]
    tab._build_progress_table(tab._get_enabled_rows(), config, scripts)

    tab.update_progress(9, "acct0", "nope", ST_SUCCESS)

    assert tab._progress_table.item(0, 2).text() == ST_PENDING
